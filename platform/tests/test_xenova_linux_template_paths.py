"""고정 Debian/Ubuntu 선택의 공통 복사 경로를 작은 실제 파일로 검증한다.

ID·배포 버전·출처는 저장소 lock에서 읽고 실제 조립기의 카탈로그 게시 함수를 사용한다.
payload와 그 manifest 해시는 축소 fixture이며, 고정 대용량 산출물·OS 부팅 수락은 아니다.
"""
from collections import Counter
import json
from pathlib import Path
from unittest.mock import patch

from first_install_fixture import ROOT, assemble
from test_xenova_spring_followup import FollowupFixture
import prepare_instance as preparation
import xenova_catalog as catalog


class DefaultLinuxTemplatePathTest(FollowupFixture):
    def select_default(self, distribution):
        locked = json.loads((ROOT/'platform/guests/default-templates.lock.json').read_text())['templates']
        sources = json.loads((ROOT/'platform/guests/sources.lock.json').read_text())
        entry = next(e for e in locked if e['guest_kind']=='linux'
                     and sources[e['id']]['distribution']==distribution)
        source = self.f.data/'templates'/entry['id']
        # 기존 fixture만 교체한다. 제품 저장소나 게시된 실제 템플릿을 수정하지 않는다.
        (self.f.data/'catalog/ubuntu-24.04-arm64/1.0.0.json').unlink()
        (self.f.data/'templates/source').rename(source)
        boot_path = source/'boot.json'
        boot = json.loads(boot_path.read_text())
        boot.update(source=sources[entry['id']],kernel_release=sources[entry['id']]['kernel_release'])
        boot_path.chmod(0o644)
        boot_path.write_text(json.dumps(boot))
        boot_path.chmod(0o444)
        manifest = source/'template.json'
        meta = json.loads(manifest.read_text())
        meta.update(id=entry['id'],template_id=entry['template_id'],template_version=entry['version'],
                    version=entry['version'],source=boot['source'])
        meta['files']['boot.json'] = {'sha256':catalog.sha(boot_path),'bytes':boot_path.stat().st_size}
        manifest.chmod(0o644)
        manifest.write_text(json.dumps(meta))
        manifest.chmod(0o444)
        # 이미지 조립 때의 게시 검증은 유지한다. 아래 create 계측과 구분한다.
        with patch.object(catalog,'sha',wraps=catalog.sha) as hashes:
            assemble.copy_management_catalog(self.f.data)
        self.assertEqual(sum(Path(c.args[0]).name=='root.raw' for c in hashes.call_args_list),1)
        self.spec.update(template_id=entry['template_id'],template_version=entry['version'])
        return entry, source

    def verified_path(self, distribution):
        entry, source = self.select_default(distribution)
        before = {p.name:p.read_bytes() for p in source.iterdir()}
        with self.linux_boundaries() as commands, \
                patch.object(catalog,'sha',wraps=catalog.sha) as hashes, \
                patch.object(preparation,'digest',wraps=preparation.digest) as digests, \
                patch.object(preparation,'_copy_verified',wraps=preparation._copy_verified) as copies:
            plan = self.m.call('create',self.spec,execute=False)
            self.assertTrue(plan['ok'],plan)
            self.assertEqual(self.m._operations(),[])
            result = self.m.call('create',self.spec,execute=True)
        self.assertTrue(result['ok'],result)
        self.assertEqual(sum(Path(c.args[0]).name=='root.raw' for c in hashes.call_args_list),0)
        self.assertEqual(digests.call_count,0)
        self.assertFalse(any(c[0]=='cp' for c in commands))
        self.assertEqual(Counter(c.args[0].name for c in copies.call_args_list),Counter({
            'root.raw':1,'Image':1,'initrd':1,'boot.json':1,'kernel.config':1}))
        op = self.m._operations()[0]
        target = self.f.data/'instances'/op['assigned']['internal_name']
        record = json.loads((target/'instance.json').read_text())
        self.assertEqual(op['template']['source_template_id'],entry['id'])
        self.assertEqual(op['template']['source']['distribution'],distribution)
        for row in (op['template'],record,result['data']):
            self.assertEqual(row['template_id'],entry['template_id'])
            self.assertEqual(row['template_version'],entry['version'])
            self.assertEqual(row['guest_kind'],'linux')
        for name in ('root.raw','Image','initrd','boot.json','kernel.config'):
            self.assertEqual((target/name).read_bytes(),before[name])
            self.assertNotEqual((target/name).stat().st_ino,(source/name).stat().st_ino)
        self.assertEqual({p.name:p.read_bytes() for p in source.iterdir()},before)

    def failed_path(self, distribution):
        _, source = self.select_default(distribution)
        root = source/'root.raw'
        root.chmod(0o644)
        changed = bytearray(root.read_bytes());changed[0] ^= 1
        root.write_bytes(changed)
        root.chmod(0o444)
        with self.linux_boundaries(), patch.object(preparation,'prepare_instance') as configure, \
                patch.object(preparation,'_copy_verified',wraps=preparation._copy_verified) as copies:
            result = self.m.call('create',self.spec,execute=True)
        self.error(result,'RECOVERY_REQUIRED')
        configure.assert_not_called()
        self.assertEqual(copies.call_count,1)
        op = self.m._operations()[0]
        self.assertIn('SHA-256 mismatch: root.raw',op['error']['message'])
        partial = self.f.data/op['owned_workspace']/op['assigned']['internal_name']/'root.raw'
        before = partial.stat().st_ino,partial.read_bytes()
        key = 'create:'+self.spec['vm_id']
        queried = self.m.call('operation',{'idempotency_key':key})
        self.assertTrue(queried['ok'],queried)
        self.assertEqual(queried['data']['operation'],op)
        self.assertEqual(queried['data']['effective_status'],'RECOVERY_REQUIRED')
        # 미게시 부분 준비는 등록 VM용 retire/purge의 대상이 아니다.
        self.error(self.m.call('get',{'vm_id':op['vm_id']}),'VM_NOT_FOUND')
        for action in ('retire','purge'):
            spec = {'vm_id':op['vm_id']}
            if action=='purge':spec['confirm_vm_id']=op['vm_id']
            self.error(self.m.call(action,spec,execute=True,idempotency_key='unsupported-'+action),'VM_NOT_FOUND')
        with patch.object(preparation,'_copy_verified',side_effect=AssertionError('must not recopy')):
            self.error(self.m.call('create',self.spec,execute=True),'RECOVERY_REQUIRED')
        self.assertEqual(self.m._operations(),[op])
        self.assertEqual((partial.stat().st_ino,partial.read_bytes()),before)

    def test_debian_default_catalog_uses_one_verified_copy_without_preflight_hash(self):
        self.verified_path('debian')

    def test_ubuntu_default_catalog_uses_one_verified_copy_without_preflight_hash(self):
        self.verified_path('ubuntu')

    def test_debian_failed_copy_preserves_origin_without_an_unpublished_asset_cleanup_api(self):
        self.failed_path('debian')

    def test_ubuntu_failed_copy_preserves_origin_without_an_unpublished_asset_cleanup_api(self):
        self.failed_path('ubuntu')
