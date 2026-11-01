"""정규 recipe의 설치 결과와 Gateway의 비활성·종료 ordering 계약을 확인한다."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
RECIPES = ROOT / 'platform/yocto/meta-xenova/recipes-core'


@unittest.skipUnless(shutil.which('bash') and os.name == 'posix', 'recipe install requires a Linux shell')
class GatewayPackagingTest(unittest.TestCase):
    def test_actual_recipe_installs_complete_runtime_without_active_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source, dest = base / 'source', base / 'root'
            source.mkdir()
            project = ROOT / 'platform/services/gateway'
            shutil.copytree(project / 'xenova_gateway', source / 'xenova_gateway',
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            shutil.copyfile(project / 'pyproject.toml', source / 'pyproject.toml')
            recipe = RECIPES / 'xenova-gateway/xenova-gateway_0.1.0.bb'
            for item in (recipe.parent / 'files').iterdir():
                shutil.copyfile(item, source / item.name)
            install = re.search(r'^do_install\(\) \{\n(.*?)^\}', recipe.read_text(), re.M | re.S).group(1)
            env = dict(os.environ, S=str(source), D=str(dest),
                       PYTHON_SITEPACKAGES_DIR='/usr/lib/python3/site-packages',
                       systemd_system_unitdir='/usr/lib/systemd/system', datadir='/usr/share')
            subprocess.run(['bash', '-e', '-c', install], env=env, check=True)
            installed = dest / 'usr/lib/python3/site-packages/xenova_gateway'
            self.assertEqual({p.name for p in installed.glob('*.py')},
                             {p.name for p in (project / 'xenova_gateway').glob('*.py')})
            for item in installed.glob('*.py'):
                self.assertEqual(item.read_bytes(), (project / 'xenova_gateway' / item.name).read_bytes())
                self.assertEqual(item.stat().st_mode & 0o777, 0o644)
            self.assertFalse((dest / 'etc/xenova/gateway.env').exists())
            self.assertFalse((dest / 'srv').exists())
            self.assertTrue((dest / 'usr/share/xenova/gateway.env.example').is_file())
            self.assertEqual((dest / 'usr/lib/systemd/system/xenova-gateway.service').read_bytes(),
                             (recipe.parent / 'files/xenova-gateway.service').read_bytes())
            self.assertNotIn('tests', {p.name for p in installed.iterdir()})

    def test_service_is_disabled_and_does_not_initialize_or_rearm(self):
        recipe = (RECIPES / 'xenova-gateway/xenova-gateway_0.1.0.bb').read_text()
        unit = (RECIPES / 'xenova-gateway/files/xenova-gateway.service').read_text()
        self.assertIn('SYSTEMD_AUTO_ENABLE:${PN} = "disable"', recipe)
        self.assertIn('python3-pyserial', recipe)
        self.assertIn('xenova-mqtt-client', recipe)
        self.assertIn('EnvironmentFile=/etc/xenova/gateway.env', unit)
        self.assertIn('ExecStart=/usr/bin/python3 -m xenova_gateway', unit)
        self.assertIn('Restart=no', unit)
        for forbidden in ('--initialize-control-state', 'ExecStartPre=', 'KillMode=process', 'KillMode=none'):
            self.assertNotIn(forbidden, unit)

    def test_gateway_does_not_cycle_with_existing_boot_and_shutdown_order(self):
        edges = {}
        for path in RECIPES.rglob('*.service'):
            for line in path.read_text().splitlines():
                if line.startswith('After='):
                    for before in line[6:].split():
                        edges.setdefault(before, set()).add(path.name)
                elif line.startswith('Before='):
                    edges.setdefault(path.name, set()).update(line[7:].split())
        gateway = 'xenova-gateway.service'
        def walk(node, active):
            self.assertNotIn(node, active, 'ordering cycle: ' + ' -> '.join([*active, node]))
            for following in edges.get(node, ()):
                walk(following, (*active, node))
        walk(gateway, ())
        self.assertIn(gateway, edges['xenova-vms.service'])
        self.assertIn('xenova-host-admission.service', edges[gateway])


if __name__ == '__main__':
    unittest.main()
