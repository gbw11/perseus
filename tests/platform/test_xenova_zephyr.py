"""실제 디스크/VM 없이 RTOS 불변 번들·출처·복사·추가 용량 계약을 검증한다."""
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import shutil
import tempfile
import unittest
from unittest.mock import patch

from first_install_fixture import ROOT, assemble, contract, profile_dts

rtos = assemble.rtos
spec = importlib.util.spec_from_file_location('prepare_zephyr', ROOT/'scripts/prepare-xenova-zephyr.py')
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


def bundle(directory):
    directory.mkdir()
    binary = bytearray(64); binary[56:60] = b'ARM\x64'
    elf = bytearray(64); elf[:6] = b'\x7fELF\x02\x01'
    struct.pack_into('<H', elf, 18, 183); struct.pack_into('<Q', elf, 24, 0x40001000)
    payloads = {'zephyr.bin': bytes(binary), 'zephyr.elf': bytes(elf),
                'zephyr.config': ('\n'.join(rtos.REQUIRED_CONFIG)+'\n').encode(),
                'zephyr.dts': profile_dts().encode(),
                'xen.cfg.in': (rtos.HERE/'xen.cfg.in').read_bytes(), 'LICENSE': b'license fixture'}
    for name, data in payloads.items():
        (directory/name).write_bytes(data); (directory/name).chmod(0o444)
    meta = {'schema': 1, 'id': rtos.source_lock()['id'], 'kind': rtos.source_lock()['kind'],
            'source': rtos.source_lock(), 'memory_mib': 16, 'vcpus': 1, 'gic_version': 'v2',
            'autostart': False, 'network': 'unsupported', 'root_disk': None, 'runtime_hardware_verified': False, 'files': {name:{'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)} for name,data in payloads.items()}}
    (directory/'template.json').write_text(json.dumps(meta))
    (directory/'template.json').chmod(0o444)
    return directory


def refresh(directory):
    meta_path=directory/'template.json'; meta=json.loads(meta_path.read_text())
    for name in meta['files']:
        meta['files'][name]={'sha256':assemble.sha(directory/name),'bytes':(directory/name).stat().st_size}
    meta_path.chmod(0o644);meta_path.write_text(json.dumps(meta));meta_path.chmod(0o444)
    return assemble.sha(meta_path)


class ZephyrTemplateTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.template=bundle(self.base/'firmware')
        self.digest=assemble.sha(self.template/'template.json')

    def info(self):
        return rtos.info(self.template,self.digest)

    def test_valid_diskless_firmware_is_not_a_linux_default_instance(self):
        result=self.info()
        self.assertFalse(result['autostart']);self.assertFalse(result['runtime_hardware_verified'])
        self.assertEqual(result['storage_directory'],'rtos-templates')
        self.assertFalse((self.template/'root.raw').exists())
        self.assertGreater(result['logical_bytes'],0)

    def test_payload_checksum_mismatch_rejected(self):
        p=self.template/'zephyr.bin';p.chmod(0o644);p.write_bytes(b'changed');p.chmod(0o444)
        with self.assertRaisesRegex(ValueError,'SHA-256'):self.info()

    def test_missing_extra_writable_and_linked_files_rejected(self):
        for kind in ('missing','extra','writable','linked'):
            path=bundle(self.base/kind)
            if kind=='missing':(path/'zephyr.bin').unlink()
            if kind=='extra':(path/'root.raw').write_bytes(b'not a Linux disk')
            if kind=='writable':(path/'zephyr.bin').chmod(0o644)
            if kind=='linked':
                (path/'zephyr.bin').unlink();(path/'zephyr.bin').symlink_to(self.template/'zephyr.bin')
            with self.assertRaises(ValueError):rtos.info(path,assemble.sha(path/'template.json'))

    def test_changed_release_provenance_rejected_even_with_new_manifest_hash(self):
        p=self.template/'template.json';meta=json.loads(p.read_text());meta['source']['version']='other'
        p.chmod(0o644);p.write_text(json.dumps(meta));p.chmod(0o444)
        with self.assertRaisesRegex(ValueError,'provenance'):rtos.info(self.template,assemble.sha(p))

    def test_manifest_cannot_claim_autostart_network_or_hardware_success(self):
        for field,value in [('autostart',True),('network','DHCP'),('runtime_hardware_verified',True),('memory_mib',64)]:
            path=bundle(self.base/field);meta_path=path/'template.json';meta=json.loads(meta_path.read_text());meta[field]=value
            meta_path.chmod(0o644);meta_path.write_text(json.dumps(meta));meta_path.chmod(0o444)
            with self.assertRaisesRegex(ValueError,'policy'):rtos.info(path,assemble.sha(meta_path))

    def test_forged_hashes_do_not_bypass_arm64_or_runtime_timer_requirements(self):
        for kind in ('bad-image','bad-elf','bad-config'):
            path=bundle(self.base/kind)
            name={'bad-image':'zephyr.bin','bad-elf':'zephyr.elf','bad-config':'zephyr.config'}[kind]
            p=path/name;p.chmod(0o644);p.write_bytes(b'bad');p.chmod(0o444)
            with self.assertRaises(ValueError):rtos.info(path,refresh(path))

    def test_output_copy_is_independent_readonly_and_not_a_linux_registry_entry(self):
        entry=self.info();data=self.base/'data';data.mkdir()
        result=assemble.copy_rtos_templates(data,[entry])
        target=data/'rtos-templates'/entry['id']
        self.assertEqual(len(result),1)
        self.assertFalse((data/'instances').exists());self.assertFalse((data/'seeds').exists())
        rtos.info(target,self.digest)
        for name in rtos.FILES|{'template.json'}:
            self.assertNotEqual((target/name).stat().st_ino,(self.template/name).stat().st_ino)
            self.assertFalse((target/name).stat().st_mode & 0o222)
        self.assertEqual(self.info(),entry)

    def test_existing_catalog_is_never_overwritten(self):
        data=self.base/'data';data.mkdir();(data/'rtos-templates').mkdir()
        (data/'rtos-templates/keep').write_text('keep')
        with self.assertRaises(FileExistsError):assemble.copy_rtos_templates(data,[self.info()])
        self.assertEqual((data/'rtos-templates/keep').read_text(),'keep')

    def test_optional_catalog_requires_explicit_hash_and_single_pinned_source(self):
        entry={'template_dir':str(self.template),'template_manifest_sha256':self.digest}
        self.assertEqual(len(assemble.rtos_info({'rtos_templates':[entry]})),1)
        self.assertEqual(assemble.rtos_info({}),[])
        for entries in ([entry,entry],{},[{'template_dir':str(self.template)}]):
            with self.assertRaises(ValueError):assemble.rtos_info({'rtos_templates':entries})

    def test_capacity_adds_firmware_without_creating_third_eight_gib_disk(self):
        guests=[{'logical_bytes':8*contract.GIB,'allocated_bytes':contract.GIB,'boot_bytes':128*contract.MIB} for _ in range(2)]
        original=contract.capacity(4428345344,guests)
        updated=contract.capacity(4428345344,guests,retained_firmware_bytes=64*contract.MIB,card_bytes=63864569856)
        self.assertEqual(updated['vm_max_bytes'],16*contract.GIB)
        self.assertEqual(updated['retained_template_root_bytes'],16*contract.GIB)
        self.assertEqual(updated['retained_firmware_bytes'],64*contract.MIB)
        self.assertGreater(updated['minimum_card_bytes'],original['minimum_card_bytes'])
        with self.assertRaisesRegex(ValueError,'insufficient card'):
            contract.capacity(4428345344,guests,retained_firmware_bytes=64*contract.MIB,card_bytes=original['minimum_card_bytes'])
        for value in (-1,True,2*contract.GIB):
            with self.assertRaises(ValueError):contract.capacity(4428345344,guests,retained_firmware_bytes=value)

    def test_packaging_plan_does_not_publish_or_modify_build(self):
        source=self.base/'source';source.mkdir();(source/'LICENSE').write_text('license')
        build=self.base/'build';build.mkdir();shutil.copytree(self.template,build/'zephyr')
        (build/'zephyr/zephyr.config').rename(build/'zephyr/.config')
        (build/'CMakeCache.txt').write_text('ZEPHYR_BASE:PATH='+str(source)+'\nAPPLICATION_SOURCE_DIR:PATH='+str(source/'samples/synchronization')+'\n')
        output=self.base/'new-template'
        with patch.object(prepare,'verify_source',return_value=59501),patch.object(prepare,'publish',side_effect=AssertionError('plan cannot publish')):
            prepare.main(['--source',str(source),'--build-dir',str(build),'--spdx',str(self.base/'spdx'),'--toolchain-archive',str(self.base/'sdk'),'--output',str(output)])
        self.assertFalse(output.exists())
