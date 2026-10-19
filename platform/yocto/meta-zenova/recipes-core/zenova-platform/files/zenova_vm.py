#!/usr/bin/python3
"""Linux·Zephyr의 영구 등록·생명주기를 관리한다. 등록, Xen 실행, 앱 준비 상태는 별개다.

명시적 create만 자산을 생성한다. start는 등록 자산을 재사용하며 자동 복구는 하지 않는다.
"""
import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

from zenova_runtime import PlatformError, atomic_write, ensure_runtime, operation_lock, sync_directory
import zenova_zephyr as zephyr

DATA = Path('/srv/zenova')
RUN = Path('/run/zenova')
ID = re.compile(r'[a-z][a-z0-9-]{0,39}\Z')
UUID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z')
MAC = re.compile(r'02(?::[0-9a-f]{2}){5}\Z')


def run(args, timeout=15):
    try:
        p = subprocess.run(args, text=True, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=timeout, check=False)
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
    if (data / '.zenova-install.json').exists():
        state = json.loads((data / '.first-install-state.json').read_text())
        if state.get('schema') != 1 or state.get('phase') != 'complete':
            raise PlatformError('first installation incomplete; VM start refused')
    marker = json.loads((data / '.zenova-storage.json').read_text())
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


def identity_fields(record):
    return ('id', 'name', 'uuid') + (('hostname', 'mac', 'ipv4') if guest_kind(record) == 'linux' else ())


def board_shutdown_policy(record):
    """기존 등록은 Linux=graceful, Zephyr=refuse. 자동 시작은 종료 승인이 아니다."""
    linux = guest_kind(record) == 'linux'
    policy = record.get('board_shutdown_policy', 'graceful' if linux else 'refuse')
    if policy not in (('graceful',) if linux else ('refuse', 'force-allowed')):
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


def load_records(data, *, validate_assets=True):
    """공통 신원·경로·디스크 중복 검증. validate_assets는 전체 부팅 자산 검사도 요청한다.

    CLI는 공통 검증 후 실행 대상만 자산을 검사한다. 누락된 비대상 부팅 자산은
    종료/콘솔을 막지 않지만, 연결된 경로나 공유 쓰기 디스크는 항상 거부한다.
    """
    registry = data / 'instances'
    if registry.is_symlink() or not registry.is_dir() or registry.resolve() != registry:
        raise PlatformError('instance registry missing')
    records, identities, disks = {}, set(), set()
    for directory in sorted(registry.iterdir()):
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
    def __init__(self, data=DATA, runtime=RUN, command=run, sleep=time.sleep, clock=time.monotonic):
        self.data, self.runtime = Path(data), Path(runtime)
        self.command, self.sleep, self.clock = command, sleep, clock

    def records(self, *, validate_assets=True):
        storage_check(self.data)
        return load_records(self.data, validate_assets=validate_assets)

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
                               'manifest_sha256': info['revision'], 'capabilities': zephyr.CAPABILITIES,
                               'hardware_profile': zephyr.PROFILE, 'logical_bytes': info['logical_bytes']})
        return result

    def create(self, args, records=None):
        """기존 Python 호출 지점도 공통 계약으로 위임한다. records 인자는 신뢰/재사용하지 않는다."""
        from zenova_management import Manager
        spec = {'name':args.id, 'template_id':getattr(args,'template_id',getattr(args,'template',None)),
                'template_version':getattr(args,'template_version',None),
                'vcpus':getattr(args,'vcpus',None), 'memory_mib':getattr(args,'memory_mib',None)}
        if getattr(args,'ipv4',None) is not None:
            spec['ipv4']=args.ipv4
        return Manager(self).call('create',spec,execute=getattr(args,'execute',False),
                                  idempotency_key=getattr(args,'idempotency_key',None))

    def reserve_space(self, additional_bytes, records):
        stats = os.statvfs(self.data)
        free = stats.f_bavail * stats.f_frsize
        growth = 0
        for key, r in records.items():
            if guest_kind(r) == 'linux':
                disk = self.data / 'instances' / key / 'root.raw'
                growth += max(0, disk.stat().st_size - disk.stat().st_blocks * 512)
        required = additional_bytes + growth + 2 * 1024 ** 3
        if free < required:
            raise PlatformError(f'insufficient storage: need {required} free bytes including existing VM growth and reserve, have {free}')
        return free, required

    def timed_command(self, args, deadline=None):
        if deadline is None:
            return self.command(args)
        remaining = deadline - self.clock()
        if remaining <= 0:
            raise PlatformError('guest operation deadline exceeded')
        return self.command(args, timeout=min(15, remaining))

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
        """종료 요청 후에만 사용한다. 디스크 설정 없이 현재 신원·상태를 읽는다.

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
            if domid in ids or name in names or uuid in uuids:
                raise PlatformError('duplicate xl list -v identity; removal not confirmed')
            ids.add(domid); names.add(name); uuids.add(uuid)
            rows[domid] = {'domid': domid, 'name': name, 'uuid': uuid,
                           'xen_state': xen_state(fields[4]), 'xen_state_flags': fields[4]}
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
                if any(row['domid'] == old[0] or row['uuid'] == old[1] or row['name'] == old[2]
                       for old in expected):
                    raise PlatformError('replacement domain appeared during shutdown; no command sent to replacement')
                if board:
                    raise PlatformError('unexpected domain appeared during shutdown; board shutdown refused')
            snapshots.append(rows)
        return snapshots

    def confirm_board_empty(self, deadline):
        self.removal_observation(set(), deadline, board=True)

    def check_xen(self):
        if 'control_d' not in Path('/proc/xen/capabilities').read_text().split():
            raise PlatformError('not Xen dom0: control_d capability absent')
        if not stat.S_ISCHR(Path('/dev/xen/privcmd').stat().st_mode):
            raise PlatformError('Xen privcmd control interface absent')
        self.command(['xl', 'info'])
        self.domains()
        if not any(line.split()[1] == '0' for line in self.command(['xl', 'list']).splitlines()[1:] if line.strip()):
            raise PlatformError('Domain-0 missing; VM start refused')

    def check_network(self, sysnet=Path('/sys/class/net')):
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
        links = json.loads(self.command(['ip', '-j', '-4', 'address', 'show', 'dev', 'xenbr0']))
        if not any(a.get('local') == '192.168.77.1' and a.get('prefixlen') == 24
                   for link in links for a in link.get('addr_info', [])):
            raise PlatformError('xenbr0 must have 192.168.77.1/24')

    def prepare_dhcp(self, records):
        self.command(['systemctl', 'start', 'zenova-dhcp.service'], timeout=30)
        self.check_network()
        self.command(['systemctl', 'is-active', '--quiet', 'zenova-dhcp.service'])
        ensure_runtime(self.runtime)
        hosts = ''.join(f"{r['mac']},{r['ipv4']},{r['hostname']},infinite\n" for r in records.values() if guest_kind(r) == 'linux')
        atomic_write(self.runtime / 'dhcp-hosts', hosts, 0o644)
        self.command(['systemctl', 'reload', 'zenova-dhcp.service'])

    def config(self, record):
        base = self.data / 'instances' / record['id']
        if guest_kind(record) == 'zephyr':
            return zephyr.config(record, base)
        # 문자열은 validate_record로 제한하고 생성 설정은 영구 원본과 분리한다.
        return '\n'.join([
            f"name = '{record['name']}'", f"uuid = '{record['uuid']}'", "type = 'pvh'",
            f"memory = {record['memory_mib']}", f"vcpus = {record['vcpus']}",
            f"kernel = '{base}/Image'", f"ramdisk = '{base}/initrd'",
            "extra = 'console=hvc0 root=/dev/xvda rw rootwait net.ifnames=0'",
            f"disk = [ 'format=raw,vdev=xvda,access=rw,backendtype=qdisk,target={base}/root.raw' ]",
            f"vif = [ 'mac={record['mac']},bridge=xenbr0,script=vif-bridge' ]",
            "on_poweroff = 'destroy'", "on_reboot = 'restart'", "on_crash = 'preserve'", '',
        ])

    def matching(self, record, domains):
        matching = []
        for d in domains:
            if d.get('domid') == 0:
                continue
            info = d['config']['c_info']
            if info.get('uuid') == record['uuid']:
                if info.get('name') != record['name']:
                    raise PlatformError('live UUID has different name')
                matching.append(d)
            elif info.get('name') == record['name']:
                raise PlatformError('live name has different UUID')
        if len(matching) > 1:
            raise PlatformError('duplicate live UUID')
        return matching

    def resolve_domain(self, record, *, expected=None, deadline=None):
        """영구 UUID/name을 현재 domid에 대조한다. domid는 저장하지 않는다.

        각 제어 호출 직전에 다시 조회한다. 외부 xl과 조회/실행 사이의 경쟁까지
        원자적으로 차단하지는 못하므로 이전 조회와 domid가 달라지면 거부한다.
        """
        rows = self.matching(record, self.domains(deadline=deadline))
        if not rows:
            raise PlatformError(f"{record['id']}: domain disappeared or instance is stopped")
        row = rows[0]
        if expected is not None and row['domid'] != expected['domid']:
            raise PlatformError(f"{record['id']}: domid changed before operation; refusing replacement domain")
        return row

    def control(self, record, action, expected, *, deadline=None):
        row = self.resolve_domain(record, expected=expected, deadline=deadline)
        self.timed_command(['xl', action, str(row['domid'])], deadline)
        return row

    def start(self, record):
        if (self.runtime / 'shutdown-requested').exists():
            raise PlatformError('board shutdown is in progress; VM start refused')
        self.command(['systemctl', 'is-active', '--quiet', 'zenova-vm-shutdown.service'])
        directory = self.data / 'instances' / record['id']
        validate_record(record, directory)
        domains = self.domains()
        existing = self.matching(record, domains)
        if existing:
            state = started_state(record, existing[0])
            print(f"{record['id']}: already {state}; no duplicate create; app_readiness=unknown")
            return state
        disk = directory / 'root.raw' if guest_kind(record) == 'linux' else None
        for d in domains:
            for item in d.get('config', {}).get('disks', []):
                path = item.get('pdev_path')
                if disk is not None and path and Path(path).exists() and os.path.samefile(path, disk):
                    raise PlatformError(f'{disk} is already attached to domain {d["domid"]}')
        ensure_runtime(self.runtime)
        config = self.runtime / (record['id'] + '.cfg')
        atomic_write(config, self.config(record))
        self.command(['xl', 'create', str(config)], timeout=45)
        created = self.matching(record, self.domains())
        if not created:
            raise PlatformError(f"{record['id']}: xl create returned without a live domain")
        state = started_state(record, created[0])
        print(f"{record['id']}: Xen domain created ({state}); guest OS/app readiness is not asserted")
        return state

    def autostart(self, records, required_ids):
        missing = [key for key in required_ids if key not in records]
        failures = ['default instances not provisioned: ' + ','.join(missing)] if missing else []
        results = {key: {'result': 'failed', 'error': 'default instance not provisioned'} for key in missing}
        results.update({key: {'result': 'pending'} for key in records})
        ensure_runtime(self.runtime)
        result_path = self.runtime / 'autostart-results.json'
        def save_results():
            atomic_write(result_path, json.dumps(results, indent=2) + '\n')
        save_results()
        for key, record in records.items():
            if not record['autostart']:
                results[key] = {'result': 'skipped', 'autostart': False}
                save_results()
                continue
            results[key] = {'result': 'starting'}
            save_results()
            try:
                if guest_kind(record) == 'linux':
                    self.prepare_dhcp(records)
                state = self.start(record)
                results[key] = {'result': state, 'xen_state': state, 'app_readiness': 'unknown'}
            except (PlatformError, OSError, ValueError, KeyError, TypeError) as exc:
                failures.append(f"{key}: {exc}")
                results[key] = {'result': 'failed', 'error': str(exc), 'app_readiness': 'unknown'}
                if isinstance(exc, DomainStateError):
                    results[key].update(xen_state=exc.xen_state, xen_state_flags=exc.xen_state_flags)
            save_results()
        print(json.dumps({'autostart_results': results}, indent=2))
        if failures:
            raise PlatformError('; '.join(failures))

    def stop_many(self, records, timeout=90, *, force=False, board=False, deadline=None):
        """사전 안전 검사와 요청 후 신원 관측을 분리한다. 자동 강제 종료는 없다."""
        ensure_runtime(self.runtime)
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
                if not rows:
                    result['already_stopped'].append(record['id'])
                    result['instances'][record['id']] = {'result': 'already_stopped', 'stage': 'domain-absent'}
                    continue
                row = rows[0]
                known.add(row['domid'])
                strategy = 'forced' if force else 'graceful'
                if guest_kind(record) == 'zephyr':
                    if not force and not (board and board_shutdown_policy(record) == 'force-allowed'):
                        raise PlatformError('Zephyr graceful shutdown unsupported; explicit --force or board force-allowed policy required: ' + record['id'])
                    strategy = 'forced'
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
        pending = []
        for record, row, strategy in planned:
            key = record['id']
            result['instances'][key] = {'result': 'pending', 'method': strategy, 'domid': row['domid'],
                                        'stage': 'identity-recheck'}
            save()
            try:
                self.control(record, 'destroy' if strategy == 'forced' else 'shutdown', row, deadline=deadline)
            except (PlatformError, OSError, ValueError, KeyError, TypeError) as exc:
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
                    result['stopped'].append(key)
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
                result['final_check_error'] = str(exc)
        os.sync()
        save()
        print(json.dumps(result, indent=2))
        failures = [f"{key}: {item.get('error', item['result'])}" for key, item in result['instances'].items()
                    if item['result'] in ('failed', 'timeout')]
        if result.get('final_check_error'):
            failures.append(result['final_check_error'])
        if failures:
            raise PlatformError('; '.join(failures))
        return result

    def prepare_board_shutdown(self, records, action, *, force=False):
        self._shutdown_deadline = self.clock() + 90
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
            # 내부 CLI는 marker로 새 기동을 막는다. 외부 xl과 원자적인 배제는 아니다.
            self.confirm_board_empty(self._shutdown_deadline)
            self.command(['systemctl', 'stop', 'zenova-vms.service', 'zenova-vm-shutdown.service', 'zenova-dhcp.service'], timeout=110)
            self.confirm_board_empty(self._shutdown_deadline)
            self.command(['systemctl', 'stop', 'srv-zenova.mount'], timeout=30)
            self.command(['systemctl', action])
        except (PlatformError, OSError, ValueError, KeyError, TypeError) as exc:
            (self.runtime / 'shutdown-requested').unlink(missing_ok=True)
            path = self.runtime / 'board-shutdown-result.json'
            result = json.loads(path.read_text())
            result['final_check_error'] = str(exc)
            atomic_write(path, json.dumps(result, indent=2) + '\n')
            raise

    def reboot(self, record, timeout=90, *, force=False):
        validate_record(record, self.data / 'instances' / record['id'])
        rows = self.matching(record, self.domains())
        if not rows:
            raise PlatformError('cannot reboot a stopped VM')
        if guest_kind(record) == 'zephyr' or force:
            if not force:
                raise PlatformError('Zephyr graceful reboot unsupported; use reboot --force')
            self.stop_many([record], timeout=timeout, force=True)
            self.start(record)
            print(json.dumps({'action': 'reboot', 'id': record['id'], 'forced': True, 'app_readiness': 'unknown'}))
            return
        previous = rows[0]['domid']
        self.control(record, 'reboot', rows[0])
        deadline = self.clock() + timeout
        while self.clock() < deadline:
            rows = self.matching(record, self.domains())
            if rows and rows[0]['domid'] != previous:
                started_state(record, rows[0])
                print('guest domain restarted with same UUID/disk; OS readiness not asserted')
                return
            self.sleep(1)
        raise PlatformError('guest reboot not observed before deadline; no force reset')


    def status(self, records):
        domains = self.domains()
        result = []
        for r in records.values():
            rows = self.matching(r, domains)
            try:
                validate_record(r, self.data / 'instances' / r['id'])
                asset_error = None
            except (PlatformError, OSError, ValueError, KeyError) as exc:
                asset_error = str(exc)
            result.append({**r, 'asset_error': asset_error, 'guest_kind': guest_kind(r), 'registered': True,
                           'xen_state': rows[0]['xen_state'] if rows else 'stopped',
                           'xen_state_flags': rows[0]['xen_state_flags'] if rows else None,
                           'domid': rows[0]['domid'] if rows else None, 'app_readiness': 'unknown',
                           'graceful_shutdown_supported': guest_kind(r) == 'linux',
                           'effective_board_shutdown_policy': board_shutdown_policy(r)})
        return {'instances': result, 'domains': domains, 'time': self.time_status()}

    def time_status(self):
        try:
            text = self.command(['timedatectl', 'show', '-p', 'NTPSynchronized', '-p', 'NTP', '-p', 'Timezone'])
            values = dict(row.split('=', 1) for row in text.splitlines() if '=' in row)
            return {'synchronized': (values['NTPSynchronized'] == 'yes') if values.get('NTPSynchronized') in ('yes', 'no') else None, 'details': values,
                    'interval_clock': 'monotonic'}
        except PlatformError as exc:
            return {'synchronized': None, 'error': str(exc), 'interval_clock': 'monotonic'}

    def replace_firmware(self, record, source, expected, *, execute=False):
        if guest_kind(record) != 'zephyr':
            raise PlatformError('firmware-replace requires a Zephyr instance')
        if self.matching(record, self.domains()):
            raise PlatformError('firmware replacement requires a stopped domain')
        directory = self.data / 'instances' / record['id']
        info = zephyr.bundle_info(source, expected)
        if not expected:
            raise PlatformError('--sha256 of firmware manifest is required')
        if execute:
            self.reserve_space(info['logical_bytes'] * 2, self.records(validate_assets=False))
            zephyr.replace_firmware(directory, record, source, expected)
        print(json.dumps({'action': 'firmware-replace' if execute else 'plan-firmware-replace',
                          'id': record['id'], 'firmware': info, 'uuid': record['uuid'],
                          'starts_guest': False}, indent=2))

    def delete(self, record, *, execute=False, idempotency_key=None):
        """delete는 공통 retire의 보관 별칭이며 영구 삭제를 수행하지 않는다."""
        from zenova_management import Manager
        return Manager(self).call('retire',{'vm_id':record['uuid']},execute=execute,
                                  idempotency_key=idempotency_key)


def retired_instance(data, key, identity=None):
    tombs = data / 'tombstones'
    if tombs.exists() or tombs.is_symlink():
        if tombs.is_symlink() or tombs.resolve() != tombs or not tombs.is_dir():
            raise PlatformError('invalid tombstone storage')
        for path in tombs.iterdir():
            regular_asset(tombs, path.name)
            tomb = json.loads(path.read_text())
            if tomb.get('name') == key and (identity is None or tomb.get('vm_id') == identity):
                return tomb.get('lifecycle') != 'REGISTERED'
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


def main(argv=None):
    raw = list(sys.argv[1:] if argv is None else argv)
    managed = {'list','get','retire','restore','purge','operation','create','delete','templates'}
    if '--json' in raw or (raw and raw[0] in managed):
        from zenova_management_cli import main as management_main
        return management_main(raw)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['create', 'templates', 'check-xen', 'check-network', 'dhcp-ready',
        'autostart', 'start', 'stop', 'reboot', 'shutdown-all', 'autostart-set', 'status', 'destroy',
        'poweroff', 'reboot-board', 'console', 'delete', 'firmware-replace', 'diagnose', 'config', 'board-shutdown-set'])
    parser.add_argument('id', nargs='?')
    parser.add_argument('value', nargs='?')
    parser.add_argument('--force', action='store_true', help='explicit immediate destruction; board/shutdown-all force applies to all guests including Linux')
    parser.add_argument('--execute', action='store_true', help='explicitly publish create/delete/firmware-replace; otherwise plan only')
    parser.add_argument('--template')
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--sha256', help='expected firmware manifest SHA-256')
    parser.add_argument('--ipv4')
    parser.add_argument('--memory-mib', type=int)
    parser.add_argument('--vcpus', type=int)
    parser.add_argument('--autostart', action='store_true', help='persist next-boot policy; no immediate start')
    args = parser.parse_args(argv)
    p = Platform()
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
        p.check_xen()
        return
    if args.action == 'diagnose':
        print(json.dumps({'time': p.time_status(), 'cmdline': Path('/proc/cmdline').read_text(),
                          'uptime': Path('/proc/uptime').read_text().split()[0]}, indent=2))
        return
    board_action, console_target = None, None
    with operation_lock(p.runtime):
        # 전체 신원/경로 검증과 실행 대상의 부팅 자산 검증은 수명을 달리한다.
        records = p.records(validate_assets=False)
        if ((p.runtime / 'shutdown-requested').exists()
                and args.action in ('create', 'delete', 'firmware-replace', 'autostart-set', 'board-shutdown-set')):
            raise PlatformError('board shutdown is in progress; registry mutation refused')
        if args.action == 'templates':
            print(json.dumps(p.templates(), indent=2))
        elif args.action == 'create':
            p.create(args, records)
        elif args.action == 'status':
            print(json.dumps(p.status(records), indent=2))
        elif args.action == 'autostart':
            p.check_xen()
            defaults = json.loads(Path('/etc/zenova/defaults.json').read_text())['instances']
            p.autostart(records, [r['id'] for r in defaults if not retired_instance(p.data, r['id'])])
        elif args.action in ('shutdown-all', 'poweroff', 'reboot-board'):
            if args.action == 'shutdown-all':
                p.stop_many(list(records.values()), force=args.force, board=True)
            else:
                board_action = 'reboot' if args.action == 'reboot-board' else 'poweroff'
                p.prepare_board_shutdown(list(records.values()), board_action, force=args.force)
        else:
            if args.id not in records:
                raise PlatformError('unknown instance id; create it explicitly')
            record = records[args.id]
            if args.action == 'start':
                p.check_xen()
                if guest_kind(record) == 'linux':
                    p.prepare_dhcp(records)
                p.start(record)
            elif args.action == 'stop':
                p.stop_many([record], force=args.force)
            elif args.action == 'reboot':
                p.check_xen()
                p.reboot(record, force=args.force)
            elif args.action == 'config':
                validate_record(record, p.data / 'instances' / record['id'])
                ensure_runtime(p.runtime)
                target = p.runtime / (record['id'] + '.cfg')
                atomic_write(target, p.config(record))
                print(target)
            elif args.action == 'console':
                row = p.resolve_domain(record)
                console_target = str(p.resolve_domain(record, expected=row)['domid'])
            elif args.action == 'firmware-replace':
                if args.bundle is None:
                    raise PlatformError('firmware-replace requires --bundle and --sha256')
                p.replace_firmware(record, args.bundle, args.sha256, execute=args.execute)
            elif args.action == 'delete':
                p.delete(record, execute=args.execute)
            elif args.action == 'autostart-set':
                if args.value not in ('on', 'off'):
                    raise PlatformError('autostart-set requires on or off')
                record['autostart'] = args.value == 'on'
                atomic_write(p.data / 'instances' / args.id / 'instance.json', json.dumps(record, indent=2) + '\n')
            elif args.action == 'board-shutdown-set':
                updated = {**record, 'board_shutdown_policy': args.value}
                validate_identity(updated)
                atomic_write(p.data / 'instances' / args.id / 'instance.json', json.dumps(updated, indent=2) + '\n')
                print(json.dumps({'id': args.id, 'board_shutdown_policy': board_shutdown_policy(updated), 'starts_guest': False}))
            elif args.action == 'destroy':
                if not args.force:
                    raise PlatformError('destroy requires --force; this is not graceful shutdown')
                p.stop_many([record], force=True)
    if console_target:
        # 직전 조회로 검증한 domid만 전달한다. 콘솔 세션 동안 작업 잠금은 보유하지 않는다.
        os.execvp('xl', ['xl', 'console', '-t', 'pv', console_target])
    if board_action:
        # ExecStop도 같은 잠금을 사용하므로 잠금 밖에서 서비스를 중지한다.
        p.finish_board_shutdown(board_action)


if __name__ == '__main__':
    try:
        main()
    except (PlatformError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f'[zenova] ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
