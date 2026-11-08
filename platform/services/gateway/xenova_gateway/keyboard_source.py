"""Pi 직결 evdev K: 원 표본·키 edge와 sticky fence만 Gateway에 전달한다.

foreground process/thread 하나이며 MQTT·Authority·serial 출력은 소유하지 않는다.
Linux 4.9 input.h/asm-generic ioctl의 LP64 ABI만 지원한다. RF 생존은 evdev의
repeat/bitmap/정상 drain으로 증명하지 않으며 설치된 독립 수락 profile이 필요하다.
UAPI: https://github.com/torvalds/linux/blob/v4.9/include/uapi/linux/input.h
Event semantics: https://www.kernel.org/doc/html/v4.12/input/event-codes.html
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import errno
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import select
import selectors
import signal
import socket
import stat
import struct
import sys
import time
from uuid import uuid4

from . import keyboard_contract as contract
from .authority_journal import canonical
from .client import trusted_endpoint
from .ipc_contract import decode as json_decode, new_request_id, HEADER
from .operator_client import OperatorClient

MAX_COUNTER = (1 << 63) - 1
LEASE_US = 150000
PERIOD_US = 50000
WORK_US = 20000
EVENT_CAP = 256
BACKLOG_US = 100000
CONTROL_US = 5000000
EV_SYN, EV_KEY, SYN_REPORT, SYN_DROPPED = 0, 1, 0, 3
W, S, A, D, SPACE, ESC, ENTER, MODIFIER = (1 << n for n in range(8))
RELEASE, REARM, DETACH, GLOBAL_STOP = 1, 2, 4, 8
KEY_BITS = {17: W, 31: S, 30: A, 32: D, 57: SPACE, 1: ESC, 28: ENTER, 96: ENTER,
            29: MODIFIER, 97: MODIFIER, 42: MODIFIER, 54: MODIFIER,
            56: MODIFIER, 100: MODIFIER, 125: MODIFIER, 126: MODIFIER}
EVENT = struct.Struct('@llHHi')


class KeyboardError(RuntimeError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def now_us():
    return time.monotonic_ns() // 1000


def _increment(value):
    if value >= MAX_COUNTER:
        raise KeyboardError('PROTOCOL_ERROR')
    return value + 1


def _hash(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


@dataclass(frozen=True, slots=True)
class DeviceProfile:
    alias: str
    device_path: str
    device_identity_sha256: str
    kernel_abi_sha256: str
    profile_sha256: str
    transport: str
    radio_policy: str
    acceptance_sha256: str | None
    max_link_loss_us: int | None

    @property
    def continuous_allowed(self):
        return self.acceptance_sha256 is not None and (
            self.transport == 'WIRED' and self.radio_policy == 'NOT_APPLICABLE'
            or self.transport == 'WIRELESS' and self.radio_policy == 'RECEIVER_RELEASE_INHIBIT'
            and self.max_link_loss_us is not None)

    @property
    def reason(self):
        if self.continuous_allowed:
            return 'OK'
        return 'UNVERIFIED_RADIO' if self.transport == 'WIRELESS' else 'UNSUPPORTED_DEVICE'


def profile_from_fields(value, expected_alias=None):
    """설치 profile의 구조와 수락 참조만 읽는다. 실물/RF 시험을 발급하지 않는다."""
    fields = {'version', 'alias', 'device_path', 'device_identity_sha256', 'kernel_abi_sha256',
              'transport', 'radio_policy', 'acceptance_sha256', 'max_link_loss_us'}
    if (type(value) is not dict or set(value) != fields or type(value['version']) is not int
            or value['version'] != 1 or type(value['alias']) is not str
            or re.fullmatch('[a-z0-9][a-z0-9_-]{0,63}', value['alias']) is None
            or expected_alias is not None and value['alias'] != expected_alias):
        raise ValueError('exact installed keyboard profile required')
    path = value['device_path']
    if (type(path) is not str or not 1 <= len(path) <= 256 or not path.isascii()
            or any(ord(c)<33 for c in path) or not path.startswith('/dev/input/') or '..' in Path(path).parts):
        raise ValueError('explicit input device path required')
    if not _hash(value['device_identity_sha256']) or not _hash(value['kernel_abi_sha256']):
        raise ValueError('installed device/ABI SHA256 required')
    if value['transport'] not in ('WIRED', 'WIRELESS') or value['radio_policy'] not in (
            'NOT_APPLICABLE', 'UNVERIFIED', 'RECEIVER_RELEASE_INHIBIT', 'VISIBLE_LINK_EPOCH'):
        raise ValueError('registered keyboard transport/radio policy required')
    if value['transport'] == 'WIRED' and value['radio_policy'] != 'NOT_APPLICABLE':
        raise ValueError('wired profile is a separate acceptance')
    if value['transport'] == 'WIRELESS' and value['radio_policy'] == 'NOT_APPLICABLE':
        raise ValueError('wireless link evidence required')
    acceptance, loss = value['acceptance_sha256'], value['max_link_loss_us']
    if acceptance is not None and not _hash(acceptance):
        raise ValueError('acceptance reference SHA256 required')
    if loss is not None and (type(loss) is not int or not 1 <= loss <= 500000):
        raise ValueError('bounded original link-loss acceptance required')
    if value['transport'] == 'WIRED' and loss is not None:
        raise ValueError('wired profile has no RF loss bound')
    # VISIBLE_LINK_EPOCH has no actual installed OS adapter in this bundle.
    # Its declared evidence alone cannot manufacture a live link observation.
    return DeviceProfile(value['alias'], path, value['device_identity_sha256'], value['kernel_abi_sha256'],
                         hashlib.sha256(canonical(value)).hexdigest(), value['transport'], value['radio_policy'],
                         acceptance, loss)


def load_device_profile(path, expected_alias=None, uid=0):
    """읽기만 하는 regular/root-owned≤8KiB profile. Gateway도 같은 loader를 쓴다."""
    path = Path(path)
    if not path.is_absolute() or path.parent.resolve() != path.parent:
        raise ValueError('canonical installed profile directory required')
    parent = path.parent.stat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != uid or parent.st_mode & 0o022:
        raise ValueError('protected installed profile directory required')
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != uid or before.st_mode & 0o022
                or before.st_nlink != 1 or not 0 < before.st_size <= 8192):
            raise ValueError('bounded installed regular profile required')
        raw = bytearray()
        while len(raw) <= 8192:
            part = os.read(fd, 8193-len(raw))
            if not part:
                break
            raw.extend(part)
        after = os.fstat(fd)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if len(raw) > 8192 or identity(before) != identity(after) or len(raw) != before.st_size:
            raise ValueError('profile changed during bounded read')
        return profile_from_fields(json_decode(bytes(raw)), expected_alias)
    finally:
        os.close(fd)


def _ioc(direction, number, length):
    return (direction << 30) | (length << 16) | (ord('E') << 8) | number


def abi_descriptor():
    """Linux LP64 native timeval/evdev ABI. 장치가 clock 설정을 거절하면 미지원이다."""
    if (sys.platform != 'linux' or platform.machine() not in ('aarch64', 'x86_64')
            or struct.calcsize('@l') != 8 or EVENT.size != 24):
        raise KeyboardError('UNSUPPORTED_DEVICE')
    return dict(input_event='@llHHi', event_bytes=24, input_id='@HHHH', byteorder=sys.byteorder,
                clock_monotonic=time.CLOCK_MONOTONIC, key_bytes=96,
                eviocgrab=_ioc(1, 0x90, 4), eviocsclockid=_ioc(1, 0xa0, 4),
                eviocgkey=_ioc(2, 0x18, 96), eviocgbit=_ioc(2, 0x21, 96), ev_version=0x010001)


class Evdev:
    """명시한 단일 evdev FD/grab의 소유자. startup/resync에서만 bitmap을 읽는다."""
    def __init__(self, profile):
        import fcntl
        self.fd = None
        self.grabbed = False
        self.ioctl = fcntl.ioctl
        self.abi = abi_descriptor()
        if hashlib.sha256(canonical(self.abi)).hexdigest() != profile.kernel_abi_sha256:
            raise KeyboardError('UNSUPPORTED_DEVICE')
        resolved = Path(profile.device_path).resolve(strict=True)
        if resolved.parent != Path('/dev/input') or re.fullmatch('event[0-9]+', resolved.name) is None:
            raise KeyboardError('UNSUPPORTED_DEVICE')
        try:
            self.fd = os.open(resolved, os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK | os.O_NOFOLLOW)
            info = os.fstat(self.fd)
            if not stat.S_ISCHR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o002:
                raise KeyboardError('UNSUPPORTED_DEVICE')
            self.identity = (info.st_dev, info.st_ino, info.st_rdev)
            self.path = resolved
            def query(number, length):
                data = bytearray(length)
                self.ioctl(self.fd, _ioc(2, number, length), data, True)
                return bytes(data)
            version = struct.unpack('@I', query(1, 4))[0]
            if version != self.abi['ev_version']:
                raise KeyboardError('UNSUPPORTED_DEVICE')
            identifier = query(2, 8)
            supported = query(0x21, 96)
            if any(not supported[key//8] & (1 << (key % 8)) for key in (17, 31, 30, 32, 28, 57, 1)):
                raise KeyboardError('UNSUPPORTED_DEVICE')
            identity = dict(input_id_hex=identifier.hex(), version=version, key_bits_hex=supported.hex(),
                            name_hex=query(6, 256).split(b'\0', 1)[0].hex(),
                            phys_hex=query(7, 256).split(b'\0', 1)[0].hex(),
                            uniq_hex=query(8, 256).split(b'\0', 1)[0].hex())
            if hashlib.sha256(canonical(identity)).hexdigest() != profile.device_identity_sha256:
                raise KeyboardError('UNSUPPORTED_DEVICE')
            self.ioctl(self.fd, self.abi['eviocsclockid'], struct.pack('@i', time.CLOCK_MONOTONIC))
            self.ioctl(self.fd, self.abi['eviocgrab'], 1)
            self.grabbed = True
        except BaseException:
            self.close()
            raise

    def keys(self):
        data = bytearray(96)
        self.ioctl(self.fd, self.abi['eviocgkey'], data, True)
        return {code for code in KEY_BITS if data[code//8] & (1 << (code % 8))}

    def check_identity(self):
        actual = self.path.stat()
        if (actual.st_dev, actual.st_ino, actual.st_rdev) != self.identity:
            raise KeyboardError('DEVICE_LOST')

    def close(self):
        if self.fd is not None:
            try:
                if self.grabbed:
                    self.ioctl(self.fd, self.abi['eviocgrab'], 0)
            finally:
                os.close(self.fd)
                self.fd = None
                self.grabbed = False


@dataclass(frozen=True, slots=True)
class InputEvent:
    time_us: int
    kind: int
    code: int
    value: int


def decode_events(raw):
    if type(raw) is not bytes or not raw or len(raw) % EVENT.size:
        raise KeyboardError('PROTOCOL_ERROR')
    if len(raw) // EVENT.size > EVENT_CAP:
        raise KeyboardError('INPUT_BACKLOG')
    result = []
    for second, micro, kind, code, value in EVENT.iter_unpack(raw):
        if second < 0 or not 0 <= micro < 1000000 or second > (MAX_COUNTER-micro)//1000000:
            raise KeyboardError('PROTOCOL_ERROR')
        if kind == EV_KEY and (code > 0x2ff or value not in (0, 1, 2)):
            raise KeyboardError('PROTOCOL_ERROR')
        result.append(InputEvent(second*1000000+micro, kind, code, value))
    return tuple(result)


@dataclass(frozen=True, slots=True)
class Frame:
    sample: int
    key_event: int
    t1: int
    t2: int
    mask: int
    last_event: int | None


class KeyState:
    """완전 SYN frame과 실제 edge만 추적한다. RF/Authority 허가를 만들지 않는다."""
    def __init__(self):
        self.pressed = set()
        self.sample = self.key_event = 0
        self.last_event = self.last_native = None
        self.packet_open = self.dropping = self.need_resync = False
        self.fence_mask = 0
        self.fence_reason = 'OK'
        self.blocked = self.require_rearm = True
        self.armed = False
        self.neutral_samples = 0
        self.arm_event = None
        self.early_keys = set()
        self.arm_waiting = False
        self.waiting_stop = False

    @property
    def mask(self):
        result = 0
        for code in self.pressed:
            result |= KEY_BITS[code]
        return result

    def fence(self, mask, reason):
        self.fence_mask |= mask
        if reason == 'GLOBAL_STOP' or self.fence_reason != 'GLOBAL_STOP':
            self.fence_reason = reason
        self.blocked = True
        self.waiting_stop = True
        self.arm_event = None
        self.neutral_samples = 0
        if mask & (REARM | DETACH | GLOBAL_STOP):
            self.require_rearm = True
            self.armed = False

    def resync(self, pressed):
        self.pressed = set(pressed) & KEY_BITS.keys()
        self.early_keys = self.pressed & {17, 31}
        self.packet_open = self.dropping = self.need_resync = False
        self.armed = False
        self.require_rearm = self.blocked = True
        self.neutral_samples = 0
        self.arm_event = None

    def process(self, event, observed_us):
        if (event.time_us > observed_us or observed_us-event.time_us > BACKLOG_US
                or self.last_native is not None and event.time_us < self.last_native):
            raise KeyboardError('INPUT_BACKLOG')
        self.last_native = event.time_us
        if event.kind == EV_SYN and event.code == SYN_DROPPED:
            self.fence(REARM, 'SYN_DROPPED')
            self.dropping = True
            self.packet_open = False
            return
        if self.dropping:
            if event.kind == EV_SYN and event.code == SYN_REPORT:
                self.dropping = False
                self.need_resync = True
            return
        if self.need_resync:
            return
        if event.kind == EV_SYN and event.code == SYN_REPORT:
            self.packet_open = False
            return
        self.packet_open = True
        if event.kind != EV_KEY or event.code not in KEY_BITS or event.value == 2:
            return
        before = self.mask
        down = event.value == 1
        if down == (event.code in self.pressed):
            return
        was_early = event.code in self.early_keys
        if down:
            self.pressed.add(event.code)
        else:
            self.pressed.discard(event.code)
            self.early_keys.discard(event.code)
        self.key_event = _increment(self.key_event)
        self.last_event = event.time_us
        mask = self.mask
        if mask & ~ENTER:
            self.neutral_samples = 0
        if not down and KEY_BITS[event.code] & (W | S) and not was_early:
            self.fence(RELEASE, 'OK')
        if mask & MODIFIER:
            self.fence(REARM, 'MODIFIER')
        if mask & (W | S) == W | S or mask & (A | D) == A | D:
            self.fence(REARM, 'KEY_CONFLICT')
        if down and KEY_BITS[event.code] == SPACE:
            self.fence(REARM | GLOBAL_STOP, 'GLOBAL_STOP')
        if down and KEY_BITS[event.code] == ESC:
            self.fence(RELEASE | DETACH, 'DETACHED')
        if down and KEY_BITS[event.code] & (W | S) and (self.blocked or not self.armed):
            self.early_keys.add(event.code)
        if mask & ~ENTER and (self.arm_waiting or self.arm_event is not None):
            self.fence(REARM, 'STOP_REQUIRED')
        if (down and KEY_BITS[event.code] == ENTER and not before & ~ENTER
                and not mask & ~ENTER and self.neutral_samples >= 2 and not self.fence_mask
                and not self.waiting_stop):
            self.arm_event = self.key_event

    def snapshot(self, t1, t2):
        if (not 0 <= t1 <= t2 <= MAX_COUNTER or t2-t1 > WORK_US or t1 > MAX_COUNTER-LEASE_US):
            raise KeyboardError('INPUT_BACKLOG')
        if self.packet_open or self.need_resync or self.dropping:
            raise KeyboardError('PROTOCOL_ERROR')
        self.sample = _increment(self.sample)
        return Frame(self.sample, self.key_event, t1, t2, self.mask, self.last_event)

    def frame_accepted(self, frame):
        if not self.waiting_stop and not frame.mask & ~ENTER and not self.mask & ~ENTER:
            self.neutral_samples = min(2, self.neutral_samples+1)

    def stop_ready(self):
        self.waiting_stop = False
        self.blocked = self.require_rearm

    def arm_accepted(self):
        if self.fence_mask or self.early_keys or self.mask & ~ENTER:
            return False
        self.armed = True
        self.blocked = self.require_rearm = False
        self.arm_event = None
        return True


@dataclass(frozen=True, slots=True)
class Pending:
    kind: str
    sequence: int
    request: str
    issued: int
    expires: int
    raw: bytes
    frame: Frame | None = None


class KeyboardSession:
    """K 한 채널의 ordinary1/latest1/fence1. retry는 원 bytes/E만 재사용한다."""
    def __init__(self, profile, view, clock=now_us):
        self.profile, self.clock = profile, clock
        self.view = {name: view[name] for name in ('host_boot_id', 'gateway_instance_id', 'generation', 'owner')}
        self.source, self.channel = str(uuid4()), str(uuid4())
        self.device, self.link = str(uuid4()), str(uuid4())
        self.sequence = self.received_sequence = self.fence_revision = 0
        self.last_reply = None
        self.binding = None
        self.state = KeyState()
        self.ordinary = self.latest = self.fence = self.acknowledged = None
        self.sent_ordinary = self.sent_fence = 0
        self.pending_mask = 0
        self.pending_reason = 'OK'
        self.wait_stop = False
        self.closing = self.done = False
        self.closing_until = None
        self.result = 'OK'
        self.start_until = self.clock()+CONTROL_US
        self._ordinary('HELLO', dict(device_profile=profile.alias,
            device_identity_sha256=profile.device_identity_sha256, kernel_abi_sha256=profile.kernel_abi_sha256))

    def _packet(self, kind, payload, issued=None, expires=None, frame=None):
        issued = self.clock() if issued is None else issued
        expires = min(issued+CONTROL_US, self.start_until) if expires is None and self.binding is None else (
            issued+CONTROL_US if expires is None else expires)
        if not 0 <= issued < expires <= MAX_COUNTER:
            raise KeyboardError('INPUT_EXPIRED')
        self.sequence = _increment(self.sequence)
        request = str(uuid4())
        body = dict(version=1, kind=kind, host_boot_id=self.view['host_boot_id'],
                    gateway_instance_id=self.view['gateway_instance_id'], source_instance=self.source,
                    channel_epoch=self.channel, sequence=str(self.sequence), request_id=request,
                    issued_pi_us=str(issued), expires_pi_us=str(expires), payload=payload)
        contract.validate(body)
        return Pending(kind, self.sequence, request, issued, expires, canonical(body), frame)

    def _ordinary(self, kind, payload, **kw):
        if self.ordinary is not None:
            raise KeyboardError('PROTOCOL_ERROR')
        self.ordinary = self._packet(kind, payload, **kw)
        self.sent_ordinary = 0

    def _bound(self):
        sid, epoch, grant, _ = self.binding
        return dict(driver_session=sid, input_epoch=epoch, grant_revision=str(grant))

    def close_intent(self, reason='DETACHED', global_stop=False):
        self.state.fence(RELEASE | DETACH | (GLOBAL_STOP | REARM if global_stop else 0), reason)
        self.closing = True
        self.closing_until = self.closing_until or self.clock()+CONTROL_US
        if reason not in ('DETACHED', 'OK'):
            self.result = reason

    def source_fault(self, reason, terminal=False):
        self.state.fence(REARM | (RELEASE | DETACH if terminal else 0), reason)
        if terminal:
            self.close_intent(reason)

    def offer(self, frame):
        # A frame describes actual raw keys, never a synthetic all-up bitmap.
        if self.binding is not None and not self.closing:
            self.latest = frame

    def _collect_fence(self):
        if not self.state.fence_mask:
            return
        mask, reason = self.state.fence_mask, self.state.fence_reason
        self.state.fence_mask, self.state.fence_reason = 0, 'OK'
        self.latest = None
        if self.binding is None:
            if mask & (DETACH | GLOBAL_STOP):
                self.close_intent(reason, bool(mask & GLOBAL_STOP))
            else:
                # No K driver/lease exists yet. The actual ATTACH performs the
                # old-source STOP before GRANT; never fabricate its fence key.
                self.state.waiting_stop = False
            return
        active = (self.fence or self.acknowledged) if self.wait_stop else self.fence
        covered = 0 if active is None else json_decode(active.raw)['payload']['reason_mask']
        self.pending_mask |= mask & ~covered
        if reason == 'GLOBAL_STOP' or self.pending_reason != 'GLOBAL_STOP':
            self.pending_reason = reason
        if mask & (DETACH | GLOBAL_STOP):
            self.closing = True
            self.closing_until = self.closing_until or self.clock()+CONTROL_US

    def _make_fence(self):
        if self.fence is not None or not self.pending_mask or self.binding is None:
            return
        covered = 0
        if self.wait_stop and self.acknowledged is not None:
            covered = json_decode(self.acknowledged.raw)['payload']['reason_mask']
        self.fence_revision = _increment(self.fence_revision)
        payload = dict(**self._bound(), fence_revision=str(self.fence_revision),
                       key_event_seq=str(self.state.key_event), reason_mask=covered | self.pending_mask,
                       reason=self.pending_reason)
        self.fence = self._packet('FENCE', payload)
        self.sent_fence = 0
        self.pending_mask = 0
        self.pending_reason = 'OK'
        self.wait_stop = True

    def outgoing(self, now):
        self._collect_fence()
        if self.closing_until is not None and now >= self.closing_until:
            self.done = True
            self.result = 'UNCONFIRMED'
            return None
        if self.binding is None and now >= self.start_until:
            self.done = True
            self.result = 'UNCONFIRMED'
            return None
        if self.ordinary is not None and now >= self.ordinary.expires and not self.closing:
            self.close_intent('INPUT_EXPIRED')
            self._collect_fence()
        self._make_fence()
        if self.fence is not None:
            if now >= self.fence.expires:
                self.done = True
                self.result = 'UNCONFIRMED'
                return None
            return self.fence if not self.sent_fence or now-self.sent_fence >= PERIOD_US else None
        if self.wait_stop or self.pending_mask:
            return None
        if self.closing:
            if self.binding is None:
                self.done = True
            elif self.ordinary is None and self.acknowledged is not None:
                self._ordinary('DETACH', dict(driver_session=self.binding[0], input_epoch=self.binding[1],
                    final_fence_revision=str(self.fence_revision), reason='DETACHED'))
        elif self.binding is not None and self.ordinary is None and self.latest is not None:
            frame, self.latest = self.latest, None
            if not 0 <= frame.t1 <= frame.t2 <= now or frame.t2-frame.t1 > WORK_US:
                raise KeyboardError('PROTOCOL_ERROR')
            if frame.t1+LEASE_US <= now:
                self.close_intent('INPUT_EXPIRED')
                return self.outgoing(now)
            if self.state.early_keys and frame.mask & (W | S):
                return None
            payload = dict(**self._bound(), device_instance=self.device, link_epoch=self.link,
                source_sample_seq=str(frame.sample), key_event_seq=str(frame.key_event),
                t1_pi_us=str(frame.t1), t2_pi_us=str(frame.t2), key_mask=frame.mask,
                last_event_pi_us=None if frame.last_event is None else str(frame.last_event))
            self._ordinary('FRAME', payload, issued=frame.t1, expires=frame.t1+LEASE_US, frame=frame)
        if self.ordinary is not None and now < self.ordinary.expires:
            return self.ordinary if not self.sent_ordinary or now-self.sent_ordinary >= PERIOD_US else None
        return None

    def sent(self, pending, when):
        if pending is self.fence:
            self.sent_fence = when
        elif pending is self.ordinary:
            self.sent_ordinary = when
        else:
            raise KeyboardError('PROTOCOL_ERROR')

    def _covered_by_fence(self, ordinary):
        if ordinary.kind not in ('FRAME', 'ARM'):
            return False
        original = json_decode(ordinary.raw)['payload']
        edge = ordinary.frame.key_event if ordinary.frame is not None else int(original['arm_event_seq'])
        for held in (self.fence if self.sent_fence else None, self.acknowledged):
            if held is None:
                continue
            barrier = json_decode(held.raw)['payload']
            if (all(original[name] == barrier[name] for name in ('driver_session', 'input_epoch', 'grant_revision'))
                    and edge <= int(barrier['key_event_seq'])):
                return True
        return False

    def receive(self, raw, now):
        value = contract.decode(raw)
        if (value['host_boot_id'] != self.view['host_boot_id']
                or value['gateway_instance_id'] != self.view['gateway_instance_id']
                or value['source_instance'] != self.source or value['channel_epoch'] != self.channel
                or int(value['issued_pi_us']) > now or int(value['expires_pi_us']) <= now):
            raise KeyboardError('SOURCE_MISMATCH')
        sequence = int(value['sequence'])
        if sequence < self.received_sequence:
            return
        if sequence == self.received_sequence:
            if self.last_reply == (sequence, raw):
                return
            raise KeyboardError('PROTOCOL_ERROR')
        self.received_sequence, self.last_reply = sequence, (sequence, raw)
        payload, kind = value['payload'], value['kind']
        if kind in ('FENCE_ACK', 'STOP_READY'):
            original = self.fence if kind == 'FENCE_ACK' else self.acknowledged
            if original is None:
                return
            expected = json_decode(original.raw)['payload']
            if int(payload['fence_revision']) < int(expected['fence_revision']):
                return
            if (value['request_id'] != original.request or any(payload[name] != expected[name]
                    for name in ('driver_session', 'input_epoch', 'fence_revision'))):
                raise KeyboardError('SOURCE_MISMATCH')
            if kind == 'FENCE_ACK':
                self.acknowledged, self.fence = original, None
                # A stronger pending mask goes out before waiting for old READY.
                self._collect_fence()
                self._make_fence()
            elif self.fence is None and not self.pending_mask and not self.state.fence_mask:
                if not int(payload['stop_command_sequence']) or not int(payload['stationary_sample_sequence']):
                    raise KeyboardError('PROTOCOL_ERROR')
                self.wait_stop = False
                self.state.stop_ready()
            return
        ordinary = self.ordinary
        if kind == 'GRANT':
            if ordinary is None or ordinary.kind != 'ATTACH' or value['request_id'] != ordinary.request:
                raise KeyboardError('PROTOCOL_ERROR')
            if payload['profile_sha256'] != self.profile.profile_sha256 or not int(payload['grant_revision']):
                raise KeyboardError('SOURCE_MISMATCH')
            self.binding = (payload['driver_session'], payload['input_epoch'], int(payload['grant_revision']), payload['generation'])
            self.ordinary = None
            if payload['state'] != 'REARM_REQUIRED' or payload['reason'] not in ('ATTACHED', 'OK'):
                self.close_intent(payload['reason'])
            return
        if kind != 'STATE':
            raise KeyboardError('PROTOCOL_ERROR')
        asynchronous = payload['reply_to_kind'] is None and payload['reply_to_sequence'] is None and payload['accepted'] is None
        if asynchronous:
            if self.binding is None:
                raise KeyboardError('PROTOCOL_ERROR')
            sid, epoch, grant, generation = self.binding
            if (payload['driver_session'], payload['input_epoch'], int(payload['grant_revision'])) != (sid, epoch, grant):
                self.close_intent('REVOKED')
                return
            if payload['generation'] < generation:
                raise KeyboardError('SOURCE_MISMATCH')
            self.binding = (sid, epoch, grant, payload['generation'])
            if payload['state'] in ('REARM_REQUIRED', 'FAULT', 'DETACHED'):
                self.state.armed = False
                self.state.require_rearm = self.state.blocked = True
            # A normal generation-only same-SID continuation preserves arm.
            return
        if (ordinary is None or value['request_id'] != ordinary.request
                or payload['reply_to_kind'] != ordinary.kind or payload['reply_to_sequence'] != str(ordinary.sequence)
                or type(payload['accepted']) is not bool):
            raise KeyboardError('PROTOCOL_ERROR')
        self.ordinary = None
        self.sent_ordinary = 0
        if not payload['accepted']:
            self.state.arm_waiting = False
            if ordinary.kind in ('HELLO', 'ATTACH'):
                self.result = payload['reason']
                self.done = True
                return
            if payload['reason'] in ('STALE_SAMPLE', 'INPUT_EXPIRED', 'STOP_REQUIRED') and self._covered_by_fence(ordinary):
                return  # This exact response retires only the fenced old request.
            self.source_fault(payload['reason'], payload['reason'] in ('WRONG_MODE', 'SOURCE_MISMATCH', 'REVOKED'))
            return
        if ordinary.kind == 'HELLO':
            if payload['state'] != 'DETACHED' or payload['driver_session'] is not None or payload['input_epoch'] is not None:
                raise KeyboardError('PROTOCOL_ERROR')
            self.start_until = now+CONTROL_US  # The first ATTACH's own cap; retry never renews it.
            self._ordinary('ATTACH', dict(expected_generation=self.view['generation'], expected_owner=self.view['owner'],
                device_instance=self.device, link_epoch=self.link))
        elif ordinary.kind == 'DETACH':
            self.done = True
        elif self.binding is None or payload['driver_session'] != self.binding[0]:
            raise KeyboardError('SOURCE_MISMATCH')
        elif ordinary.kind == 'ARM':
            self.state.arm_waiting = False
            if (payload['state'] != 'ARMED_NEUTRAL' or payload['reason'] != 'ARMED'
                    or int(payload['grant_revision']) <= self.binding[2] or payload['generation'] < self.binding[3]):
                raise KeyboardError('PROTOCOL_ERROR')
            self.binding = (payload['driver_session'], payload['input_epoch'], int(payload['grant_revision']), payload['generation'])
            if self.fence is None and not self.wait_stop and not self.pending_mask:
                self.state.arm_accepted()
            self.latest = None
        elif ordinary.kind == 'FRAME':
            if payload['accepted_state_seq'] is None or not int(payload['accepted_state_seq']):
                raise KeyboardError('PROTOCOL_ERROR')
            if (payload['input_epoch'], int(payload['grant_revision'])) != self.binding[1:3]:
                raise KeyboardError('SOURCE_MISMATCH')
            frame = ordinary.frame
            if frame is not None:
                self.state.frame_accepted(frame)
            if (frame is not None and self.state.arm_event == frame.key_event and frame.mask == ENTER
                    and self.state.mask in (0, ENTER) and self.fence is None and not self.wait_stop
                    and not self.pending_mask and not self.state.fence_mask and frame.last_event is not None
                    and 0 <= now-frame.last_event <= BACKLOG_US and now < frame.t1+LEASE_US):
                self._ordinary('ARM', dict(**self._bound(), source_sample_seq=str(frame.sample),
                    arm_event_seq=str(frame.key_event)), issued=frame.t1, expires=frame.t1+LEASE_US)
                self.state.arm_waiting = True
                self.latest = None


class OperatorStop:
    """ATTACH 결과 전 Space를 기존 operator2로 전달하는 단일 nonblocking 요청.

    K가 권위를 쓰는 것이 아니다. 원 key/E를 보존하고 응답 유실은 UNCONFIRMED다.
    정상 driver가 있으면 keyboard FENCE의 GLOBAL_STOP이 같은 Gateway actor로 간다.
    """
    def __init__(self, endpoint, view, uid, clock=now_us):
        from .operator_contract import request
        trusted_endpoint(endpoint, uid)
        key = new_request_id(view['host_boot_id'])
        issued = int(key.split(':')[2])
        body = dict(version=2, action='stop', request_id=key, host_boot_id=view['host_boot_id'],
                    gateway_instance_id=view['gateway_instance_id'], expected_generation=view['generation'],
                    expected_owner=view['owner'], deadline_pi_ms=issued+5000, args={})
        request(body)
        raw = canonical(body)
        self.wire = HEADER.pack(len(raw))+raw
        self.offset = 0
        self.received = bytearray()
        self.uid, self.clock, self.until = uid, clock, issued*1000+CONTROL_US
        self.done, self.accepted, self.connected = False, False, False
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.socket.setblocking(False)
            result = self.socket.connect_ex(str(endpoint))
            if result not in (0, errno.EINPROGRESS, errno.EAGAIN, errno.EWOULDBLOCK):
                raise KeyboardError('UNCONFIRMED')
        except BaseException:
            self.socket.close()
            raise

    def poll(self):
        if self.done:
            return
        if self.clock() >= self.until:
            self.done = True
            return
        try:
            if not self.connected:
                if not select.select([], [self.socket], [], 0)[1]:
                    return
                error = self.socket.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if error:
                    raise OSError(error, 'operator connect')
                _, uid, _ = struct.unpack('3i', self.socket.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                if uid != self.uid:
                    raise KeyboardError('SOURCE_MISMATCH')
                self.connected = True
            if self.offset < len(self.wire):
                size = self.socket.send(self.wire[self.offset:])
                if not size:
                    raise EOFError()
                self.offset += size
            while self.offset == len(self.wire):
                part = self.socket.recv(8197-len(self.received))
                if not part:
                    raise EOFError()
                self.received.extend(part)
                if len(self.received) < 4:
                    continue
                length = HEADER.unpack(self.received[:4])[0]
                if not 0 < length <= 8192 or len(self.received) > length+4:
                    raise KeyboardError('PROTOCOL_ERROR')
                if len(self.received) == length+4:
                    value = json_decode(bytes(self.received[4:]))
                    self.accepted = value.get('version') == 2 and value.get('ok') is True
                    self.done = True
                    return
        except BlockingIOError:
            return
        except (OSError, EOFError, ValueError, KeyboardError):
            self.done = True

    def close(self):
        self.socket.close()


def drain_device(device, state, clock=now_us):
    """실제 read→EAGAIN 구간. 조용한 held도 새 drain이지만 RF proof는 아니다."""
    t1, count, changed = clock(), 0, False
    while True:
        if clock()-t1 > WORK_US:
            raise KeyboardError('INPUT_BACKLOG')
        try:
            raw = os.read(device.fd, max(1, EVENT_CAP-count)*EVENT.size)
        except BlockingIOError:
            break
        if not raw:
            raise KeyboardError('DEVICE_LOST')
        events = decode_events(raw)
        count += len(events)
        if count > EVENT_CAP:
            raise KeyboardError('INPUT_BACKLOG')
        observed = clock()
        for event in events:
            state.process(event, observed)
        changed = True
        if count == EVENT_CAP:
            try:
                extra = os.read(device.fd, EVENT.size)
            except BlockingIOError:
                break
            if not extra:
                raise KeyboardError('DEVICE_LOST')
            raise KeyboardError('INPUT_BACKLOG')
    if state.need_resync:
        state.resync(device.keys())
    t2 = clock()
    if t2-t1 > WORK_US:
        raise KeyboardError('INPUT_BACKLOG')
    if state.dropping:
        return None, changed
    return state.snapshot(t1, t2), changed


def run_keyboard(profile, view, *, endpoint=contract.ENDPOINT, operator_endpoint='/run/xenova-gateway/operator.sock', uid=0):
    """실제 foreground K. 성공 종료도 물리 정지 수락이나 RF 실측을 뜻하지 않는다."""
    if not profile.continuous_allowed:
        raise KeyboardError(profile.reason)
    host = Path('/proc/sys/kernel/random/boot_id').read_text(encoding='ascii').strip()
    if (view['host_boot_id'] != host or view['mode'] != 'MANUAL' or view['owner'] is None
            or type(view['generation']) is not int or not 1 <= view['generation'] <= 0xffffffff):
        raise KeyboardError('WRONG_MODE')
    trusted_endpoint(endpoint, uid)
    try:
        stdin_fd = sys.stdin.fileno()
        os.fstat(stdin_fd)
    except (AttributeError, OSError, ValueError) as error:
        raise KeyboardError('DETACHED') from error
    device = Evdev(profile)
    stream = None
    selector = None
    stopped = [False]
    handlers = {}
    operator = None
    operator_attempted = False
    connected = False
    detached_on_wire = False
    try:
        selector = selectors.DefaultSelector()
        session = KeyboardSession(profile, view)
        session.state.resync(device.keys())
        next_sample = now_us()
        stream = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        stream.setblocking(False)
        status = stream.connect_ex(str(endpoint))
        if status not in (0, errno.EINPROGRESS, errno.EAGAIN, errno.EWOULDBLOCK):
            raise KeyboardError('LINK_LOST')
        selector.register(stream, selectors.EVENT_READ | selectors.EVENT_WRITE, 'socket')
        selector.register(device.fd, selectors.EVENT_READ, 'device')
        try:
            selector.register(stdin_fd, selectors.EVENT_READ, 'stdin')
        except (AttributeError, OSError, ValueError):
            pass  # A regular management stdin is not keyboard/RF freshness.
        def terminate(number, frame):
            del number, frame
            stopped[0] = True
        for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            handlers[number] = signal.signal(number, terminate)
        print('REARM_REQUIRED: 모든 조작 키를 놓고 Enter를 새로 누르세요.', flush=True)
        while not session.done:
            work_started = now_us()
            if stopped[0]:
                session.close_intent()
                stopped[0] = False
            if not detached_on_wire:
                try:
                    device.check_identity()
                    frame, changed = drain_device(device, session.state)
                    observed = now_us()
                    if frame is not None and (changed or observed >= next_sample):
                        session.offer(frame)
                        if observed >= next_sample:
                            next_sample += ((observed-next_sample)//PERIOD_US+1)*PERIOD_US
                except KeyboardError as error:
                    session.source_fault(error.reason, True)
                except OSError:
                    session.source_fault('DEVICE_LOST', True)
            # Space before a known driver tuple uses the existing operator
            # actor. It never guesses a driver UUID or opens a serial writer.
            if session.binding is None and session.state.fence_mask & GLOBAL_STOP and not operator_attempted:
                operator_attempted = True
                try:
                    operator = OperatorStop(operator_endpoint, view, uid)
                except (OSError, ValueError, KeyboardError):
                    session.result = 'UNCONFIRMED'
            if operator is not None:
                operator.poll()
            if connected:
                try:
                    for _ in range(4):
                        raw, ancillary, flags, _ = stream.recvmsg(2048, 0, socket.MSG_DONTWAIT)
                        if not raw:
                            raise KeyboardError('LINK_LOST')
                        if ancillary or flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
                            raise KeyboardError('PROTOCOL_ERROR')
                        session.receive(raw, now_us())
                except BlockingIOError:
                    pass
                outgoing = session.outgoing(now_us())
                if outgoing is not None and not session.done:
                    try:
                        sent = stream.send(outgoing.raw)
                        if sent != len(outgoing.raw):
                            raise KeyboardError('PROTOCOL_ERROR')
                        session.sent(outgoing, now_us())
                        if outgoing.kind == 'DETACH':
                            detached_on_wire = True
                    except BlockingIOError:
                        selector.modify(stream, selectors.EVENT_READ | selectors.EVENT_WRITE, 'socket')
            else:
                session.outgoing(now_us())
            if now_us()-work_started > WORK_US and not session.closing:
                session.source_fault('INPUT_BACKLOG')
            if session.done and operator is not None and not operator.done:
                session.done = False  # One original operator E; no request/key renewal.
            if session.done:
                break
            wait_us = PERIOD_US if detached_on_wire else min(PERIOD_US, max(0, next_sample-now_us()))
            if session.closing_until is not None:
                wait_us = min(wait_us, max(0, session.closing_until-now_us()))
            for key, mask in selector.select(wait_us/1000000):
                if key.data == 'socket' and mask & selectors.EVENT_WRITE:
                    if not connected:
                        error = stream.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                        if error:
                            raise KeyboardError('LINK_LOST')
                        _, peer_uid, _ = struct.unpack('3i', stream.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                        if peer_uid != uid:
                            raise KeyboardError('SOURCE_MISMATCH')
                        connected = True
                    selector.modify(stream, selectors.EVENT_READ, 'socket')
                elif key.data == 'stdin':
                    if not os.read(key.fd, 256):
                        session.close_intent()
                    # Management characters never become evdev press/release.
        if operator is not None and (not operator.done or not operator.accepted):
            session.result = 'UNCONFIRMED'
        print(session.result, flush=True)
        return 0 if session.result in ('OK', 'DETACHED', 'GLOBAL_STOP') else 1
    except (OSError, ValueError, KeyboardError) as error:
        reason = error.reason if isinstance(error, KeyboardError) else 'UNCONFIRMED'
        print(reason+': 원 입력 lease/독립 Gateway STOP으로 제한됩니다.', file=sys.stderr, flush=True)
        return 1
    finally:
        cleanup_errors = []
        for number, previous in handlers.items():
            try:
                signal.signal(number, previous)
            except (OSError, ValueError) as error:
                cleanup_errors.append(error)
        for owned in (selector, operator, stream, device):
            if owned is not None:
                try:
                    owned.close()
                except OSError as error:
                    cleanup_errors.append(error)
        if cleanup_errors:
            raise KeyboardError('UNCONFIRMED') from cleanup_errors[0]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', default='control-keyboard')
    parser.add_argument('--profile-directory', type=Path, default=Path('/etc/xenova/keyboards'))
    parser.add_argument('--operator-socket', default='/run/xenova-gateway/operator.sock')
    parser.add_argument('--keyboard-socket', default=contract.ENDPOINT)
    args = parser.parse_args(argv)
    if re.fullmatch('[a-z0-9][a-z0-9_-]{0,63}', args.device) is None:
        parser.error('등록된 device alias가 필요합니다.')
    try:
        profile = load_device_profile(args.profile_directory/(args.device+'.json'), args.device)
        if not profile.continuous_allowed:
            print(profile.reason+': HELD 미수락. 명시 manual nudge/neutral을 사용하세요.', file=sys.stderr)
            return 2
        result = OperatorClient(args.operator_socket, wait_ms=1000).call(dict(version=2, action='status'))
        if result.get('version') != 2 or result.get('ok') is not True:
            raise KeyboardError('WRONG_MODE')
        return run_keyboard(profile, result['data'], endpoint=args.keyboard_socket, operator_endpoint=args.operator_socket)
    except (OSError, ValueError, KeyboardError, RuntimeError) as error:
        print(error, file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
