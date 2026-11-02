#!/usr/bin/python3
"""공통 SD의 검증·마지막 ext4 확장·최초 VM 등록. 포맷/VM 생성/xl 실행/암호 변경은 하지 않는다."""
import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

from first_install_contract import FEATURES, LEGACY_FEATURES, SECTOR, growth, layout, uuid4
from xenova_vm import (PlatformError, atomic_write, load_records, validate_identity, validate_record,
                        guest_kind, has_network, identity_fields, retired_instance)
from xenova_runtime import operation_lock
import xenova_zephyr as zephyr


def invoke(args, *, input=None, allow=(0,), timeout=120):
    result = subprocess.run([str(a) for a in args], input=input, text=True, capture_output=True, timeout=timeout, check=False,
                            env={**os.environ, 'LC_ALL': 'C'})
    if result.returncode not in allow:
        raise ValueError(f'{args!r}: exit {result.returncode}: {result.stderr.strip()}')
    return result.stdout


def new_mac():
    return '02:' + ':'.join(f'{b:02x}' for b in os.urandom(5))


def read_json(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f'missing/linked JSON asset: {path}')
    return json.loads(path.read_text())


def plain(directory):
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError(f'missing/linked asset directory: {directory}')


def asset(directory, name):
    plain(directory)
    p = directory / name
    if p.is_symlink() or not p.is_file() or not p.stat().st_size:
        raise ValueError(f'missing/linked/empty asset: {p}')
    return p


class FirstBoot:
    def __init__(self, root=Path('/'), command=invoke, *, block_devices=True):
        self.root, self.command = Path(root), command
        self.data, self.run = self.root / 'srv/xenova', self.root / 'run/xenova'
        self.block_devices = block_devices  # fixture 주입 전용. CLI로 해제할 수 없다.

    def path(self, relative):
        return self.root / relative

    def save(self, path, value):
        if path.exists() and read_json(path) == value:
            return
        atomic_write(path, json.dumps(value, sort_keys=True, indent=2) + '\n')

    def mounts(self):
        return [line.split(' - ', 1)[0].split() for line in self.path('proc/self/mountinfo').read_text().splitlines()]

    def mbr(self, disk, total=None):
        with disk.open('rb') as stream:
            return layout(stream.read(SECTOR), total, with_data=True)

    def discover(self):
        """현재 /의 major:minor → sysfs partition 2 → 같은 부모의 partition 3만 선택한다."""
        rows = [fields for fields in self.mounts() if fields[4] == '/']
        if len(rows) != 1:
            raise ValueError('root mount is not uniquely identifiable')
        node = self.path('sys/dev/block') / rows[0][2]
        resolved = node.resolve(strict=True)
        if not resolved.is_relative_to(self.path('sys/devices').resolve()) or (resolved / 'partition').read_text().strip() != '2':
            raise ValueError('root must be a real sysfs partition 2')
        parent = resolved.parent
        children = [p for p in parent.iterdir() if (p / 'partition').is_file() and (p / 'partition').read_text().strip() == '3']
        if len(children) != 1:
            raise ValueError('boot disk must have exactly one data partition 3')
        data_node = children[0]
        for name in (parent.name, resolved.name, data_node.name):
            if not re.fullmatch('[A-Za-z0-9_-]+', name):
                raise ValueError('unsafe sysfs device name')
        disk, root, data = [self.path('dev') / p.name for p in (parent, resolved, data_node)]
        if self.block_devices:
            for _ in range(100):
                if all(p.exists() for p in (disk, root, data)):
                    break
                time.sleep(0.2)
            if not all(stat.S_ISBLK(p.stat().st_mode) for p in (disk, root, data)):
                raise ValueError('boot disk/partitions must be block devices')
        if any(fields[2] == (data_node / 'dev').read_text().strip() for fields in self.mounts()):
            raise ValueError('data partition is already mounted; offline expansion refused')
        total = int(self.command(['blockdev', '--getsize64', disk]))
        if self.command(['blockdev', '--getss', disk]).strip() != '512':
            raise ValueError('only 512-byte-sector DOS media are supported')
        current = self.mbr(disk, total)
        expected = current['disk_id'] + '-02'
        actual = self.command(['blkid', '-c', '/dev/null', '-s', 'PARTUUID', '-o', 'value', root]).strip().lower()
        roots = [token[5:] for token in self.path('proc/cmdline').read_text().split() if token.startswith('root=')]
        if actual != expected or roots != ['PARTUUID=' + expected]:
            raise ValueError('running root and U-Boot PARTUUID reference disagree')
        # 공통 이미지는 PARTUUID를 유지한다. 다른 연결 매체에 같은 root UUID가 있으면 추측하지 않는다.
        block_class = self.path('sys/class/block')
        if not block_class.is_dir():
            raise ValueError('cannot verify boot PARTUUID uniqueness')
        for candidate in block_class.iterdir():
            if not (candidate / 'partition').is_file() or (candidate / 'partition').read_text().strip() != '2' or candidate.name == root.name:
                continue
            if not re.fullmatch('[A-Za-z0-9_-]+', candidate.name):
                raise ValueError('unsafe block device name')
            other = self.path('dev') / candidate.name
            identifier = self.command(['blkid', '-c', '/dev/null', '-s', 'PARTUUID', '-o', 'value', other], allow=(0, 2)).strip().lower()
            if identifier == expected:
                raise ValueError('ambiguous duplicate boot PARTUUID on another medium; no partition is changed')
        info = self.command(['blkid', '-p', '-o', 'export', data])
        values = dict(line.split('=', 1) for line in info.splitlines() if '=' in line)
        if values.get('TYPE') != 'ext4' or values.get('LABEL') != 'xenova-data' or not values.get('UUID'):
            raise ValueError('partition 3 must already be ext4 labelled xenova-data; never formatted here')
        return {'disk': str(disk), 'root': str(root), 'data': str(data), 'data_dev': (data_node / 'dev').read_text().strip(),
                'device_bytes': total, 'layout': current, 'filesystem_uuid': values['UUID']}

    def fs_bytes(self, device):
        text = self.command(['dumpe2fs', '-h', device])
        counts = re.findall(r'^Block count:\s+(\d+)\s*$', text, re.M)
        sizes = re.findall(r'^Block size:\s+(\d+)\s*$', text, re.M)
        if len(counts) != 1 or len(sizes) != 1 or int(sizes[0]) not in (1024, 2048, 4096):
            raise ValueError('cannot verify ext4 size')
        return int(counts[0]) * int(sizes[0]), int(sizes[0])

    def grow(self):
        info = self.discover()
        disk, data = Path(info['disk']), Path(info['data'])
        before = info['layout']
        target = growth(before, info['device_bytes'])
        if target['grow_partition']:
            self.command(['sfdisk', '--no-reread', '--no-tell-kernel', '--wipe', 'never', '--wipe-partitions', 'never', '-N', '3', disk],
                         input=f"start={target['start']}, size={target['size']}, type=83\n")
        after = self.mbr(disk, info['device_bytes'])
        if (after['disk_id'] != before['disk_id'] or after['partitions'][:2] != before['partitions'][:2]
                or after['partitions'][2] != {**before['partitions'][2], 'size': target['size']}):
            raise ValueError('partition update differs from the permitted last-partition growth')
        wanted = target['filesystem_max_bytes']
        # 중단으로 파티션 표만 커진 상태도 커널 크기부터 다시 확인한다.
        if int(self.command(['blockdev', '--getsize64', data])) != wanted:
            self.command(['partx', '--update', '--nr', '3', disk])
            for _ in range(20):
                if int(self.command(['blockdev', '--getsize64', data])) == wanted:
                    break
                time.sleep(0.2)
            else:
                raise ValueError('kernel data-partition size not updated; retry after reboot, no formatting')
        fs_size, block_size = self.fs_bytes(data)
        expected = wanted // block_size * block_size
        if fs_size > expected:
            raise ValueError('filesystem exceeds device size; shrinking is forbidden')
        if fs_size < expected:
            self.command(['e2fsck', '-p', '-f', data], allow=(0, 1))
            self.command(['resize2fs', data])
        if self.fs_bytes(data)[0] != expected:
            raise ValueError('ext4 growth did not reach the verified partition size')
        final = dict(line.split('=', 1) for line in self.command(['blkid', '-p', '-o', 'export', data]).splitlines() if '=' in line)
        if final.get('UUID') != info['filesystem_uuid'] or final.get('LABEL') != 'xenova-data':
            raise ValueError('filesystem identity changed during growth')
        self.run.mkdir(parents=True, exist_ok=True)
        plain(self.run)
        info['layout'], info['filesystem_bytes'] = after, expected
        self.save(self.run / 'boot-storage.json', info)
        link = self.run / 'data-device'
        if link.exists() or link.is_symlink():
            if not link.is_symlink() or link.readlink() != data:
                raise ValueError('runtime data-device was replaced by an unexpected path')
        else:
            link.symlink_to(data)
        print('storage verified; last ext4 partition uses actual device bytes; VM disks unchanged')

    def verify_mount(self):
        info = read_json(self.run / 'boot-storage.json')
        rows = [fields for fields in self.mounts() if fields[4] == '/srv/xenova']
        if len(rows) != 1 or rows[0][2] != info['data_dev'] or 'rw' not in rows[0][5].split(','):
            raise ValueError('storage is not the verified writable boot-disk data partition')
        plain(self.data)
        return info

    def verify_bundle(self, directory, spec):
        if guest_kind(spec['record']) == 'zephyr':
            current = read_json(directory / 'instance.json')
            # 현재 참조를 검증한다. 합법적인 교체를 factory 버전으로 되돌리지 않는다.
            if current.get('creation_origin') != spec['record'].get('creation_origin'):
                raise ValueError('Zephyr creation origin differs from factory manifest')
            zephyr.validate_instance(current, directory)
            return
        for name in ('root.raw', 'Image', 'initrd', 'boot.json', 'instance.json', 'kernel.config'):
            asset(directory, name)
        if (directory / 'root.raw').stat().st_size != spec['root_bytes']:
            raise ValueError('root disk size differs from factory manifest; no replacement created')
        for name, key in [('Image', 'kernel_sha256'), ('initrd', 'initrd_sha256'), ('kernel.config', 'config_sha256')]:
            if self.command(['sha256sum', directory / name]).split()[0] != spec['boot'][key]:
                raise ValueError(f'factory boot asset mismatch: {name}')

    def supply(self,record,common):
        """UUID 계획 뒤 대상만 보호한다. 해시/guest I/O 동안 공통 관리 잠금은 반환한다."""
        from contextlib import nullcontext
        from xenova_vm import Platform
        from xenova_preparation import request_output,AssetLease
        from xenova_provisioning import ensure,apply_stopped_linux
        platform=Platform(self.data,self.run)
        path=self.data/'instances'/record['id']/'instance.json'
        progress=self.data/'.first-install-state.json';before=read_json(progress)
        with request_output(),AssetLease(platform,record['uuid'],'provision',None) as lease:
            lease.guard()
            with common.outside(platform) if common is not None else nullcontext():
                if guest_kind(record)=='linux':apply_stopped_linux(platform,record,factory=True)
                else:ensure(self.data,record)
            lease.guard()
            if read_json(path)!=record or read_json(progress)!=before:
                raise ValueError('factory registration target changed during supply')

    def register(self,*,common=None):
        self.verify_mount()
        factory = read_json(self.data / '.xenova-install.json')
        if factory.get('features') not in (FEATURES, LEGACY_FEATURES) or factory.get('schema') != 1:
            raise ValueError('unsupported factory manifest/capabilities')
        specs = factory['instances']
        firmware_specs = factory.get('firmware_instances', [])
        if not isinstance(specs, list) or not isinstance(firmware_specs, list):
            raise ValueError('invalid factory instance specification')
        linux_ids = [s.get('record', {}).get('id') for s in specs]
        firmware_ids = [s.get('record', {}).get('id') for s in firmware_specs]
        current = (linux_ids == ['debian-dev'] and len(firmware_ids) == 2
                   and set(firmware_ids) == {'zephyr-drive-a', 'zephyr-drive-b'})
        legacy = (len(linux_ids) == 2 and set(linux_ids) == {'ubuntu-dev', 'debian-dev'}
                  and len(firmware_ids) <= 1 and set(firmware_ids).issubset({'zephyr-dev'}))
        if (not (current or legacy) or any(guest_kind(s['record']) != 'linux' for s in specs)
                or any(guest_kind(s['record']) != 'zephyr' for s in firmware_specs)):
            raise ValueError('invalid factory deployment: expected Debian and two independent Zephyr seeds')
        if factory['features'] == FEATURES and not firmware_specs:
            raise ValueError('factory contract requires Zephyr firmware seeds')
        if current and (specs[0]['record'].get('template') != 'debian-13-20261015'
                        or specs[0]['record'].get('template_id') != 'debian-13-arm64'
                        or any(s['record'].get('template_id') not in ('zephyr-xenvm-starter-arm64','zephyr-drive-lattice-lab-arm64') for s in firmware_specs)):
            raise ValueError('current factory requires verified Debian and supported Zephyr template profiles')
        for item in firmware_specs:
            r=item['record']
            if r.get('template_id')=='zephyr-drive-lattice-lab-arm64' and (
                    r.get('template')!='xenova-zephyr-drive-lattice-lab-1.0.0'
                    or r.get('provisioning_profile',{}).get('apps')!={
                        'autonomous':{'model':'stateful','standby_progress':'idle'},
                        'manual':{'model':'stateless','standby_progress':'idle'}}):
                raise ValueError('factory drive requires the actual bounded lattice/warm profile')
        if current and len({(s['record'].get('template'), s['record'].get('template_id'),
                             s['record'].get('template_version'), s['record'].get('creation_origin', {}).get('manifest_sha256'))
                            for s in firmware_specs}) != 1:
            raise ValueError('drive A/B must originate from the same firmware template')
        specs = specs + firmware_specs
        for directory in ('instances', 'seeds', 'templates'):
            plain(self.data / directory)
        templates = factory.get('templates', [])
        required_templates = {s['record']['template'] for s in factory['instances']}
        if current:
            required_templates.add('ubuntu-24.04-20260926')
        if len(templates) != 2 or {t['id'] for t in templates} != required_templates:
            raise ValueError('both immutable local templates must be retained')
        for item in templates:
            if not re.fullmatch('[a-z0-9][a-z0-9.+_-]{0,99}', item['id']):
                raise ValueError('invalid local template path')
            directory = self.data / 'templates' / item['id']
            plain(directory)
            meta = read_json(directory / 'template.json')
            root = asset(directory, 'root.raw')
            if meta != item['metadata'] or root.stat().st_size != item['root_bytes'] or root.stat().st_mode & 0o222:
                raise ValueError('immutable template metadata/size/permissions mismatch')
            template_boot = read_json(directory / 'boot.json')
            for name, field in [('Image', 'kernel_sha256'), ('initrd', 'initrd_sha256'), ('kernel.config', 'config_sha256')]:
                if self.command(['sha256sum', asset(directory, name)]).split()[0] != template_boot[field]:
                    raise ValueError('local template boot asset mismatch')
        mid = self.path('etc/machine-id').read_text().strip()
        if not re.fullmatch('[0-9a-f]{32}', mid) or mid == '0' * 32:
            raise ValueError('PID1 has not established dom0 machine-id; it is never changed here')
        state_path = self.data / '.first-install-state.json'
        if state_path.exists():
            state = read_json(state_path)
            if state.get('factory_id') != factory['factory_id'] or state.get('dom0_machine_id') != mid or state.get('schema') != 1:
                raise ValueError('existing storage belongs to another install; refusing reinitialization')
        else:
            if (self.data / '.xenova-storage.json').exists() or any((self.data / 'instances').iterdir()):
                raise ValueError('existing data without factory progress cannot be adopted or reset')
            identities, seen = {}, set()
            for spec in specs:
                key = spec['record']['id']
                identities[key] = {'uuid': uuid4()}
                if has_network(spec['record']):
                    mac = new_mac()
                    while mac in seen:
                        mac = new_mac()
                    seen.add(mac)
                    identities[key]['mac'] = mac
            state = {'schema': 1, 'phase': 'identities-planned', 'factory_id': factory['factory_id'],
                     'dom0_machine_id': mid, 'storage_uuid': uuid4(), 'identities': identities, 'registered': []}
            from xenova_cpu import factory_activation
            activation = factory_activation(identities, self.root/'etc/xenova/cpu-policy-preset.json')
            if activation:
                state['cpu_policy_activation'] = activation
            self.save(state_path, state)
        if set(state.get('identities', {})) != {s['record']['id'] for s in specs}:
            raise ValueError('progress has an incomplete identity plan')
        if (not isinstance(state.get('registered'), list) or len(state['registered']) != len(set(state['registered']))
                or not set(state['registered']).issubset(state['identities'])):
            raise ValueError('invalid progress registration list')
        seen_identities = set()
        for spec in specs:
            planned = {**spec['record'], **state['identities'][spec['record']['id']], 'identity_state': 'ready'}
            validate_identity(planned)
            for field in identity_fields(planned):
                item = (field, planned[field])
                if item in seen_identities:
                    raise ValueError('duplicate identity in factory progress')
                seen_identities.add(item)
        completed = state['phase'] == 'complete'
        if getattr(self, 'display_progress', None):
            self.display_mode = 'existing-install-verification' if completed else 'initial-registration-resume'
            self.display_progress('asset-verification', mode=self.display_mode)
        asset_errors = {}
        inodes = set()
        for spec in specs:
            key = spec['record']['id']
            if not re.fullmatch('[a-z][a-z0-9-]{0,39}', key):
                raise ValueError('invalid factory instance path')
            seed, target = self.data / 'seeds' / key, self.data / 'instances' / key
            expected = {**spec['record'], **state['identities'][key], 'identity_state': 'ready'}
            from xenova_preparation import guard_asset
            guard_asset(argparse.Namespace(runtime=self.run), expected['uuid'])
            if (not target.exists() and not target.is_symlink() and state['phase'] == 'complete'
                    and retired_instance(self.data, key, expected['uuid'])):
                continue
            if target.exists() or target.is_symlink():
                plain(target)
                if seed.exists() or seed.is_symlink():
                    raise ValueError('both seed and live instance exist; refusing ambiguous recovery')
                current = read_json(target / 'instance.json')
                fields = (('id', 'uuid', 'name', 'template', 'creation_origin') + (('mac','ipv4','hostname') if has_network(expected) else ()) if guest_kind(expected) == 'zephyr'
                          else ('id', 'uuid', 'mac', 'name', 'hostname', 'ipv4', 'template', 'kernel_release'))
                if any(current.get(k) != expected[k] for k in fields):
                    raise ValueError('live identity differs from planned identity; no reset')
            else:
                if state['phase'] == 'complete':
                    raise ValueError('completed instance missing; never recreated from a seed')
                self.verify_bundle(seed, spec)
                current = read_json(seed / 'instance.json')
                if current not in (spec['record'], expected):
                    raise ValueError('seed metadata changed outside resumable preparation')
                self.save(seed / 'instance.json', expected)
                os.rename(seed, target)
                os.sync()
            current = read_json(target / 'instance.json')
            try:
                self.verify_bundle(target, spec)
                validate_record(current, target)
            except (PlatformError, OSError, ValueError, KeyError) as exc:
                if not completed:
                    raise
                # 완료된 등록의 자산 오류는 개별 start가 실패하게 한다. 다른 VM의 기동은 계속한다.
                asset_errors[key] = str(exc)
                continue
            if guest_kind(current) == 'linux':
                disk = (target / 'root.raw').stat()
                inode = (disk.st_dev, disk.st_ino)
                if inode in inodes:
                    raise ValueError('factory VMs share a writable disk')
                inodes.add(inode)
            if key not in state['registered']:
                if current.get('provisioning_profile',{}).get('channels'):
                    self.supply(current,common)
                state['registered'].append(key)
                state['phase'] = 'registering'
                self.save(state_path, state)
        marker = self.data / '.xenova-storage.json'
        desired = {'schema': 1, 'uuid': state['storage_uuid']}
        if marker.exists():
            if read_json(marker) != desired:
                raise ValueError('storage identity mismatch; never overwritten')
        else:
            self.save(marker, desired)
        # 완료 표식보다 먼저 전체 registry의 중복/boot 계약을 확인한다.
        load_records(self.data, validate_assets=not completed)
        if not completed:
            from xenova_vm import Platform
            execution = Platform(self.data, self.run).execution()
            for spec in specs:
                current = read_json(self.data/'instances'/spec['record']['id']/'instance.json')
                execution.initialize(current, 'running', 'factory')
        notices=[]
        if not completed:
            consumed=state.setdefault('registration_notices',[])
            for spec in specs:
                current=read_json(self.data/'instances'/spec['record']['id']/'instance.json')
                if current['uuid'] not in consumed:
                    consumed.append(current['uuid']);notices.append(current)
        state['phase'] = 'complete'
        self.save(state_path, state)
        self.save(self.run / 'install-ready.json', {'factory_id': factory['factory_id'], 'dom0_machine_id': mid,
                                                 'asset_errors': asset_errors})
        if asset_errors:
            print(json.dumps({'registration': 'complete', 'asset_errors': asset_errors}))
        print('installation verified; hand off to existing VM autostart service')
        # 부팅은 HTTP 응답을 기다리지 않는다. complete 뒤 중단되면 송신 0회일 수 있다.
        if notices:
            from xenova_registration import automatic
            for record in notices:automatic(self.data,self.run,record)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['grow', 'register'])
    args = parser.parse_args()
    from xenova_preparation import request_output
    with request_output(),operation_lock() as common:
        boot = FirstBoot()
        from xenova_runtime import boot_progress
        display = boot_progress('storage' if args.action == 'grow' else 'registration', boot.run)
        boot.display_progress = display
        display('verifying')
        if args.action == 'grow':
            boot.grow()
        else:
            boot.register(common=common)
        # 성공 근거는 기존 필수 저장과 unit 결과다. 이 파일은 표시 보조 정보뿐이다.
        display('complete', mode=getattr(boot, 'display_mode', 'storage-verification'))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, PlatformError, subprocess.SubprocessError) as error:
        print('[xenova-first-boot] ERROR:', error, file=sys.stderr)
        sys.exit(1)
