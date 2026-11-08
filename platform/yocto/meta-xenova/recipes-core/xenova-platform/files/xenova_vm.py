#!/usr/bin/python3
"""Linux·Zephyr의 영구 등록·생명주기를 관리한다. 등록, Xen 실행, 앱 준비 상태는 별개다.

명시적 create만 자산을 생성한다. start는 등록 자산을 재사용하며 자동 복구는 하지 않는다.
"""
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

from xenova_runtime import PlatformError, atomic_write, ensure_runtime, operation_lock, sync_directory
from xenova_trace import span, traced
from xenova_preparation import run as request_run, diagnostic as print, ensure_quiet, drain
import xenova_zephyr as zephyr

DATA = Path('/srv/xenova')
RUN = Path('/run/xenova')
ID = re.compile(r'[a-z][a-z0-9-]{0,39}\Z')
UUID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z')
MAC = re.compile(r'02(?::[0-9a-f]{2}){5}\Z')
# xenova-vms.service의 150초 바깥 상한에서 결과 저장·종료 여유를 남긴다.
AUTOSTART_TIMEOUT = 140


def run(args, timeout=15):
    try:
        p = request_run(args, text=True, capture=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PlatformError(f'{args[0]} failed: {exc}') from exc
    if p.returncode:
        raise PlatformError(f'{args!r}: exit {p.returncode}: {p.stderr.strip()}')
    return p.stdout


def storage_check(data=DATA, mountinfo=Path('/proc/self/mountinfo')):
    """정확히 그 경로에 마운트된 쓰기 가능한 ext4와 초기화 표식을 요구한다."""
    found = False
    for line in mountinfo.read_text().splitlines():
        left, right = line.split(' - ', 1)
        fields, fs = left.split(), right.split()
        if fields[4] == str(data):
            if fs[0] != 'ext4' or not fs[1].startswith('/dev/') or 'rw' not in fields[5].split(','):
                raise PlatformError('storage must be a writable block-backed ext4 mount')
            found = True
    if not found:
        raise PlatformError(f'persistent storage is not mounted at {data}; no fallback disk is created')
    if (data / '.xenova-install.json').exists():
        state = json.loads((data / '.first-install-state.json').read_text())
        if state.get('schema') != 1 or state.get('phase') != 'complete':
            raise PlatformError('first installation incomplete; VM start refused')
    marker = json.loads((data / '.xenova-storage.json').read_text())
    if marker.get('schema') != 1 or not UUID.fullmatch(marker.get('uuid', '')):
        raise PlatformError('invalid persistent storage marker')


def regular_asset(directory, filename):
    path = directory / filename
    if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
        raise PlatformError(f'missing, empty or linked VM asset: {path}')
    if path.resolve().parent != directory.resolve():
        raise PlatformError(f'VM asset escapes instance: {path}')
    return path


def guest_kind(record):
    if not isinstance(record, dict):
        raise PlatformError('instance metadata must be an object')
    kind = record.get('guest_kind', 'linux' if record.get('schema') == 1 else None)
    if kind not in ('linux', 'zephyr'):
        raise PlatformError('unsupported guest_kind')
    return kind


def has_network(record):
    return guest_kind(record) == 'linux' or zephyr.networked(record)


def cooperative(record):
    return guest_kind(record) == 'linux' or zephyr.networked(record)


def identity_fields(record):
    return ('id', 'name', 'uuid') + (('hostname', 'mac', 'ipv4') if has_network(record) else (('mac','ipv4') if record.get('ipv4') else ()))


def board_shutdown_policy(record):
    """기존 등록은 Linux=graceful, Zephyr=refuse. 자동 시작은 종료 승인이 아니다."""
    linux = guest_kind(record) == 'linux'
    policy = record.get('board_shutdown_policy', 'graceful' if linux else 'refuse')
    if policy not in (('graceful',) if linux else (('graceful','refuse','force-allowed') if zephyr.networked(record) else ('refuse','force-allowed'))):
        raise PlatformError('invalid board shutdown policy for guest kind')
    return policy


def validate_identity(record):
    kind = guest_kind(record)
    board_shutdown_policy(record)
    for key in ('id', 'name') + (('hostname',) if kind == 'linux' else ()):
        if not ID.fullmatch(record.get(key, '')):
            raise PlatformError(f'invalid instance {key}')
    if not UUID.fullmatch(record.get('uuid', '')) or type(record.get('autostart')) is not bool:
        raise PlatformError('invalid instance UUID/autostart')
    if kind == 'zephyr':
        if record.get('schema') != 2:
            raise PlatformError('Zephyr instance requires schema 2')
        if not record.get('ipv4'):
            if zephyr.networked(record):
                raise PlatformError('network Zephyr profile requires reserved MAC/IP/hostname')
            return
        if not MAC.fullmatch(record.get('mac','')) or not ID.fullmatch(record.get('hostname','')):
            raise PlatformError('invalid retained network reservation')
        ip=record['ipv4']
        if not re.fullmatch(r'192\.168\.77\.(?:[2-9]|[1-9][0-9])',ip):
            raise PlatformError('invalid retained network reservation IP')
        return
    if record.get('schema') != 1:
        raise PlatformError('instance schema/id mismatch')
    if not MAC.fullmatch(record.get('mac', '')):
        raise PlatformError('invalid instance UUID or locally administered MAC')
    ip = record.get('ipv4', '').split('.')
    if ip[:3] != ['192', '168', '77'] or len(ip) != 4 or not ip[3].isdigit() or not 2 <= int(ip[3]) <= 99 or str(int(ip[3])) != ip[3]:
        raise PlatformError('reserved VM IP must be 192.168.77.2..99')
    for key, low, high in [('memory_mib', 128, 8192), ('vcpus', 1, 4)]:
        if type(record.get(key)) is not int or not low <= record[key] <= high:
            raise PlatformError(f'invalid {key}')
    if type(record.get('autostart')) is not bool:
        raise PlatformError('autostart must be boolean')
    for key in ('template', 'kernel_release'):
        if not re.fullmatch(r'[A-Za-z0-9.+_-]{1,100}', record.get(key, '')):
            raise PlatformError(f'invalid {key}')


def validate_record(record, directory, *, allow_seed=False, allow_archive=False):
    validate_identity(record)
    if record.get('identity_state') == 'pending' and not allow_seed:
        raise PlatformError('factory seed identity is pending; VM start refused')
    if not allow_archive and record['id'] != directory.name:
        raise PlatformError('instance directory/id mismatch')
    if guest_kind(record) == 'zephyr':
        regular_asset(directory, 'instance.json')
        return zephyr.validate_instance(record, directory)
    # 디스크 경로는 사용자 문자열 대신 인스턴스 디렉터리에서만 결정한다.
    for asset in ('root.raw', 'Image', 'initrd', 'boot.json', 'instance.json'):
        regular_asset(directory, asset)
    boot = json.loads((directory / 'boot.json').read_text())
    if boot.get('kernel_release') != record['kernel_release'] or boot.get('root_device') != '/dev/xvda':
        raise PlatformError('kernel/initramfs/modules contract mismatch')
    for filename, key in [('Image', 'kernel_sha256'), ('initrd', 'initrd_sha256')]:
        expected = boot.get(key, '')
        if not re.fullmatch(r'[0-9a-f]{64}', expected):
            raise PlatformError('boot asset digest missing')
        actual = run(['sha256sum', str(directory / filename)]).split()[0]
        if actual != expected:
            raise PlatformError(f'boot asset digest mismatch: {filename}')
    return record


def load_records(data, *, validate_assets=True, deadline=None, clock=time.monotonic):
    """공통 신원·경로·디스크 중복 검증. validate_assets는 전체 부팅 자산 검사도 요청한다.

    CLI는 공통 검증 후 실행 대상만 자산을 검사한다. 누락된 비대상 부팅 자산은
    종료/콘솔을 막지 않지만, 연결된 경로나 공유 쓰기 디스크는 항상 거부한다.
    """
    registry = data / 'instances'
    if registry.is_symlink() or not registry.is_dir() or registry.resolve() != registry:
        raise PlatformError('instance registry missing')
    records, identities, disks = {}, set(), set()
    for directory in sorted(registry.iterdir()):
        if deadline is not None and clock()>=deadline:
            from xenova_execution import refuse
            refuse('DEADLINE_EXCEEDED','registry scan budget expired')
        if directory.name.startswith('.'):
            continue
        if directory.is_symlink() or not directory.is_dir() or directory.resolve() != directory:
            raise PlatformError(f'invalid registry entry: {directory}')
        r = json.loads(regular_asset(directory, 'instance.json').read_text())
        validate_identity(r)
        if r['id'] != directory.name:
            raise PlatformError('instance directory/id mismatch')
        if validate_assets:
            validate_record(r, directory)
        for key in identity_fields(r):
            identity = (key, r[key])
            if identity in identities:
                raise PlatformError(f'duplicate VM {key}: {r[key]}')
            identities.add(identity)
        disk = directory / 'root.raw'
        if guest_kind(r) == 'linux' and disk.is_symlink():
            raise PlatformError(f'linked writable disk: {disk}')
        if guest_kind(r) == 'linux' and disk.exists():
            if not disk.is_file():
                raise PlatformError(f'invalid writable disk: {disk}')
            st = disk.stat()
            inode = (st.st_dev, st.st_ino)
            if inode in disks:
                raise PlatformError('same writable disk is registered more than once')
            disks.add(inode)
            template_disk = data / 'templates' / r['template'] / 'root.raw'
            if template_disk.exists() and os.path.samefile(directory / 'root.raw', template_disk):
                raise PlatformError('instance disk aliases immutable template')
        records[r['id']] = r
    return records


def xen_state(flags):
    """xl의 여섯 상태 열. 다섯째 열은 독립 crash flag가 아닌 shutdown reason이다."""
    if flags[5] == 'd':
        return 'dying'
    if flags[3] == 's':
        return 'crashed' if flags[4] == 'c' else 'shutdown'
    for position, state in ((2, 'paused'), (1, 'blocked'), (0, 'running')):
        if flags[position] != '-':
            return state
    return 'unknown'


class DomainIdentityError(PlatformError):
    """검증한 실행 대상의 UUID/name/domid가 달라 TARGET_CHANGED로 분류할 오류."""


class DomainStateError(PlatformError):
    """도메인은 존재하지만 start 성공으로 인정할 수 없는 관측 상태."""
    def __init__(self, record, row):
        self.xen_state = row['xen_state']
        self.xen_state_flags = row['xen_state_flags']
        super().__init__(f"{record['id']}: Xen domain is {self.xen_state} ({self.xen_state_flags}); no automatic recovery")


def started_state(record, row):
    if row['xen_state'] not in ('running', 'blocked'):
        raise DomainStateError(record, row)
    return row['xen_state']


class Platform:
    def __init__(self, data=DATA, runtime=RUN, command=run, sleep=time.sleep, clock=time.monotonic, *, defaults_path=Path("/etc/xenova/defaults.json"),
                 boot_id_reader=None, execution_checkpoint=None, execution_policy_path=None, creation_policy_path=None):
        self.data, self.runtime = Path(data), Path(runtime)
        self.defaults_path = Path(defaults_path)
        self.command, self.sleep, self.clock = command, sleep, clock
        # 주입 runner는 Xen/프로세스 경계를 대역한다. 실제 runner만 systemd scope로 실행한다.
        self.process_isolation = command is run
        self.boot_id_reader = boot_id_reader or (lambda: Path("/proc/sys/kernel/random/boot_id").read_text().strip())
        self.execution_checkpoint = execution_checkpoint or (lambda phase, value: None)
        self.execution_policy_path = Path(execution_policy_path) if execution_policy_path else self.defaults_path.with_name("execution-policy.json")
        self.creation_policy_path = Path(creation_policy_path) if creation_policy_path else self.defaults_path.with_name('creation-policy.json')

    def execution(self):
        from xenova_execution import Execution
        return Execution(self)

    def request_control(self, action, record, *, force=False, idempotency_key=None, deadline=None):
        """Caller holds operation_lock and has verified the mounted registry."""
        return self.execution().control(action, record, force=force, key=idempotency_key, deadline=deadline)


    @traced('platform.registry')
    def records(self, *, validate_assets=True):
        storage_check(self.data)
        return load_records(self.data, validate_assets=validate_assets,
                            deadline=getattr(self,'response_deadline',None),clock=self.clock)

    def templates(self):
        """SD의 불변 템플릿 목록. 전체 root 해시는 실제 생성 직전에 검증한다."""
        directory = self.data / 'templates'
        if directory.is_symlink() or not directory.is_dir():
            raise PlatformError('local template directory missing')
        result = []
        for item in sorted(directory.iterdir()):
            if item.name.startswith('.'):
                continue
            if item.is_symlink() or not item.is_dir():
                raise PlatformError('invalid local template path')
            meta = json.loads(regular_asset(item, 'template.json').read_text())
            if meta.get('id') != item.name or meta.get('source', {}).get('distribution') not in ('ubuntu', 'debian'):
                raise PlatformError('unsupported Linux template adapter; RTOS firmware has a separate catalog')
            root = regular_asset(item, 'root.raw')
            if type(meta.get('size_gib')) is not int or not 4 <= meta['size_gib'] <= 32:
                raise PlatformError('invalid template logical size')
            if root.stat().st_mode & 0o222 or root.stat().st_size != meta['size_gib'] * 1024 ** 3:
                raise PlatformError('template root must be immutable and match its logical size')
            result.append({'id': item.name, 'guest_kind': 'linux', 'capabilities': {'graceful_shutdown': True, 'ssh': True, 'dhcp': True, 'guest_writable_persistent_storage': True}, 'version': meta.get('version'), 'logical_bytes': root.stat().st_size, 'source': meta['source'], 'root_sha256': meta['root_sha256']})
        catalog = self.data / 'rtos-templates'
        if catalog.exists():
            zephyr.plain(catalog)
            for item in sorted(catalog.iterdir()):
                if item.name.startswith('.'):
                    continue
                info = zephyr.bundle_info(item)
                meta = json.loads((item / info['manifest']).read_text())
                if meta.get('kind') != 'zephyr-xen-firmware' or meta.get('id') != item.name:
                    raise PlatformError('invalid Zephyr template catalog entry')
                result.append({'id': item.name, 'guest_kind': 'zephyr', 'version': info['version'],
                               'manifest_sha256': info['revision'], 'capabilities': info.get('capabilities', zephyr.CAPABILITIES),
                               'hardware_profile': info.get('hardware_profile', zephyr.PROFILE), 'logical_bytes': info['logical_bytes']})
        return result

    def create(self, args, records=None):
        """기존 Python 호출 지점도 공통 계약으로 위임한다. records 인자는 신뢰/재사용하지 않는다."""
        from xenova_management import Manager
        spec = {'vm_id':getattr(args,'vm_id',None), 'name':getattr(args,'name',args.id), 'template_id':getattr(args,'template_id',getattr(args,'template',None)),
                'template_version':getattr(args,'template_version',None)}
        for field in ('vcpus','memory_mib'):
            if hasattr(args,field):spec[field]=getattr(args,field)
        if getattr(args,'ipv4',None) is not None:
            spec['ipv4']=args.ipv4
        return Manager(self).call('create',spec,execute=getattr(args,'execute',False),
                                  idempotency_key=getattr(args,'idempotency_key',None))

    def reserve_space(self, additional_bytes, records, *, disk_paths=None):
        """전체 등록 예정 집합의 미할당 증가분과 2GiB 여유를 확인한다. 복원은 복사량 0이다."""
        stats = os.statvfs(self.data)
        free = stats.f_bavail * stats.f_frsize
        growth = 0
        for key, r in records.items():
            if guest_kind(r) == 'linux':
                disk = (disk_paths or {}).get(key, self.data / 'instances' / key / 'root.raw')
                info = disk.stat()
                growth += max(0, info.st_size - info.st_blocks * 512)
        required = additional_bytes + growth + 2 * 1024 ** 3
        if free < required:
            raise PlatformError(f'insufficient storage: need {required} free bytes including existing VM growth and reserve, have {free}')
        return free, required

    def timed_command(self, args, deadline=None):
        kind = ('.'.join(args[:2]) if args[0] in ('xl','systemctl') else args[0])
        with span('external.command', command_kind=kind,
                  remaining_seconds=deadline-self.clock() if deadline is not None else 15):
            return self._timed_command(args, deadline)

    def _timed_command(self, args, deadline=None):
        if deadline is None:
            return self.command(args)
        remaining = deadline - self.clock()
        if remaining <= 0:
            from xenova_execution import refuse
            refuse('DEADLINE_EXCEEDED', 'guest operation deadline exceeded')
        return self.command(args, timeout=min(15, remaining))

    @traced('platform.domains')
    def domains(self, *, deadline=None):
        """고정 Xen의 상세 설정과 짧은 목록의 실행 상태를 domid/name으로 대조한다."""
        rows = json.loads(self.timed_command(['xl', 'list', '-l'], deadline))
        if not isinstance(rows, list):
            raise PlatformError('xl list -l returned unexpected schema')
        short = {}
        for line in self.timed_command(['xl', 'list'], deadline).splitlines()[1:]:
            if not line.strip():
                continue
            fields = line.split()
            if len(fields) < 6 or not fields[1].isdigit() or not re.fullmatch(r'[r-][b-][p-][s-][-rscwS?][d-]', fields[4]):
                raise PlatformError('xl list returned invalid domain identity/state')
            domid = int(fields[1])
            if domid in short:
                raise PlatformError('duplicate live domain ID')
            short[domid] = (fields[0], fields[4])
        ids, identities = set(), set()
        for row in rows:
            if not isinstance(row, dict) or type(row.get('domid')) is not int or row['domid'] < 0 or row['domid'] in ids:
                raise PlatformError('invalid/duplicate detailed domain ID')
            domid = row['domid']
            ids.add(domid)
            if domid == 0:
                continue
            config = row.get('config')
            info = config.get('c_info') if isinstance(config, dict) else None
            if (not isinstance(info, dict) or not isinstance(info.get('uuid'), str)
                    or not UUID.fullmatch(info['uuid']) or not isinstance(info.get('name'), str) or not info['name']):
                raise PlatformError('cannot inspect live domain UUID/name; refusing unsafe action')
            if domid not in short or short[domid][0] != info['name']:
                raise PlatformError('xl domain lists disagree on domain ID/name')
            for key in ('uuid', 'name'):
                identity = (key, info[key])
                if identity in identities:
                    raise PlatformError(f'duplicate live {key}')
                identities.add(identity)
            # Xen 4.21 gentypes.py:get_default_expr는 길이 0인 배열을 생략한다.
            # 설정 조회 실패는 xl_info.c:list_domains_details에서 도메인 전체를 생략한다.
            disks = config.setdefault('disks', [])
            if not isinstance(disks, list) or any(not isinstance(disk, dict) for disk in disks):
                raise PlatformError('cannot inspect live domain disks; refusing unsafe action')
            flags = short[domid][1]
            row['xen_state_flags'] = flags
            row['xen_state'] = xen_state(flags)
        if (set(short) - {0}) != (ids - {0}):
            raise PlatformError('xl omitted live domain configuration; writable-disk safety cannot be checked')
        return rows

    def removal_snapshot(self, deadline):
        """디스크 설정 없이 현재 신원·상태를 읽는다. 제어 사전 검사의 대체는 아니다.

        Xen 4.21 xl_info.c:list_domains(verbose=true)의 고정 열 형식이다.
        헤더/Domain-0/UUID까지 검증하므로 명령 실패나 빈 출력은 부재가 아니다.
        """
        lines = self.timed_command(['xl', 'list', '-v'], deadline).splitlines()
        if not lines or lines[0].split() != [
                'Name', 'ID', 'Mem', 'VCPUs', 'State', 'Time(s)', 'UUID',
                'Reason-Code', 'Security', 'Label']:
            raise PlatformError('invalid xl list -v header; removal not confirmed')
        rows, ids, names, uuids = {}, set(), set(), set()
        for line in lines[1:]:
            if not line.strip():
                continue
            fields = line.split()
            if (len(fields) < 9 or not fields[1].isdigit()
                    or not fields[2].isdigit() or not fields[3].isdigit()
                    or not re.fullmatch(r'[r-][b-][p-][s-][-rscwS?][d-]', fields[4])
                    or not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', fields[5])
                    or not UUID.fullmatch(fields[6])):
                raise PlatformError('invalid xl list -v identity/state; removal not confirmed')
            name, domid, uuid = fields[0], int(fields[1]), fields[6]
            state = xen_state(fields[4])
            unavailable_name = name == '(null)' and state in ('shutdown', 'dying')
            if domid in ids or (not unavailable_name and name in names) or uuid in uuids:
                raise PlatformError('duplicate xl list -v identity; removal not confirmed')
            ids.add(domid); uuids.add(uuid)
            if not unavailable_name:
                names.add(name)
            rows[domid] = {'domid': domid, 'name': name, 'uuid': uuid,
                           'xen_state': state, 'xen_state_flags': fields[4]}
        if 0 not in rows or rows[0]['name'] != 'Domain-0':
            raise PlatformError('Domain-0 missing from xl list -v; removal not confirmed')
        return {key: row for key, row in rows.items() if key != 0}

    def removal_observation(self, expected, deadline, *, board=False):
        """성공한 두 전체 목록으로 제거를 확인하며 교체·새 도메인을 거부한다."""
        snapshots = []
        for _ in range(2):
            rows = self.removal_snapshot(deadline)
            for row in rows.values():
                identity = (row['domid'], row['uuid'], row['name'])
                if identity in expected:
                    continue
                # libxl의 XenStore 이름 정리가 실제 도메인 제거보다 먼저 끝날 수 있다.
                # 같은 domid/UUID의 종료 행은 계속 pending이며 부재로 바꾸지 않는다.
                if (row['name'] == '(null)' and row['xen_state'] in ('shutdown', 'dying')
                        and any((row['domid'], row['uuid']) == old[:2] for old in expected)):
                    continue
                if any(row['domid'] == old[0] or row['uuid'] == old[1] or row['name'] == old[2]
                       for old in expected):
                    raise DomainIdentityError('replacement domain appeared during shutdown; no command sent to replacement')
                if board:
                    raise DomainIdentityError('unexpected domain appeared during shutdown; board shutdown refused')
            snapshots.append(rows)
        return snapshots

    def confirm_board_empty(self, deadline):
        self.removal_observation(set(), deadline, board=True)

    def check_xen(self, *, deadline=None):
        if 'control_d' not in Path('/proc/xen/capabilities').read_text().split():
            raise PlatformError('not Xen dom0: control_d capability absent')
        if not stat.S_ISCHR(Path('/dev/xen/privcmd').stat().st_mode):
            raise PlatformError('Xen privcmd control interface absent')
        self.timed_command(['xl', 'info'], deadline)
        self.domains(deadline=deadline)
        if not any(line.split()[1] == '0' for line in self.timed_command(['xl', 'list'], deadline).splitlines()[1:] if line.strip()):
            raise PlatformError('Domain-0 missing; VM start refused')

    def check_network(self, sysnet=Path('/sys/class/net'), *, deadline=None):
        bridge = sysnet / 'xenbr0'
        if not (bridge / 'bridge').is_dir():
            raise PlatformError('xenbr0 is not a Linux bridge')
        backend_bus = sysnet.parent.parent / 'bus/xen-backend'
        for port in (bridge / 'brif').iterdir():
            device = sysnet / port.name / 'device'
            # netback도 부모 device를 가진다. 이름과 실제 Xen backend 바인딩을 함께 확인한다.
            try:
                netback = (re.fullmatch(r'vif[0-9]+\.[0-9]+', port.name)
                           and (device / 'subsystem').resolve(strict=True) == backend_bus.resolve(strict=True)
                           and (device / 'driver').resolve(strict=True) == (backend_bus / 'drivers/vif').resolve(strict=True))
            except (OSError, RuntimeError):
                netback = False
            if not netback:
                raise PlatformError(f'non-Xen/physical NIC {port.name} attached to xenbr0; DHCP refused')
        links = json.loads(self.timed_command(['ip', '-j', '-4', 'address', 'show', 'dev', 'xenbr0'], deadline))
        if not any(a.get('local') == '192.168.77.1' and a.get('prefixlen') == 24
                   for link in links for a in link.get('addr_info', [])):
            raise PlatformError('xenbr0 must have 192.168.77.1/24')

    def prepare_dhcp(self, records, *, deadline=None, _outside=None):
        """예약 파일 게시는 공통 잠금 아래, 소유한 자산 호출의 서비스 대기만 밖에서 수행한다."""
        if _outside is None:
            with span('platform.prepare-dhcp'):
                return self._prepare_dhcp(records,deadline=deadline)
        # lock.hold를 끝내는 동안 그 안에 걸친 aggregate span을 열어두지 않는다.
        # 이 경로는 개별 external.command와 lock.released.lifecycle로 계측한다.
        return self._prepare_dhcp(records,deadline=deadline,_outside=_outside)

    def _prepare_dhcp(self,records,*,deadline=None,_outside=None):
        def command(args, timeout=15):
            remaining = timeout if deadline is None else min(timeout, deadline-self.clock())
            if remaining <= 0:
                from xenova_execution import refuse
                refuse('DEADLINE_EXCEEDED', 'guest operation deadline exceeded')
            with span('external.command', command_kind='.'.join(args[:2]), timeout_seconds=remaining):
                return self.command(args, timeout=remaining)
        def service():
            command(['systemctl', 'start', 'xenova-dhcp.service'], timeout=30)
            if deadline is None:self.check_network()
            else:self.check_network(deadline=deadline)
            command(['systemctl', 'is-active', '--quiet', 'xenova-dhcp.service'])
        if _outside is None:service()
        else:
            with _outside():service()
        # 서비스 대기 사이 새 VM이 게시될 수 있다. 이전 사본으로 최신 예약을 덮지 않는다.
        if _outside is not None:records=self.records(validate_assets=False)
        ensure_runtime(self.runtime)
        known = {r.get('mac') for r in records.values()}
        known.update(t['record'].get('mac') for t in read_tombstones(self.data).values())
        preserved = []
        path = self.runtime / 'dhcp-hosts'
        if path.exists() or path.is_symlink():
            st = path.lstat()
            if path.is_symlink() or not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                raise PlatformError('unsafe DHCP reservation file')
            for row in path.read_text().splitlines():
                if not row.strip() or row.lstrip().startswith('#'):
                    continue
                fields = row.split(',')
                if len(fields) != 4 or not re.fullmatch(r'[0-9a-f]{2}(?::[0-9a-f]{2}){5}', fields[0]):
                    raise PlatformError('cannot preserve unknown DHCP reservation')
                # 플랫폼이 소유한 현재/보관 VM 예약만 재작성한다. 다른 정적 예약은 유지한다.
                if fields[0] not in known:
                    preserved.append(row + '\n')
        hosts = ''.join(preserved) + ''.join(
            f"{r['mac']},{r['ipv4']},{r['hostname']},infinite\n"
            for r in records.values() if has_network(r))
        atomic_write(self.runtime / 'dhcp-hosts', hosts, 0o644)
        if _outside is None:command(['systemctl', 'reload', 'xenova-dhcp.service'])
        else:
            with _outside():command(['systemctl', 'reload', 'xenova-dhcp.service'])

    def cpu(self):
        from xenova_cpu import CPU
        return CPU(self)

    def config(self, record):
        base = self.data / 'instances' / record['id']
        if guest_kind(record) == 'zephyr':
            return zephyr.config(record, base, cpu_context=self.cpu().context(record))
        # Linux 설정은 P2의 설치된 OS별 템플릿에서 생성한다.
        # id/name/uuid와 자원 범위는 기존 등록 정보를 그대로 사용한다.
        from xen_config import ConfigError, render_config
        try:
            return render_config(record, instance_root=self.data / 'instances', cpu_context=self.cpu().context(record))
        except ConfigError as exc:
            raise PlatformError(str(exc)) from exc

    def matching(self, record, domains):
        matching = []
        for d in domains:
            if d.get('domid') == 0:
                continue
            info = d['config']['c_info']
            if info.get('uuid') == record['uuid']:
                if info.get('name') != record['name']:
                    raise DomainIdentityError('live UUID has different name')
                matching.append(d)
            elif info.get('name') == record['name']:
                raise DomainIdentityError('live name has different UUID')
        if len(matching) > 1:
            raise DomainIdentityError('duplicate live UUID')
        return matching

    def resolve_domain(self, record, *, expected=None, deadline=None):
        """영구 UUID/name을 현재 domid에 대조한다. domid는 저장하지 않는다.

        각 제어 호출 직전에 다시 조회한다. 외부 xl과 조회/실행 사이의 경쟁까지
        원자적으로 차단하지는 못하므로 이전 조회와 domid가 달라지면 거부한다.
        """
        rows = self.matching(record, self.domains(deadline=deadline))
        if not rows:
            raise DomainIdentityError(f"{record['id']}: domain disappeared or instance is stopped")
        row = rows[0]
        if expected is not None and row['domid'] != expected['domid']:
            raise DomainIdentityError(f"{record['id']}: domid changed before operation; refusing replacement domain")
        if expected is not None and expected.get("xen_unique_id") is not None:
            if self.execution().unique(row, deadline) != expected["xen_unique_id"]:
                raise DomainIdentityError("domain incarnation changed before operation")
        return row

    def require_cooperative(self, record, row, action, deadline):
        """Active bundle and per-incarnation handler feature must both support the operation."""
        if guest_kind(record) == 'linux':
            return
        from xenova_execution import refuse
        validate_record(record,self.data/'instances'/record['id'])
        if not cooperative(record):
            refuse('RECOVERY_MODE_UNSUPPORTED','active firmware has no cooperative handler')
        current=self.resolve_domain(record,expected=row,deadline=deadline)
        incarnation=self.execution().unique(current,deadline)
        # This validated profile supports both operations. Requiring the pair also
        # prevents accepting a new action during feature publication/withdrawal.
        for feature in ('poweroff','reboot'):
            try:
                ready=self.timed_command(['xenstore-read',f"/local/domain/{current['domid']}/control/feature-{feature}"],deadline)
            except PlatformError:
                refuse('OBSERVATION_UNAVAILABLE','cooperative handler readiness could not be observed')
            self.resolve_domain(record,expected={**current,'xen_unique_id':incarnation},deadline=deadline)
            if ready.strip()!='1':
                refuse('RECOVERY_MODE_UNSUPPORTED','cooperative handler is not ready in this execution')

    def wait_reboot_removed(self,record,old,deadline):
        """Two trusted snapshots plus incarnation checks; (null) is never absence."""
        previous=old['domid']
        def pair():
            snapshots=[self.removal_snapshot(deadline),self.removal_snapshot(deadline)]
            for snapshot in snapshots:
                for row in snapshot.values():
                    if row['domid']==previous:
                        if row['uuid']!=record['uuid'] or row['name'] not in (record['name'],'(null)'):
                            raise DomainIdentityError('old reboot domain was replaced')
                    elif row['uuid']==record['uuid'] or row['name']==record['name']:
                        raise DomainIdentityError('unexpected replacement during cooperative reboot')
            return snapshots
        def absent(snapshots):
            return all(previous not in snapshot for snapshot in snapshots)
        while self.clock()<deadline:
            snapshots=pair()
            if absent(snapshots):
                return
            try:
                incarnations=[self.execution().unique(snapshot[previous],deadline)
                              for snapshot in snapshots if previous in snapshot]
            except PlatformError:
                # A cooperative guest may disappear after list and before libxenmanage.
                # Only two NEW successful observations establish removal; lookup failure
                # alone, a still-present domain, or an unreadable list remains an error.
                if absent(pair()):
                    return
                raise
            if any(value!=old['xen_unique_id'] for value in incarnations):
                raise DomainIdentityError('old reboot incarnation was replaced')
            self.sleep(min(0.1,max(0,deadline-self.clock())))
        raise PlatformError('cooperative reboot removal deadline exceeded; no force')

    def control(self, record, action, expected, *, deadline=None, _launch=None):
        row = self.resolve_domain(record, expected=expected, deadline=deadline)
        if action=='reboot' and self.process_isolation and guest_kind(record)=='linux':
            from xenova_guest_process import command
            self.timed_command(command(self,record,deadline,target=expected,reboot_launch=_launch),deadline)
        elif action=='reboot':
            # 대역 runner도 최종 의도 검사와 제출의 순서를 실제 admission 잠금으로 검증한다.
            from xenova_execution import short_lock, refuse
            with short_lock(self.runtime,'admission'):
                if _launch:
                    self.execution().guard_launch(record,_launch,deadline)
                else:
                    intent=self.execution().intent(record)
                    if intent and intent['desired_state']!='running':
                        refuse('INTENT_MISMATCH','reboot superseded by stop intent')
                self.timed_command(['xl', action, str(row['domid'])], deadline)
        else:
            self.timed_command(['xl',action,str(row['domid'])],deadline)
        return row

    def _settle_created_domain(self, record, row, deadline):
        """생성 직후의 빈 xl 상태만 기존 기한 안에서 재관측한다.

        unknown을 running으로 바꾸지 않는다. 같은 incarnation의 실제
        running/blocked 관측이 필요하며 기존 도메인 시작에는 적용하지 않는다.
        """
        if row['xen_state'] != 'unknown':
            return row
        deadline = deadline if deadline is not None else self.clock()+15
        expected = {**row, 'xen_unique_id':self.execution().unique(row, deadline)}
        while row['xen_state'] == 'unknown':
            remaining = deadline-self.clock()
            if remaining <= 0:
                raise DomainStateError(record, row)
            self.sleep(min(0.1, remaining))
            row = self.resolve_domain(record, expected=expected, deadline=deadline)
        return row

    @traced('platform.start')
    def start(self, record, *, _accept=None, _owner=None, deadline=None, _result=None):
        """Validated launch primitive; user intent enters through request_control."""
        from xenova_preparation import guard_asset
        guard_asset(self, record['uuid'])
        execution = self.execution()
        deadline = self.clock()+90 if deadline is None else deadline
        execution.gate()
        self.timed_command(['systemctl', 'is-active', '--quiet', 'xenova-vm-shutdown.service'], deadline)
        directory = self.data / 'instances' / record['id']
        validate_record(record, directory)
        cpu_context = self.cpu().context(record)
        text = self.config(record)
        domains = self.domains(deadline=deadline)
        existing = self.matching(record, domains)
        if existing:
            state = started_state(record, existing[0])
            if _accept:
                _accept()
            observation = execution.observe(record, deadline)
            state = started_state(record, observation)
            self.cpu().require(record, deadline, expected=cpu_context, present=True,
                               expected_identity=observation)
            self.supply_execution(record,deadline)
            if _result is not None:
                _result['observation'] = observation
            print(f"{record['id']}: already {state}; no duplicate create; app_readiness=unknown")
            return state
        disk = directory / 'root.raw' if guest_kind(record) == 'linux' else None
        for d in domains:
            for item in d.get('config', {}).get('disks', []):
                path = item.get('pdev_path')
                if disk is not None and path and Path(path).exists() and os.path.samefile(path, disk):
                    raise PlatformError(f'{disk} is already attached to domain {d["domid"]}')
        execution.require_launch_available(record)
        owner = _accept() if _accept else _owner
        execution.gate()
        ensure_runtime(self.runtime)
        captured = {}
        self.cpu().require(record, deadline, expected=cpu_context, capture=captured)
        launch = execution.prepare_launch(record, owner, deadline=deadline, expected_cpu=cpu_context,
                                          cpu_evidence=captured.get('evidence'))
        config = self.runtime / (record['id'] + '.cfg')
        atomic_write(config, text)
        # 준비/쓰기 뒤 새 도메인·디스크 점유를 다시 확인한다. 이 검사는
        # CPU 근거 재사용과 별개이며 외부 xl을 전역 잠금으로 막는다는 뜻이 아니다.
        latest = self.domains(deadline=deadline)
        if self.matching(record, latest):
            raise DomainIdentityError('domain appeared before explicit launch')
        for domain in latest:
            for item in domain.get('config', {}).get('disks', []):
                path = item.get('pdev_path')
                if disk is not None and path and Path(path).exists() and os.path.samefile(path, disk):
                    raise PlatformError(f'{disk} became attached before launch')
        try:
            execution.guard_launch(record, launch, deadline)
        except PlatformError:
            # 아직 helper/명령을 만들지 않은 이 경계만 안전한 미제출 종결 근거다.
            # 확인·저장 실패면 원 거절과 미확정 실행을 유지하며 성공으로 바꾸지 않는다.
            try:
                execution.abort_unsubmitted_launch(record, launch, deadline)
            except (PlatformError, OSError, ValueError):
                pass
            raise
        command_deadline=min(deadline,self.clock()+45)
        remaining = command_deadline-self.clock()
        if remaining <= 0:
            from xenova_execution import refuse
            refuse('DEADLINE_EXCEEDED', 'guest operation deadline exceeded')
        def submit(argv):
            left=command_deadline-self.clock()
            if left<=0:
                from xenova_execution import refuse
                refuse('DEADLINE_EXCEEDED','create submission deadline expired')
            # create의 기존 45초 상한을 유지한다. 조회용 timed_command의 15초와 구별한다.
            return self.command(argv,timeout=left)
        with span('external.command', command_kind='xl.create', timeout_seconds=remaining):
            if _result is not None:
                _result['physical_submitted'] = True
            if self.process_isolation:
                from xenova_guest_process import command
                argv=command(self,record,deadline,launch=launch)
                submit(argv)
            else:
                from xenova_execution import short_lock
                with short_lock(self.runtime,'admission'):
                    execution.guard_launch(record,launch,deadline)
                    submit(['xl','create',str(config)])
        created = self.matching(record, self.domains(deadline=deadline))
        if not created:
            raise PlatformError(f"{record['id']}: xl create returned without a live domain")
        current = self._settle_created_domain(record, created[0], deadline)
        state = started_state(record, current)
        bound = execution.bind_launch(record, launch, current, deadline)
        self.supply_execution(record,deadline)
        if _result is not None:
            _result['observation'] = bound['observation']
        print(f"{record['id']}: Xen domain created ({state}); guest OS/app readiness is not asserted")
        return state

    def supply_execution(self,record,deadline):
        """검증된 새 실행에 기존 영구 자격을 게시한다. guest 인증/준비 성공은 별개다."""
        if guest_kind(record)=='zephyr' and record.get('provisioning_profile',{}).get('channels'):
            from xenova_provisioning import bootstrap_execution
            bootstrap_execution(self,record,deadline=deadline)

    def autostart(self, records, required_ids, *, deadline=None):
        """한 부팅 호출의 전체 예산을 공유하고 이미 시작한 guest를 롤백하지 않는다.

        이 결과는 Xen 실행 단계다. guest OS·앱 준비를 기다리지 않으며, 실패한
        start는 실제 효과가 없다는 뜻이 아니다. 재호출은 기존 실행 신원을 확인한다.
        """
        deadline = self.clock() + AUTOSTART_TIMEOUT if deadline is None else deadline
        host = self.execution().host
        missing = [key for key in required_ids if key not in records]
        failures = ['default instances not provisioned: ' + ','.join(missing)] if missing else []
        results = {key: {'result': 'failed', 'error': 'default instance not provisioned'} for key in missing}
        results.update({key: {'result': 'pending'} for key in records})
        ensure_runtime(self.runtime)
        from xenova_runtime import boot_progress
        display = boot_progress('autostart', self.runtime, clock=self.clock, host=host)
        display_states = {key: {'vm_id': record['uuid'], 'name': record.get('display_name', record['id']),
                               'internal_name': key, 'state': 'waiting', 'selected': None, 'code': None}
                          for key, record in records.items()}
        def show(phase):
            display(phase, vms=list(display_states.values()), missing_instances=missing)
        show('targets-discovered')
        result_path = self.runtime / 'autostart-results.json'
        def save_results():
            for value in results.values():
                value.update(host_boot_id=host, deadline_monotonic_ms=int(deadline * 1000))
            atomic_write(result_path, json.dumps(results, indent=2) + '\n')
        save_results()
        for key, record in records.items():
            if self.clock() >= deadline:
                # admission/intent를 새로 만들거나 다음 VM에 새 예산을 주지 않는다.
                for value in results.values():
                    if value['result'] == 'pending':
                        value.update(result='not-started', error='autostart batch deadline exceeded',
                                     start_invoked=False)
                for key, item in results.items():
                    if item['result'] == 'not-started' and key in display_states:
                        display_states[key].update(state='not-started', code='BATCH_DEADLINE_EXCEEDED')
                show('deadline-exceeded')
                failures.append('autostart batch deadline exceeded; pending guests not started')
                save_results()
                break
            decision = self.execution().boot_decision(record)
            display_states[key]['selected'] = bool(decision['start'])
            if not decision['start']:
                results[key] = {'result': 'skipped', 'autostart': record['autostart'],
                                'boot_admission': decision['admission']}
                save_results()
                display_states[key].update(state='excluded', code=decision['admission'].get('reason'))
                show('policy-selection')
                continue
            results[key] = {'result': 'starting'}
            save_results()
            start_invoked = False
            failure_code = None
            try:
                if has_network(record):
                    display_states[key]['state'] = 'network-prepare'
                    show('network-preparation')
                    self.prepare_dhcp(records, deadline=deadline)
                if self.clock() >= deadline:
                    raise PlatformError('autostart batch deadline exceeded before start')
                start_invoked = True
                display_states[key]['state'] = 'execution-start'
                show('execution-start-and-verification')
                state = self.start(record, deadline=deadline)
                results[key] = {'result': state, 'xen_state': state, 'app_readiness': 'unknown',
                                'boot_admission': decision['admission']}
            except (PlatformError, OSError, ValueError, KeyError, TypeError) as exc:
                failures.append(f"{key}: {exc}")
                known_code = getattr(exc, 'error', {}).get('code')
                if isinstance(known_code, str) and re.fullmatch('[A-Z][A-Z0-9_]{0,63}', known_code):
                    failure_code = known_code
                results[key] = {'result': 'not-started' if not start_invoked and self.clock() >= deadline else 'failed',
                                'error': str(exc), 'app_readiness': 'unknown',
                                'start_invoked': start_invoked, 'boot_admission': decision['admission']}
                if isinstance(exc, DomainStateError):
                    results[key].update(xen_state=exc.xen_state, xen_state_flags=exc.xen_state_flags)
            save_results()
            outcome = results[key]['result']
            code = None
            if outcome in ('failed', 'not-started'):
                # 원문 stderr/exception에는 비밀이 있을 수 있어 화면에는 원인 유형만 게시한다.
                code = failure_code or ('START_FAILED_RESULT_MAY_EXIST' if start_invoked else 'START_NOT_SUBMITTED')
            display_states[key].update(state=outcome if outcome in ('failed', 'not-started') else 'verified', code=code)
            show('result-recorded')
        show('failed' if failures else 'complete')
        print(json.dumps({'autostart_results': results}, indent=2))
        if failures:
            raise PlatformError('; '.join(failures))

    def observed_target(self, record, rows, observation, deadline):
        """수락 전 관측과 실제 제어 계획을 같은 incarnation에 연결한다."""
        if observation is None:
            return rows
        present = observation['presence'] == 'present'
        if bool(rows) != present or (rows and rows[0]['domid'] != observation['domid']):
            raise DomainIdentityError('target changed after control observation')
        if rows:
            row = {**rows[0], 'xen_unique_id':observation['xen_unique_id']}
            self.resolve_domain(record, expected=row, deadline=deadline)
            return [row]
        return rows

    def stop_many(self, records, timeout=90, *, force=False, board=False, deadline=None,
                  _accept=None, _expected_runs=None, _observed=None, _result=None):
        """사전 안전 검사와 요청 후 신원 관측을 분리한다. 자동 강제 종료는 없다."""
        from xenova_preparation import guard_asset
        for record in records:
            guard_asset(self, record['uuid'])
        ensure_runtime(self.runtime)
        if board:
            ensure_quiet(self)
        attempt = os.urandom(16).hex()
        result = {'action': 'stop', 'attempt_id': attempt, 'stopped': [], 'already_stopped': [],
                  'forced': [], 'graceful_shutdown_requested': [], 'instances': {},
                  'board_shutdown': board, 'global_force': force,
                  'observation': {'samples': 0, 'changed_pairs': 0, 'recent_changes': []}}
        def save():
            text = json.dumps(result, indent=2) + '\n'
            atomic_write(self.runtime / ('stop-attempt-' + attempt + '.json'), text)
            atomic_write(self.runtime / 'last-stop.json', text)
        if deadline is None:
            deadline = self.clock() + timeout
        try:
            domains = self.domains(deadline=deadline)
            expected = {(d['domid'], d['config']['c_info']['uuid'], d['config']['c_info']['name'])
                        for d in domains if d['domid'] != 0}
            planned, known = [], set()
            for record in records:
                rows = self.matching(record, domains)
                rows = self.observed_target(record, rows, (_observed or {}).get(record['uuid']), deadline)
                if not rows:
                    if self.process_isolation:
                        from xenova_guest_process import cleanup
                        cleanup(self,record,self.execution().run_record(record),deadline)
                    result['already_stopped'].append(record['id'])
                    result['instances'][record['id']] = {'result': 'already_stopped', 'stage': 'domain-absent'}
                    continue
                row = rows[0]
                known.add(row['domid'])
                strategy = 'forced' if force else 'graceful'
                if guest_kind(record) == 'zephyr' and not cooperative(record):
                    if not force and not (board and board_shutdown_policy(record) == 'force-allowed'):
                        raise PlatformError('Zephyr graceful shutdown unsupported; explicit --force or board force-allowed policy required: ' + record['id'])
                    strategy = 'forced'
                if strategy == 'graceful' and guest_kind(record) == 'zephyr':
                    self.require_cooperative(record,row,'shutdown',deadline)
                if _expected_runs and record['uuid'] in _expected_runs:
                    row = {**row, 'xen_unique_id': _expected_runs[record['uuid']]}
                    self.resolve_domain(record, expected=row, deadline=deadline)
                planned.append((record, row, strategy))
            if board:
                unknown = [d['domid'] for d in domains if d['domid'] != 0 and d['domid'] not in known]
                if unknown:
                    raise PlatformError('running unregistered domain has no shutdown policy: ' + str(unknown))
        except (PlatformError, OSError, ValueError, KeyError, TypeError) as exc:
            result['preflight_error'] = str(exc)
            for record in records:
                result['instances'].setdefault(record['id'], {'result': 'not_requested', 'stage': 'preflight-refused'})
            save()
            raise
        if _accept:
            _accept()
        pending = []
        identity_error = None
        for record, row, strategy in planned:
            key = record['id']
            result['instances'][key] = {'result': 'pending', 'method': strategy, 'domid': row['domid'],
                                        'stage': 'identity-recheck'}
            save()
            try:
                if _result is not None:
                    _result['physical_submitted'] = True
                self.control(record, 'destroy' if strategy == 'forced' else 'shutdown', row, deadline=deadline)
            except (PlatformError, OSError, ValueError, KeyError, TypeError) as exc:
                if isinstance(exc, DomainIdentityError):
                    identity_error = exc
                result['instances'][key].update(result='failed', error=str(exc))
            else:
                result['instances'][key]['stage'] = 'request-accepted'
                result['forced' if strategy == 'forced' else 'graceful_shutdown_requested'].append(key)
                pending.append((record, row, strategy))
            save()
        while pending:
            if self.clock() >= deadline:
                for record, row, strategy in pending:
                    message = ('forced removal not observed before deadline' if strategy == 'forced' else
                               'graceful shutdown timed out; disks retained, no destroy')
                    result['instances'][record['id']].update(result='timeout', error=message, stage='removal-timeout')
                break
            try:
                before, after = self.removal_observation(expected, deadline, board=board)
            except (PlatformError, OSError, ValueError, KeyError, TypeError) as exc:
                if isinstance(exc, DomainIdentityError):
                    identity_error = exc
                for record, row, strategy in pending:
                    result['instances'][record['id']].update(result='failed', error=str(exc), stage='removal-query-failed')
                break
            observation = result['observation']
            observation['samples'] += 2
            before_ids, after_ids = sorted(before), sorted(after)
            if before_ids != after_ids:
                observation['changed_pairs'] += 1
                observation['recent_changes'] = (observation['recent_changes'] + [
                    {'before_domids': before_ids[:32], 'after_domids': after_ids[:32]}])[-4:]
            remaining = []
            for record, row, strategy in pending:
                key, domid = record['id'], row['domid']
                if domid not in before and domid not in after:
                    if self.process_isolation:
                        from xenova_guest_process import cleanup
                        cleanup(self,record,self.execution().run_record(record),deadline)
                    result['stopped'].append(key)
                    self.execution().end_run(record)
                    result['instances'][key].update(result=strategy, stage='domain-removed', xen_state='stopped')
                else:
                    current = after.get(domid) or before[domid]
                    result['instances'][key]['xen_state'] = current['xen_state']
                    remaining.append((record, row, strategy))
            pending = remaining
            save()
            if pending and self.clock() < deadline:
                self.sleep(min(1, max(0, deadline - self.clock())))
        if board and not any(item['result'] in ('failed', 'timeout') for item in result['instances'].values()):
            try:
                self.confirm_board_empty(deadline)
            except (PlatformError, OSError, ValueError, KeyError, TypeError) as exc:
                if isinstance(exc, DomainIdentityError):
                    identity_error = exc
                result['final_check_error'] = str(exc)
        os.sync()
        save()
        print(json.dumps(result, indent=2))
        failures = [f"{key}: {item.get('error', item['result'])}" for key, item in result['instances'].items()
                    if item['result'] in ('failed', 'timeout')]
        if result.get('final_check_error'):
            failures.append(result['final_check_error'])
        if failures:
            if identity_error is not None:
                raise DomainIdentityError('; '.join(failures)) from identity_error
            raise PlatformError('; '.join(failures))
        return result

    def prepare_board_shutdown(self, records, action, *, force=False, deadline=None):
        self.execution().quiesce(action)
        ensure_quiet(self)  # 대기 drain은 CLI가 공통 잠금 밖에서 수행한다.
        self._shutdown_deadline = self.clock() + 90 if deadline is None else deadline
        try:
            result = self.stop_many(records, force=force, board=True, deadline=self._shutdown_deadline)
        except (PlatformError, OSError, ValueError, KeyError, TypeError):
            if (self.runtime / 'last-stop.json').is_file():
                result = json.loads((self.runtime / 'last-stop.json').read_text())
                atomic_write(self.runtime / 'board-shutdown-result.json', json.dumps({'board_action': action, **result}, indent=2) + '\n')
            raise
        atomic_write(self.runtime / 'board-shutdown-result.json', json.dumps({'board_action': action, **result}, indent=2) + '\n')
        atomic_write(self.runtime / 'shutdown-requested', action + '\n')

    def finish_board_shutdown(self, action):
        try:
            barrier=self.execution().barrier()
            if not barrier or barrier['state']!='quiescing':
                raise PlatformError('host admission must remain closed before storage shutdown')
            ensure_quiet(self)
            # 내부 CLI는 marker로 새 기동을 막는다. 외부 xl과 원자적인 배제는 아니다.
            self.confirm_board_empty(self._shutdown_deadline)
            self.command(['systemctl', 'stop', 'xenova-vms.service', 'xenova-vm-shutdown.service', 'xenova-dhcp.service'], timeout=110)
            self.confirm_board_empty(self._shutdown_deadline)
            self.command(['systemctl', 'stop', 'srv-xenova.mount'], timeout=30)
            self.command(['systemctl', action])
        except (PlatformError, OSError, ValueError, KeyError, TypeError) as exc:
            (self.runtime / 'shutdown-requested').unlink(missing_ok=True)
            path = self.runtime / 'board-shutdown-result.json'
            result = json.loads(path.read_text()) if path.exists() else {'board_action':action}
            result['final_check_error'] = str(exc)
            atomic_write(path, json.dumps(result, indent=2) + '\n')
            raise

    def reboot_candidate(self, record, previous, deadline):
        """Find a stable new Linux incarnation with the registered root disk."""
        snapshot = self.removal_snapshot(deadline)
        old = snapshot.get(previous['domid'])
        if old and (old['uuid'] != record['uuid'] or old['name'] not in (record['name'], '(null)')):
            raise DomainIdentityError('replacement domain appeared during reboot')
        candidates = [row for row in snapshot.values()
                      if row['uuid'] == record['uuid'] or row['name'] == record['name']]
        if len(candidates) > 1:
            raise DomainIdentityError('ambiguous reboot candidates')
        if not candidates:
            return None
        row = candidates[0]
        # A restarting libxl domain can appear before its XenStore name is available.
        # A matching UUID and missing name only justify another observation.
        if row['uuid'] != record['uuid'] or row['name'] not in (record['name'], '(null)'):
            raise DomainIdentityError('replacement domain appeared during reboot')
        if row['name'] == '(null)' or row['xen_state'] not in ('running', 'blocked'):
            return None
        execution = self.execution()
        unique = execution.unique(row, deadline)
        if unique == previous['xen_unique_id']:
            return None
        domains = self.domains(deadline=deadline)
        current = self.matching(record, domains)
        if not current or current[0]['domid'] != row['domid']:
            raise DomainIdentityError('reboot target changed before launch binding')
        current = current[0]
        if current['xen_state'] == 'unknown':
            return None
        disk = self.data/'instances'/record['id']/'root.raw'
        disks = current['config']['disks']
        if (len(disks) != 1 or not disks[0].get('pdev_path')
                or not os.path.samefile(disks[0]['pdev_path'], disk)):
            raise DomainIdentityError('reboot candidate does not own the registered root disk')
        for domain in domains:
            if domain['domid'] in (0, current['domid']):
                continue
            for item in domain['config']['disks']:
                path = item.get('pdev_path')
                if path and Path(path).exists() and os.path.samefile(path, disk):
                    raise DomainIdentityError('registered reboot disk is attached to another domain')
        current = {**current, 'xen_unique_id':unique}
        if execution.unique(current, deadline) != unique:
            raise DomainIdentityError('reboot candidate incarnation changed during observation')
        started_state(record, current)
        return current

    @traced('platform.reboot')
    def reboot(self, record, timeout=90, *, force=False, _accept=None, _observed=None,
               deadline=None, _result=None):
        from xenova_preparation import guard_asset
        guard_asset(self, record['uuid'])
        deadline = self.clock()+timeout if deadline is None else deadline
        validate_record(record, self.data / 'instances' / record['id'])
        rows = self.matching(record, self.domains(deadline=deadline))
        rows = self.observed_target(record, rows, _observed, deadline)
        if not rows:
            raise PlatformError('cannot reboot a stopped VM')
        if guest_kind(record) == 'zephyr' and not force:
            raise PlatformError('Zephyr graceful reboot unsupported; use reboot --force')
        self.execution().require_launch_available(record)
        cpu_context = self.cpu().context(record)
        if guest_kind(record) == 'zephyr' or force:
            owner = _accept() if _accept else None
            self.stop_many([record], timeout=timeout, force=True, deadline=deadline,
                           _observed={record['uuid']:_observed} if _observed else None, _result=_result)
            self.start(record, _owner=owner, deadline=deadline, _result=_result)
            print(json.dumps({'action': 'reboot', 'id': record['id'], 'forced': True, 'app_readiness': 'unknown'}))
            return
        execution = self.execution()
        previous = {'domid':rows[0]['domid'],
                    'xen_unique_id':rows[0].get('xen_unique_id') or execution.unique(rows[0], deadline)}
        ensure_runtime(self.runtime)
        owner = _accept() if _accept else None
        captured = {}
        self.cpu().require(record, deadline, expected=cpu_context, present=True,
                           expected_identity=previous, capture=captured)
        launch = execution.prepare_launch(record, owner, deadline=deadline, expected_cpu=cpu_context,
                                          cpu_evidence=captured.get('evidence'), reuse_process_scope=True,
                                          reboot_from=previous)
        execution.guard_launch(record, launch, deadline)
        if _result is not None:
            _result['physical_submitted'] = True
        self.control(record, 'reboot', previous, deadline=deadline, _launch=launch)
        launch = {**launch, 'reboot_returned':True}
        execution.write(execution._path(self.runtime, 'executions', record['uuid']), launch)
        while self.clock() < deadline:
            current = self.reboot_candidate(record, previous, deadline)
            if current is not None:
                bound = execution.bind_launch(record, launch, current, deadline)
                if _result is not None:
                    _result['observation'] = bound['observation']
                print('guest domain restarted with same UUID/disk; OS readiness not asserted')
                return
            self.sleep(min(1, max(0, deadline-self.clock())))
        raise PlatformError('guest reboot not observed before deadline; no force reset')


    def status(self, records):
        deadline = self.clock()+15
        domains = self.domains(deadline=deadline)
        execution = self.execution()
        observations = execution.observe_many(list(records.values()), deadline)
        result = []
        for r in records.values():
            try:
                validate_record(r, self.data / 'instances' / r['id'])
                asset_error = None
            except (PlatformError, OSError, ValueError, KeyError) as exc:
                asset_error = str(exc)
            observation = observations[r['uuid']]
            lifecycle, generation = execution.lifecycle(r)
            control = execution.describe(r, generation, lifecycle, observation)
            result.append({**r, **control, 'asset_error': asset_error, 'guest_kind': guest_kind(r), 'registered': True,
                           'xen_state': observation['xen_state'],
                           'xen_state_flags': observation['xen_state_flags'],
                           'domid': observation['domid'], 'app_readiness': 'unknown',
                           'graceful_shutdown_supported': cooperative(r),
                           'effective_board_shutdown_policy': board_shutdown_policy(r)})
        return {'instances': result, 'domains': domains, 'time': self.time_status(deadline=deadline)}

    def time_status(self, *, deadline=None):
        try:
            text = self.timed_command(['timedatectl', 'show', '-p', 'NTPSynchronized', '-p', 'NTP', '-p', 'Timezone'],
                                      self.clock()+15 if deadline is None else deadline)
            values = dict(row.split('=', 1) for row in text.splitlines() if '=' in row)
            return {'synchronized': (values['NTPSynchronized'] == 'yes') if values.get('NTPSynchronized') in ('yes', 'no') else None, 'details': values,
                    'interval_clock': 'monotonic'}
        except PlatformError as exc:
            return {'synchronized': None, 'error': str(exc), 'interval_clock': 'monotonic'}

    def replace_firmware(self, record, source, expected, *, execute=False, _context=None):
        from xenova_preparation import guard_asset
        from xenova_management import Manager
        manager=Manager(self)
        if _context is None:guard_asset(self, record['uuid'])
        else:manager._asset_guard(_context,record['uuid'])
        if guest_kind(record) != 'zephyr':
            raise PlatformError('firmware-replace requires a Zephyr instance')
        if self.matching(record, self.domains()):
            raise PlatformError('firmware replacement requires a stopped domain')
        directory = self.data / 'instances' / record['id']
        records=self.records(validate_assets=False)
        with manager._lifecycle_io(_context or {},records):
            info = zephyr.bundle_info(source, expected)
        if not expected:
            raise PlatformError('--sha256 of firmware manifest is required')
        if execute:
            self.reserve_space(info['logical_bytes'] * 2, records)
            if _context is None:zephyr.replace_firmware(directory, record, source, expected)
            else:
                with manager._lifecycle_io(_context,records):
                    updated=zephyr.prepare_replacement(directory,record,source,expected)
                manager._stopped(record)
                if manager._read(directory/'instance.json')!=record:
                    raise PlatformError('firmware configuration changed before publication')
                from xenova_execution import short_lock
                with short_lock(self.runtime,'admission'):
                    manager._writable()
                    atomic_write(directory/'instance.json',json.dumps(updated,indent=2)+'\n')
        return {'action': 'firmware-replace' if execute else 'plan-firmware-replace',
                'id': record['id'], 'firmware': info, 'uuid': record['uuid'],
                'starts_guest': False}

    def delete(self, record, *, execute=False, idempotency_key=None):
        """delete는 공통 retire의 보관 별칭이며 영구 삭제를 수행하지 않는다."""
        from xenova_management import Manager
        return Manager(self).call('retire',{'vm_id':record['uuid']},execute=execute,
                                  idempotency_key=idempotency_key)


def read_tombstones(data, *, deadline=None, clock=time.monotonic):
    """정식 UUID.json만 읽는다. atomic_write 잔여 파일은 검증 후 보존하고 채택하지 않는다."""
    from xenova_catalog import plain
    root = data / 'tombstones'
    if not root.exists() and not root.is_symlink():
        return {}
    plain(root, directory=True)
    result = {}
    for path in sorted(root.iterdir()):
        if deadline is not None and clock()>=deadline:
            from xenova_execution import refuse
            refuse('DEADLINE_EXCEEDED','lifecycle scan budget expired')
        suffix = re.fullmatch(r'(.+\.json)\.tmp-[0-9]+-[0-9a-f]{12}', path.name)
        if suffix and UUID.fullmatch(suffix[1][:-5]):
            plain(path)
            continue
        if path.suffix != '.json' or not UUID.fullmatch(path.stem):
            raise PlatformError('invalid tombstone path')
        tomb = json.loads(plain(path).read_text())
        if not isinstance(tomb, dict) or not isinstance(tomb.get('record'), dict):
            raise PlatformError('tombstone and record must be objects')
        record = tomb['record']
        validate_identity(record)
        if (tomb.get('schema_version') != '1.0' or tomb.get('vm_id') != path.stem
                or record['uuid'] != path.stem or record['name'] != tomb.get('name')
                or type(tomb.get('generation')) is not int or tomb['generation'] < 0
                or tomb.get('lifecycle') not in ('REGISTERED', 'RETIRED', 'PURGING', 'PURGED')):
            raise PlatformError('invalid tombstone identity/lifecycle')
        result[path.stem] = tomb
    return result


def retired_instance(data, key, identity=None):
    for tomb in read_tombstones(data).values():
        if tomb['name'] == key and (identity is None or tomb['vm_id'] == identity):
            return tomb['lifecycle'] != 'REGISTERED'
    for path in (data / 'instances').glob('.deleted-' + key + '-*'):
        if path.is_symlink() or not path.is_dir():
            raise PlatformError('invalid retired instance path')
        r = json.loads(regular_asset(path, 'instance.json').read_text())
        validate_identity(r)
        if r['id'] != key or path.name != '.deleted-' + key + '-' + r['uuid']:
            raise PlatformError('invalid retired instance identity')
        if identity is None or r['uuid'] == identity:
            return True
    return False


def dhcp_ready(pid, timeout=5):
    """dnsmasq 프로세스 소유의 UDP/67 소켓을 제한 시간 내 확인한다."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        fds = Path(f'/proc/{pid}/fd')
        if not fds.exists():
            raise PlatformError('dnsmasq exited before DHCP became ready')
        sockets = {os.readlink(p) for p in fds.iterdir() if p.is_symlink()}
        for line in Path('/proc/net/udp').read_text().splitlines()[1:]:
            fields = line.split()
            if fields[1].endswith(':0043') and f'socket:[{fields[9]}]' in sockets:
                return
        time.sleep(0.1)
    raise PlatformError('dnsmasq did not open UDP/67 within 5s')


def legacy_command(action, options, *, platform=None):
    """부팅·PTY·명시 진단의 기존 handler. 공통 VM 관리는 CLI에서 Manager로 연결한다."""
    from argparse import Namespace
    args = Namespace(**{'force':False,'execute':False,'id':None,'bundle':None,'sha256':None,
                        **options,'action':action})
    p = platform if platform is not None else Platform()
    if args.action in ('host-init', 'host-quiesce'):
        value = p.execution().initialize_host() if args.action == 'host-init' else p.execution().quiesce('systemd-shutdown')
        if args.action=='host-quiesce':
            # host-admission unit의 기존 TimeoutStopSec=10s 안에서 반환 여유 1초를 남긴다.
            drain(p,p.clock()+9)
        print(json.dumps(value))
        return
    if args.action == 'check-network':
        p.check_network()
        ensure_runtime(p.runtime)
        hosts = p.runtime / 'dhcp-hosts'
        # DHCP의 ExecStartPre는 부모 VM 작업의 잠금을 다시 잡지 않는다.
        # O_EXCL로 빈 파일만 만들며 동시 게시된 예약 파일을 덮어쓰지 않는다.
        try:
            fd = os.open(hosts, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        except FileExistsError:
            pass
        else:
            os.close(fd)
        return
    if args.action == 'dhcp-ready':
        dhcp_ready(int(args.id))
        return
    if args.action == 'check-xen':
        # xenova-xen-ready.service의 30초 상한 안에서 모든 xl 조회가 예산을 공유한다.
        p.check_xen(deadline=p.clock()+25)
        return
    if args.action == 'firmware-replace':
        if args.bundle is None:
            raise PlatformError('firmware-replace requires --bundle and --sha256')
        from xenova_preparation import AssetLease, lifecycle_slot, request_output
        records=p.records(validate_assets=False)
        if args.id not in records:raise PlatformError('unknown instance id; create it explicitly')
        identity=records[args.id]['uuid']
        with request_output() as output:
            lease=output.stack.enter_context(AssetLease(p,identity,'firmware-replace',None))
            output.stack.enter_context(lifecycle_slot(p))
            with operation_lock(p.runtime) as common:
                record=p.records(validate_assets=False).get(args.id)
                if not record or record['uuid']!=identity:raise PlatformError('firmware target changed')
                lease.guard()
                result=p.replace_firmware(record,args.bundle,args.sha256,execute=args.execute,
                                          _context={'asset_lease':lease,'common_fd':common})
        # 정식 CLI 결과는 진단 sink와 분리하고 소유권 해제 뒤 호출자가 출력한다.
        return result
    if args.action == 'diagnose':
        if options.get('details'):
            with operation_lock(p.runtime):
                result = p.status(p.records(validate_assets=False))
            return result
        return {'time': p.time_status(), 'cmdline': Path('/proc/cmdline').read_text(),
                'uptime': Path('/proc/uptime').read_text().split()[0]}
    board_action, console_target = None, None
    shutdown_deadline = None
    if args.action in ('shutdown-all', 'poweroff', 'reboot-board'):
        shutdown_deadline = p.clock()+90
        p.execution().quiesce(args.action)
        drain(p,shutdown_deadline)
    autostart_deadline = p.clock()+AUTOSTART_TIMEOUT if args.action == 'autostart' else None
    with operation_lock(p.runtime, deadline=shutdown_deadline, clock=p.clock, sleep=p.sleep):
        # 전체 신원/경로 검증과 실행 대상의 부팅 자산 검증은 수명을 달리한다.
        records = p.records(validate_assets=False)
        if args.action == 'autostart':
            p.check_xen(deadline=autostart_deadline)
            defaults = json.loads(p.defaults_path.read_text())
            required = defaults['instances'] + defaults.get('firmware_instances', [])
            p.autostart(records, [r['id'] for r in required if not retired_instance(p.data, r['id'])],
                        deadline=autostart_deadline)
        elif args.action in ('shutdown-all', 'poweroff', 'reboot-board'):
            if args.action == 'shutdown-all':
                p.stop_many(list(records.values()), force=args.force, board=True, deadline=shutdown_deadline)
            else:
                board_action = 'reboot' if args.action == 'reboot-board' else 'poweroff'
                p.prepare_board_shutdown(list(records.values()), board_action, force=args.force, deadline=shutdown_deadline)
        elif args.action == 'console':
            if args.id not in records:
                raise PlatformError('unknown instance id; create it explicitly')
            record = records[args.id]
            row = p.resolve_domain(record)
            console_target = str(p.resolve_domain(record, expected=row)['domid'])
    if console_target:
        # 직전 조회로 검증한 domid만 전달한다. 콘솔 세션 동안 작업 잠금은 보유하지 않는다.
        os.execvp('xl', ['xl', 'console', '-t', 'pv', console_target])
    if board_action:
        # ExecStop도 같은 잠금을 사용하므로 잠금 밖에서 서비스를 중지한다.
        p.finish_board_shutdown(board_action)


def main(argv=None):
    from xenova_management_cli import main as management_main
    return management_main(argv)


if __name__ == '__main__':
    try:
        main()
    except (PlatformError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f'[xenova] ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
