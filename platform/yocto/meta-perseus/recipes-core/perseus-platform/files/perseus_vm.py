#!/usr/bin/python3
"""영구 VM의 실행·정상 종료를 관리한다. 디스크 생성·복원·초기화는 하지 않는다.

registry가 유일한 설정 원본이며 xl 도메인은 실행 상태다. 생성은 별도 호스트 도구가
수행한다. python3-core와 표준 JSON 모듈, 기존 xl/ip/systemctl을 사용하며 외부 Python 라이브러리는 없다.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

DATA = Path('/srv/perseus')
RUN = Path('/run/perseus')
ID = re.compile(r'[a-z][a-z0-9-]{0,39}\Z')
UUID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z')
MAC = re.compile(r'02(?::[0-9a-f]{2}){5}\Z')


class PlatformError(RuntimeError):
    """숨기면 안 되는 플랫폼 준비·계약·생명주기 오류."""


def run(args, timeout=15):
    try:
        p = subprocess.run(args, text=True, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PlatformError(f'{args[0]} failed: {exc}') from exc
    if p.returncode:
        raise PlatformError(f'{args!r}: exit {p.returncode}: {p.stderr.strip()}')
    return p.stdout


def atomic_write(path, content, mode=0o600):
    """같은 디렉터리의 새 임시 파일을 fsync 후 교체하고 디렉터리도 동기화한다."""
    temp = path.with_name(path.name + '.tmp-' + str(os.getpid()) + '-' + os.urandom(6).hex())
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
        dfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    finally:
        if temp.exists():
            temp.unlink()


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
    if (data / '.perseus-install.json').exists():
        state = json.loads((data / '.first-install-state.json').read_text())
        if state.get('schema') != 1 or state.get('phase') != 'complete':
            raise PlatformError('first installation incomplete; VM start refused')
    marker = json.loads((data / '.perseus-storage.json').read_text())
    if marker.get('schema') != 1 or not UUID.fullmatch(marker.get('uuid', '')):
        raise PlatformError('invalid persistent storage marker')


def regular_asset(directory, filename):
    path = directory / filename
    if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
        raise PlatformError(f'missing, empty or linked VM asset: {path}')
    if path.resolve().parent != directory.resolve():
        raise PlatformError(f'VM asset escapes instance: {path}')
    return path


def validate_identity(record):
    for key in ('id', 'name', 'hostname'):
        if not ID.fullmatch(record.get(key, '')):
            raise PlatformError(f'invalid instance {key}')
    if record.get('schema') != 1:
        raise PlatformError('instance schema/id mismatch')
    if not UUID.fullmatch(record.get('uuid', '')) or not MAC.fullmatch(record.get('mac', '')):
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


def validate_record(record, directory, *, allow_seed=False):
    validate_identity(record)
    if record.get('identity_state') == 'pending' and not allow_seed:
        raise PlatformError('factory seed identity is pending; VM start refused')
    if record['id'] != directory.name:
        raise PlatformError('instance directory/id mismatch')
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


def load_records(data):
    """마운트를 이미 검증한 호출자용 registry 검증. 디스크 생성이나 상태 변경은 없다."""
    registry = data / 'instances'
    if registry.is_symlink() or not registry.is_dir():
        raise PlatformError('instance registry missing')
    records, identities, disks = {}, set(), set()
    for directory in sorted(registry.iterdir()):
        if directory.name.startswith('.'):
            continue
        if directory.is_symlink() or not directory.is_dir():
            raise PlatformError(f'invalid registry entry: {directory}')
        r = validate_record(json.loads((directory / 'instance.json').read_text()), directory)
        for key in ('id', 'name', 'hostname', 'uuid', 'mac', 'ipv4'):
            identity = (key, r[key])
            if identity in identities:
                raise PlatformError(f'duplicate VM {key}: {r[key]}')
            identities.add(identity)
        st = (directory / 'root.raw').stat()
        inode = (st.st_dev, st.st_ino)
        if inode in disks:
            raise PlatformError('same writable disk is registered more than once')
        disks.add(inode)
        template_disk = data / 'templates' / r['template'] / 'root.raw'
        if template_disk.exists() and os.path.samefile(directory / 'root.raw', template_disk):
            raise PlatformError('instance disk aliases immutable template')
        records[r['id']] = r
    return records


class Platform:
    def __init__(self, data=DATA, runtime=RUN, command=run, sleep=time.sleep, clock=time.monotonic):
        self.data, self.runtime = Path(data), Path(runtime)
        self.command, self.sleep, self.clock = command, sleep, clock

    def records(self):
        storage_check(self.data)
        return load_records(self.data)

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
            result.append({'id': item.name, 'logical_bytes': root.stat().st_size, 'source': meta['source'], 'root_sha256': meta['root_sha256']})
        return result

    def create(self, args, records):
        """명시적 최초 생성만 공유 준비 코드를 호출한다. start/stop은 이 경로를 사용하지 않는다."""
        if args.id in records or not args.id or not ID.fullmatch(args.id):
            raise PlatformError('new valid instance id required; existing instances are never overwritten')
        available = {r['id']: r for r in self.templates()}
        if args.template not in available:
            raise PlatformError('local template missing/unsupported; no PC fallback or download')
        template = self.data / 'templates' / args.template
        boot = json.loads(regular_asset(template, 'boot.json').read_text())
        mac = '02:' + ':'.join(f'{value:02x}' for value in os.urandom(5))
        record = {'schema': 1, 'id': args.id, 'name': args.id, 'hostname': args.id, 'template': args.template,
                  'uuid': '00000000-0000-4000-8000-000000000000', 'mac': mac, 'ipv4': args.ipv4,
                  'kernel_release': boot['kernel_release'], 'memory_mib': args.memory_mib,
                  'vcpus': args.vcpus, 'autostart': args.autostart}
        validate_identity(record)
        for existing in records.values():
            if any(existing[k] == record[k] for k in ('id', 'name', 'hostname', 'mac', 'ipv4')):
                raise PlatformError('new identity/IP conflicts with a registered instance')
        stats = os.statvfs(self.data)
        free = stats.f_bavail * stats.f_frsize
        pending_growth = sum(max(0, (self.data / 'instances' / key / 'root.raw').stat().st_size
                                 - (self.data / 'instances' / key / 'root.raw').stat().st_blocks * 512) for key in records)
        required = available[args.template]['logical_bytes'] + pending_growth + 2 * 1024 ** 3
        if free < required:
            raise PlatformError(f'insufficient storage: need {required} free bytes including existing VM growth and reserve, have {free}')
        print(json.dumps({'action': 'create' if args.execute else 'plan-create', 'record': record,
                          'free_bytes': free, 'required_free_bytes': required, 'starts_guest': False}, indent=2))
        if not args.execute:
            return
        from prepare_instance import make_instance
        from argparse import Namespace
        make_instance(Namespace(data_root=self.data, id=args.id, spec=None, template_root=None, deployment_seed=False),
                      vm=sys.modules[__name__], defaults_path=Path('/etc/perseus/defaults.json'), record_override=record)
        self.records()
        print('new persistent instance registered; use start explicitly; immutable template retained')

    def domains(self):
        rows = json.loads(self.command(['xl', 'list', '-l']))
        if not isinstance(rows, list):
            raise PlatformError('xl list -l returned unexpected schema')
        # xl long list는 설정 조회 실패 도메인을 생략하므로 짧은 목록과 대조한다.
        short = self.command(['xl', 'list']).splitlines()
        ids = {int(line.split()[1]) for line in short[1:] if line.strip()}
        if (ids - {0}) != {row['domid'] for row in rows if row['domid'] != 0}:
            raise PlatformError('xl omitted live domain configuration; writable-disk safety cannot be checked')
        for row in rows:
            if row.get('domid') == 0:
                continue
            config = row.get('config', {})
            if not config.get('c_info', {}).get('uuid') or 'disks' not in config:
                raise PlatformError('cannot inspect live domain UUID/disks; refusing unsafe action')
        return rows

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
        self.check_network()
        self.command(['systemctl', 'is-active', '--quiet', 'perseus-dhcp.service'])
        self.runtime.mkdir(mode=0o755, exist_ok=True)
        hosts = ''.join(f"{r['mac']},{r['ipv4']},{r['hostname']},infinite\n" for r in records.values())
        atomic_write(self.runtime / 'dhcp-hosts', hosts, 0o644)
        self.command(['systemctl', 'reload', 'perseus-dhcp.service'])

    def config(self, record):
        base = self.data / 'instances' / record['id']
        # 문자열은 validate_record로 제한하고 생성 설정은 영구 원본과 분리한다.
        return '\n'.join([
            f"name = '{record['name']}'", f"uuid = '{record['uuid']}'", "type = 'pvh'",
            f"memory = {record['memory_mib']}", f"vcpus = {record['vcpus']}",
            f"kernel = '{base}/Image'", f"ramdisk = '{base}/initrd'",
            "extra = 'console=hvc0 root=/dev/xvda rw rootwait net.ifnames=0'",
            f"disk = [ 'target={base}/root.raw,format=raw,vdev=xvda,access=rw,backendtype=qdisk' ]",
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

    def start(self, record):
        if (self.runtime / 'shutdown-requested').exists():
            raise PlatformError('board shutdown is in progress; VM start refused')
        self.command(['systemctl', 'is-active', '--quiet', 'perseus-vm-shutdown.service'])
        domains = self.domains()
        if self.matching(record, domains):
            print(f"{record['id']}: already running; no duplicate create")
            return
        disk = self.data / 'instances' / record['id'] / 'root.raw'
        for d in domains:
            for item in d.get('config', {}).get('disks', []):
                path = item.get('pdev_path')
                if path and Path(path).exists() and os.path.samefile(path, disk):
                    raise PlatformError(f'{disk} is already attached to domain {d["domid"]}')
        self.runtime.mkdir(mode=0o755, exist_ok=True)
        config = self.runtime / (record['id'] + '.cfg')
        atomic_write(config, self.config(record))
        self.command(['xl', 'create', str(config)], timeout=45)
        if not self.matching(record, self.domains()):
            raise PlatformError(f"{record['id']}: xl create returned without a live domain")
        print(f"{record['id']}: Xen domain created; guest OS/app readiness is not asserted")

    def autostart(self, records, required_ids):
        missing = [key for key in required_ids if key not in records]
        if missing:
            raise PlatformError('default instances not provisioned: ' + ','.join(missing))
        self.prepare_dhcp(records)
        failures = []
        for record in records.values():
            if record['autostart']:
                try:
                    self.start(record)
                except PlatformError as exc:
                    failures.append(str(exc))
        if failures:
            raise PlatformError('; '.join(failures))

    def stop_many(self, records, timeout=90):
        """모두에게 정상 종료 요청 후 공통 기한까지 대기. 강제 제거하지 않는다."""
        domains = self.domains()
        pending = [r for r in records if self.matching(r, domains)]
        for record in pending:
            self.command(['xl', 'shutdown', record['uuid']])
        deadline = self.clock() + timeout
        while pending:
            domains = self.domains()
            pending = [r for r in pending if self.matching(r, domains)]
            if not pending:
                break
            if self.clock() >= deadline:
                raise PlatformError('graceful shutdown timed out; disks retained, no destroy: ' + ','.join(r['id'] for r in pending))
            self.sleep(1)
        os.sync()
        print('requested guests stopped normally; writes synchronized')

    def prepare_board_shutdown(self, records, action):
        self.stop_many(records)
        self.runtime.mkdir(exist_ok=True)
        atomic_write(self.runtime / 'shutdown-requested', action + '\n')

    def finish_board_shutdown(self, action):
        try:
            self.command(['systemctl', 'stop', 'perseus-vms.service', 'perseus-vm-shutdown.service'], timeout=110)
            self.command(['systemctl', 'stop', 'srv-perseus.mount'], timeout=30)
            self.command(['systemctl', action])
        except PlatformError:
            (self.runtime / 'shutdown-requested').unlink(missing_ok=True)
            raise

    def reboot(self, record, timeout=90):
        rows = self.matching(record, self.domains())
        if not rows:
            raise PlatformError('cannot reboot a stopped VM')
        previous = rows[0]['domid']
        self.command(['xl', 'reboot', record['uuid']])
        deadline = self.clock() + timeout
        while self.clock() < deadline:
            rows = self.matching(record, self.domains())
            if rows and rows[0]['domid'] != previous:
                print('guest domain restarted with same UUID/disk; OS readiness not asserted')
                return
            self.sleep(1)
        raise PlatformError('guest reboot not observed before deadline; no force reset')


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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['create', 'templates', 'check-xen', 'check-network', 'dhcp-ready', 'autostart', 'start', 'stop', 'reboot', 'shutdown-all', 'autostart-set', 'status', 'destroy', 'poweroff', 'reboot-board'])
    parser.add_argument('id', nargs='?')
    parser.add_argument('value', nargs='?')
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--execute', action='store_true', help='create only: explicitly allow new disk/identity creation')
    parser.add_argument('--template')
    parser.add_argument('--ipv4')
    parser.add_argument('--memory-mib', type=int, default=512)
    parser.add_argument('--vcpus', type=int, default=1)
    parser.add_argument('--autostart', action='store_true', help='new instance: start on the next platform boot, not immediately')
    args = parser.parse_args(argv)
    p = Platform()
    if args.action == 'check-network':
        p.check_network()
        RUN.mkdir(mode=0o755, exist_ok=True)
        hosts = RUN / 'dhcp-hosts'
        if not hosts.exists():
            atomic_write(hosts, '', 0o644)
        return
    if args.action == 'dhcp-ready':
        dhcp_ready(int(args.id))
        return
    if args.action == 'check-xen':
        p.check_xen()
        return
    lockdir = Path('/run/lock')
    lockdir.mkdir(exist_ok=True)
    board_action = None
    with (lockdir / 'perseus-vm.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PlatformError('another VM operation is in progress') from exc
        p.check_xen()
        records = p.records()
        if args.action == 'templates':
            print(json.dumps(p.templates(), indent=2))
        elif args.action == 'create':
            p.create(args, records)
        elif args.action == 'status':
            print(json.dumps({'instances': list(records.values()), 'domains': p.domains()}, indent=2))
        elif args.action == 'autostart':
            defaults = json.loads(Path('/etc/perseus/defaults.json').read_text())['instances']
            p.autostart(records, [r['id'] for r in defaults])
        elif args.action in ('shutdown-all', 'poweroff', 'reboot-board'):
            if args.action == 'shutdown-all':
                p.stop_many(list(records.values()))
            else:
                board_action = 'reboot' if args.action == 'reboot-board' else 'poweroff'
                p.prepare_board_shutdown(list(records.values()), board_action)
        else:
            if args.id not in records:
                raise PlatformError('unknown instance id; create it explicitly with the host provisioning tool')
            record = records[args.id]
            if args.action == 'start':
                p.prepare_dhcp(records)
                p.start(record)
            elif args.action == 'stop':
                p.stop_many([record])
            elif args.action == 'reboot':
                p.reboot(record)
            elif args.action == 'autostart-set':
                if args.value not in ('on', 'off'):
                    raise PlatformError('autostart-set requires on or off')
                record['autostart'] = args.value == 'on'
                atomic_write(DATA / 'instances' / args.id / 'instance.json', json.dumps(record, indent=2) + '\n')
            elif args.action == 'destroy':
                if not args.force:
                    raise PlatformError('destroy requires --force; this is not graceful shutdown')
                if p.matching(record, p.domains()):
                    p.command(['xl', 'destroy', record['uuid']])
                    print('FORCED domain removal; graceful shutdown/data consistency NOT guaranteed')


    if board_action:
        # ExecStop도 같은 잠금을 사용하므로 잠금 밖에서 서비스를 중지한다.
        p.finish_board_shutdown(board_action)


if __name__ == '__main__':
    try:
        main()
    except (PlatformError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f'[perseus] ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
