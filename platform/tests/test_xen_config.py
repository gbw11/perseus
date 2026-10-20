"""Linux cfg contract and actual platform/installed-package integration checks."""
import ast
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / 'platform/guests/common/xen_config.py'
SPEC = importlib.util.spec_from_file_location('p2_xen_config_test', MODULE)
cfg = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cfg)


def example(os_id='ubuntu'):
    return json.loads((ROOT / f'platform/guests/examples/{os_id}-cfg-spec.json').read_text(encoding='utf-8'))


def render(data):
    return cfg.render_config(data['instance'], instance_root=data['instance_root'])


def assignments(text):
    return {node.targets[0].id: ast.literal_eval(node.value) for node in ast.parse(text).body}


class XenConfigTest(unittest.TestCase):
    def test_both_os_preserve_existing_names_uuid_paths_and_boot_settings(self):
        for os_id in ('ubuntu', 'debian'):
            with self.subTest(os_id=os_id):
                data = example(os_id)
                r = data['instance']
                base = f"/srv/xenova/instances/{r['id']}"
                expected = {
                    'name': r['name'], 'uuid': r['uuid'], 'type': 'pvh',
                    'memory': r['memory_mib'], 'vcpus': r['vcpus'],
                    'kernel': base + '/Image', 'ramdisk': base + '/initrd',
                    'extra': 'console=hvc0 root=/dev/xvda rw rootwait net.ifnames=0',
                    'disk': [f'format=raw,vdev=xvda,access=rw,backendtype=qdisk,target={base}/root.raw'],
                    'vif': [f"mac={r['mac']},bridge=xenbr0,script=vif-bridge"],
                    'on_poweroff': 'destroy', 'on_reboot': 'restart', 'on_crash': 'preserve',
                }
                self.assertEqual(assignments(render(data)), expected)
                self.assertNotIn('/templates/', expected['disk'][0])

    def test_legacy_records_render_without_inventing_id_or_version(self):
        for os_id in ('ubuntu', 'debian'):
            data = example(os_id)
            expected = assignments(render(data))
            del data['instance']['template_id']
            del data['instance']['template_version']
            before = copy.deepcopy(data)
            self.assertEqual(assignments(render(data)), expected)
            self.assertEqual(data, before)

    def test_two_instances_of_same_template_have_independent_identity_paths_and_mac(self):
        first, second = example(), example()
        second['instance'].update(id='ubuntu-demo', name='ubuntu-demo',
                                  uuid='550e8400-e29b-41d4-a716-446655440002', mac='02:16:3e:77:00:12')
        a, b = assignments(render(first)), assignments(render(second))
        for key in ('name', 'uuid', 'kernel', 'ramdisk', 'disk', 'vif'):
            self.assertNotEqual(a[key], b[key])

    def test_renderer_is_deterministic_without_writes_or_processes(self):
        data = example()
        before = copy.deepcopy(data)
        with patch.object(subprocess, 'run', side_effect=AssertionError('no subprocess')):
            self.assertEqual(render(data), render(data))
        self.assertEqual(before, data)
        self.assertNotIn('${', render(data))

    def test_internal_resources_keep_platform_range_without_changing_http_cap(self):
        for memory in (128, 1024, 2048, 8192):
            data = example()
            data['instance']['memory_mib'] = memory
            self.assertEqual(assignments(render(data))['memory'], memory)
        for field, value in [('vcpus', 0), ('vcpus', 5), ('vcpus', True), ('vcpus', '1'),
                             ('memory_mib', 127), ('memory_mib', 8193),
                             ('memory_mib', False), ('memory_mib', 512.0)]:
            with self.subTest(field=field, value=value):
                data = example()
                data['instance'][field] = value
                with self.assertRaises(cfg.ConfigError):
                    render(data)

    def test_identity_and_mac_cannot_inject_config(self):
        cases = [('id', '../vm'), ('id', 'vm,extra'), ('name', "vm'\nuuid='bad"),
                 ('uuid', 'invalid'), ('uuid', '550E8400-E29B-41D4-A716-446655440000'),
                 ('uuid', None), ('mac', '01:16:3e:77:00:10'),
                 ('mac', '02:16:3e:77:00:10,bridge=evil')]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                data = example()
                data['instance'][field] = value
                with self.assertRaises(cfg.ConfigError):
                    render(data)

    def test_os_resolution_rejects_firmware_unknown_ids_and_conflicting_sources(self):
        for update in ({'template_id': 'alpine'}, {'template_id': '../ubuntu'},
                       {'template_id': []}, {'guest_kind': 'zephyr'},
                       {'template_id': 'debian-13-arm64'}, {'template': []}):
            data = example()
            data['instance'].update(update)
            with self.assertRaises(cfg.ConfigError):
                render(data)
        data = example()
        del data['instance']['template_id']
        data['instance']['template'] = 'unmapped-source'
        with self.assertRaises(cfg.ConfigError):
            render(data)

    def test_path_injection_and_traversal_are_rejected(self):
        for root in ('relative', '/', '/srv/../etc', '/srv//xenova', '/srv/./xenova',
                     '/srv/xenova/', "/srv/a'", '/srv/a,b', '/srv/a\nb'):
            data = example()
            data['instance_root'] = root
            with self.assertRaises(cfg.ConfigError):
                render(data)

    def test_missing_or_malformed_template_fails_without_inline_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(cfg, 'TEMPLATE_ROOT', root):
                with self.assertRaises(cfg.ConfigError):
                    render(example())
                (root / 'ubuntu').mkdir()
                (root / 'ubuntu/xen.cfg.in').write_text("name = '${unknown}'\n")
                with self.assertRaises(cfg.ConfigError):
                    render(example())

    def test_cli_outputs_cfg_only_and_rejects_http_request_shape(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'request.json'
            path.write_text(json.dumps(example()))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(cfg.main([str(path)]), 0)
            self.assertEqual(output.getvalue(), render(example()))
            self.assertEqual(list(Path(temp).iterdir()), [path])
            path.write_text(json.dumps({'vm_spec': {}, 'render_context': {}}))
            output, errors = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                self.assertEqual(cfg.main([str(path)]), 2)
            self.assertEqual(output.getvalue(), '')
            self.assertIn('ConfigError:', errors.getvalue())


@unittest.skipUnless(sys.platform == 'linux', 'platform installation/start fixtures require Linux')
class PlatformTemplateIntegrationTest(unittest.TestCase):
    def test_real_start_uses_os_template_and_preserves_existing_disk(self):
        from first_install_fixture import vm
        from test_xenova_platform import record, fixture, FakeXen
        import xen_config as runtime_cfg
        for os_id in ('ubuntu', 'debian'):
            with self.subTest(os_id=os_id), tempfile.TemporaryDirectory() as temp:
                base = Path(temp)
                r = record(os_id + '-dev')
                directory = fixture(base, r)
                disk = (directory / 'root.raw').read_bytes()
                runner = FakeXen(base, {r['id']: r})
                p = vm.Platform(base, base / 'run', runner)
                templates = base / 'test-templates'
                (templates / os_id).mkdir(parents=True)
                source = (cfg.TEMPLATE_ROOT / os_id / 'xen.cfg.in').read_text()
                (templates / os_id / 'xen.cfg.in').write_text(source + '# integration marker\n')
                with patch.object(runtime_cfg, 'TEMPLATE_ROOT', templates), contextlib.redirect_stdout(io.StringIO()):
                    p.start(r)
                output = (base / 'run' / (r['id'] + '.cfg')).read_text()
                self.assertIn('# integration marker', output)
                self.assertEqual(assignments(output)['uuid'], r['uuid'])
                self.assertEqual((directory / 'root.raw').read_bytes(), disk)
                self.assertEqual(sum(call[:2] == ['xl', 'create'] for call in runner.calls), 1)

    def test_missing_template_prevents_xl_create(self):
        from first_install_fixture import vm
        from test_xenova_platform import record, fixture, FakeXen
        import xen_config as runtime_cfg
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            r = record()
            fixture(base, r)
            runner = FakeXen(base, {r['id']: r})
            p = vm.Platform(base, base / 'run', runner)
            with patch.object(runtime_cfg, 'TEMPLATE_ROOT', base / 'missing'):
                with self.assertRaises(vm.PlatformError):
                    p.start(r)
            self.assertFalse(any(call[:2] == ['xl', 'create'] for call in runner.calls))

    def test_recipe_installs_renderer_and_both_templates_without_source_paths(self):
        from dom0_ssh_fixture import install_policy
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            install_policy(root, replay_recipe=True)
            library = root / 'usr/lib/xenova'
            self.assertTrue((library / 'xen_config.py').is_file())
            for os_id in ('ubuntu', 'debian'):
                installed = library / f'xen-templates/{os_id}/xen.cfg.in'
                self.assertEqual(installed.read_bytes(), (cfg.TEMPLATE_ROOT / os_id / 'xen.cfg.in').read_bytes())
                # A marker proves the installed copy is read, with no source fallback.
                installed.write_text(installed.read_text() + '# installed template\n')
                code = ('import sys,json; sys.path.insert(0,sys.argv[1]); from xenova_vm import Platform; '
                        'data=json.loads(sys.argv[2]); print(Platform().config(data["instance"]))')
                env = {key: value for key, value in os.environ.items() if key != 'PYTHONPATH'}
                result = subprocess.run([sys.executable, '-B', '-c', code, str(library), json.dumps(example(os_id))],
                                        cwd=temp, env=env, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('# installed template', result.stdout)
                self.assertEqual(assignments(result.stdout), assignments(render(example(os_id))))
            recipe = (ROOT / 'platform/yocto/meta-xenova/recipes-core/xenova-platform/xenova-platform_1.0.bb').read_text()
            self.assertIn('python3-stringold', recipe)


if __name__ == '__main__':
    unittest.main()
