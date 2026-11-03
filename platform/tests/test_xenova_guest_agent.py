"""실제 준비·설치 로직과 배포판 unit을 사용한다. apt/chroot/loop만 시험 경계로 대체한다."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from first_install_fixture import ROOT, prepare_rootfs as guest
from test_xenova_platform import load, record
from test_xenova_guest_ssh_graph import extract, PROVENANCE

import guest_agent_template as agent
import template_recipe
tool = load('xenova_agent_template_tool_test', ROOT / 'platform/scripts/prepare-xenova-guests.py')
spec = importlib.util.spec_from_file_location('xenova_real_agent_installer_test', agent.INSTALLER)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def paho(root):
    p = root / 'usr/lib/python3/dist-packages/paho/mqtt/client.py'
    p.parent.mkdir(parents=True, exist_ok=True); p.write_text('# package boundary only\n')


class GuestAgentTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='xenova-agent-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'root'; self.root.mkdir()

    def test_apt_timeout_reaps_only_its_own_child_group_before_returning(self):
        child = self.base / 'child.pid'
        script = ('import subprocess,sys,time;'
                  'p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]);'
                  'open(sys.argv[1],"w").write(str(p.pid));time.sleep(60)')
        with self.assertRaises(subprocess.TimeoutExpired):
            agent.apt_command([sys.executable, '-B', '-c', script, str(child)], timeout=1)
        pid = child.read_text().strip()
        status = Path('/proc') / pid / 'status'
        try:
            state = status.read_text()
        except FileNotFoundError:
            pass  # PID 1 may reap the child between probing and reading /proc.
        else:
            self.assertIn('Z (zombie)', state)
        # PID 1 수거 전 잠시 zombie일 수 있지만 실행을 계속하면 실패다.

    def test_policy_rejects_unknown_packages_and_existing_instances_before_apt(self):
        with patch.object(agent.subprocess, 'run') as run:
            with self.assertRaises(ValueError): agent.prepare(self.root, {'packages': ['other']})
            guest.write(self.root, 'etc/xenova/template-prepared', '1\n')
            guest.write(self.root, 'etc/xenova/instance-id', record()['uuid'])
            with self.assertRaisesRegex(ValueError, 'new prepared template'):
                agent.prepare(self.root, {'packages': agent.PACKAGES})
            run.assert_not_called()

    def test_temporary_dns_and_service_guard_restore_bytes_modes_and_links_on_failure(self):
        guest.write(self.root, 'usr/sbin/policy-rc.d', 'original policy', 0o750)
        guest.write(self.root, 'etc/apt/apt.conf.d/50command-not-found', 'original hook', 0o640)
        guest.write(self.root, 'etc/apt/apt.conf.d/50appstream', 'original GUI hook', 0o644)
        dns = self.root / 'etc/resolv.conf'; dns.parent.mkdir(parents=True, exist_ok=True)
        dns.symlink_to('/run/systemd/resolve/stub-resolv.conf')
        with self.assertRaisesRegex(RuntimeError, 'apt failed'):
            with agent.package_environment(self.root):
                self.assertEqual((self.root / 'usr/sbin/policy-rc.d').read_text(), '#!/bin/sh\nexit 101\n')
                self.assertFalse(dns.is_symlink())
                self.assertEqual((self.root / 'etc/apt/apt.conf.d/50command-not-found').read_text(), 'original hook')
                self.assertIn('#clear APT::Update::Post-Invoke-Success;',
                              (self.root / 'run/xenova-agent-apt.conf').read_text())
                raise RuntimeError('apt failed')
        self.assertEqual(os.readlink(dns), '/run/systemd/resolve/stub-resolv.conf')
        policy = self.root / 'usr/sbin/policy-rc.d'
        self.assertEqual(policy.read_text(), 'original policy')
        cnf = self.root / 'etc/apt/apt.conf.d/50command-not-found'
        self.assertEqual(cnf.read_text(), 'original hook')
        self.assertEqual(cnf.stat().st_mode & 0o777, 0o640)
        self.assertEqual((self.root / 'etc/apt/apt.conf.d/50appstream').read_text(), 'original GUI hook')
        self.assertFalse((self.root / 'run/xenova-agent-apt.conf').exists())
        self.assertEqual(policy.stat().st_mode & 0o777, 0o750)

    def test_source_inventory_covers_package_data_and_refuses_linked_inputs(self):
        source = self.base / 'source'
        for path in agent.source_files():
            target = source / path.relative_to(agent.SOURCE)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
        data = source / 'mqtt-client/xenova_mqtt/contract.json'
        data.write_text('{"schema":1}')
        with patch.object(agent, 'SOURCE', source), patch.object(agent, 'INSTALLER',
                source / 'agent/deploy/install_into_rootfs.py'):
            self.assertIn(data, agent.source_files())
            linked = source / 'agent/xenova_agent/outside.py'
            linked.symlink_to('/etc/hosts')
            with self.assertRaisesRegex(ValueError, 'symlinks'):
                agent.source_files()

    def test_source_lock_includes_installer_unit_and_both_python_packages(self):
        for source in ('ubuntu-24.04-20260926', 'debian-13-20261015'):
            recipe = ROOT / 'platform/guests/linux' / source / 'recipe.json'
            result = template_recipe.source_info(recipe, source)
            self.assertEqual(result['recipe']['guest_agent'], {'packages': ['python3-paho-mqtt']})
            self.assertEqual(result['recipe']['version'], '1.0.4')
            inputs = result['preparer_files']
            for path in agent.source_files():
                self.assertEqual(inputs[str(path.relative_to(ROOT))], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_required_agent_missing_or_incomplete_is_not_reported_installed(self):
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            guest.verify_guest_agent(self.root, required=True)
        self.assertEqual(guest.verify_guest_agent(self.root)['reason'], 'legacy-template')

    def test_production_prepare_paths_set_only_seed_requires_and_real_loader_accepts_both(self):
        loader = json.loads(os.environ.get('XENOVA_SYSTEMD_ANALYZE', '["systemd-analyze"]'))
        for distribution in PROVENANCE['guests']:
            for seed in (False, True):
                with self.subTest(distribution=distribution, seed=seed):
                    root = self.base / (distribution + str(seed)); root.mkdir()
                    extract(root, distribution); paho(root); installer.install(root, agent.SOURCE)
                    if seed:
                        guest.prepare_deployment_seed(root, record())
                    else:
                        guest.prepare_instance(root, record(), command=lambda args, **kw: Path(args[-1]).touch())
                    dropin = root / installer.IDENTITY_DROPIN
                    self.assertEqual(dropin.is_file(), seed)
                    if seed:
                        self.assertEqual(dropin.read_text(), installer.IDENTITY_DROPIN_SRC.read_text())
                    result = subprocess.run(loader + ['verify', '--recursive-errors=yes', '--generators=no',
                        '--man=no', '--root=' + str(root), 'xenova-agent.service'],
                        text=True, capture_output=True, env={**os.environ, 'SYSTEMD_LOG_LEVEL': 'debug'})
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertNotIn('Found ordering cycle', result.stderr)
                    self.assertNotIn('deleted to break ordering cycle', result.stderr)
                    self.assertIn('xenova-agent.service: Enqueued job', result.stderr)
                    self.assertEqual('Requires: xenova-guest-identity.service' in result.stdout, seed)

    def test_seed_missing_dependency_and_import_failure_fail_closed(self):
        paho(self.root); installer.install(self.root, agent.SOURCE)
        with self.assertRaisesRegex(ValueError, 'dependency absent'):
            guest.verify_guest_agent(self.root, seed=True, required=True)
        with patch.object(guest.subprocess, 'check_output', side_effect=subprocess.CalledProcessError(1, 'import')):
            with self.assertRaises(subprocess.CalledProcessError):
                guest.verify_guest_agent(self.root, required=True)

    def template(self, *, fail_apt=False):
        original = self.base / 'original'; original.mkdir()
        guest.write(original, 'etc/shadow', 'root:fixture:0:0:99999:7:::\n')
        guest.write(original, 'etc/ssh/sshd_config', '')
        guest.write(original, 'etc/os-release', 'ID=ubuntu\n')
        # 명령 존재/enable 검사용 fixture. 실제 그래프는 별도 배포판 unit 시험에서 검사한다.
        for unit in ('systemd-networkd.service', 'ssh.service'):
            guest.write(original, 'usr/lib/systemd/system/' + unit, '[Service]\\nExecStart=/bin/true\\n')
        sourcefile = self.base / 'cloud.qcow2'; sourcefile.write_bytes(b'small image boundary')
        output = self.base / 'new-template'
        args = SimpleNamespace(input=sourcefile, output=output, source='ubuntu-24.04-20260926',
                               size_gib=0, recipe=ROOT / 'platform/guests/linux/ubuntu-24.04-20260926/recipe.json')
        source = {'hash_algorithm': 'sha256', 'digest': hashlib.sha256(sourcefile.read_bytes()).hexdigest(),
                  'format': 'qcow2', 'distribution': 'ubuntu'}
        work = SimpleNamespace(path=self.base / 'work', mounts=[], loops=[])
        work.path.mkdir()
        def mount(device, target, **kw): target.mkdir(parents=True, exist_ok=True)
        work.mount = mount
        work.loop = lambda *a, **kw: '/dev/loop-test-only'
        def close(): work.mounts.clear(); work.loops.clear()
        work.close = close
        def command(argv, **kw):
            if argv[0] == 'cp':
                shutil.copytree(original, Path(argv[-1]), dirs_exist_ok=True)
        calls = []
        def apt(argv, **kw):
            calls.append(argv)
            root = Path(argv[1])
            self.assertEqual((root / 'usr/sbin/policy-rc.d').read_text(), '#!/bin/sh\nexit 101\n')
            self.assertIn('SYSTEMD_OFFLINE=1', argv)
            if fail_apt: raise subprocess.CalledProcessError(100, argv)
            if 'install' in argv: paho(root)
        real_prepare = agent.prepare
        def prepare(root, policy):
            return real_prepare(root, policy, command=apt)
        def assets(root, stage, source):
            for name in ('Image', 'initrd', 'kernel.config', 'boot.json'): (stage / name).write_text(name)
        def query(argv, **kw):
            if argv[0] == 'systemctl': return 'enabled\n'
            if 'dpkg-query' in argv: return 'python3-paho-mqtt\t1.6.1-1\tall\n'
            return json.dumps({'python': '3.12', 'paho_mqtt': '1.6.1'})
        with patch.object(tool, 'Workspace', return_value=work), patch.object(tool, 'root_partition', return_value=original), \
             patch.object(tool, 'command', side_effect=command), patch.object(tool, 'boot_assets', side_effect=assets), \
             patch.object(tool.guest_agent_template, 'prepare', side_effect=prepare), \
             patch.object(guest.subprocess, 'check_output', side_effect=query), patch.object(tool.os, 'sync'):
            if fail_apt:
                with self.assertRaises(subprocess.CalledProcessError): tool.make_template(args, source)
            else:
                tool.make_template(args, source)
        return output, work, calls

    def test_real_template_entrypoint_installs_agent_and_records_receipt(self):
        # prepare를 감싸는 경계는 apt/chroot만 가로챈다. installer·의존성 판정은 production이다.
        output, work, calls = self.template()
        meta = json.loads((output / 'template.json').read_text())
        self.assertTrue(meta['guest_agent']['verification']['installed'])
        self.assertEqual(meta['guest_agent']['broker_connection'], 'not-tested')
        root = work.path / 'root'
        self.assertTrue((root / 'opt/xenova/agent/xenova_agent/heartbeat.py').is_file())
        self.assertTrue((root / 'etc/systemd/system/multi-user.target.wants/xenova-agent.service').is_symlink())
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[-1][-2:], ['install', 'python3-paho-mqtt'])
        self.assertFalse((root / 'etc/resolv.conf').exists())
        self.assertFalse((root / 'usr/sbin/policy-rc.d').exists())

    def test_apt_failure_never_publishes_template_or_installs_agent(self):
        output, work, calls = self.template(fail_apt=True)
        self.assertFalse(output.exists())
        self.assertFalse((work.path / 'root/opt/xenova/agent').exists())
        self.assertFalse((work.path / 'root/etc/resolv.conf').exists())
        self.assertEqual(len(calls), 1)
