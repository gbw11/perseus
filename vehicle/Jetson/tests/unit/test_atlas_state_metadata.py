"""선택 atlas 좌표 메타데이터의 순수 문법과 CP/LOAD codec 거절 회귀.

보정·source·Scene/복원 승인이 아니다. LIVE/body/proof를 넣지 않고 원 ID union과
구형 canonical bytes를 보존하며 storage 모듈에 runtime/NumPy 의존성을 추가하지 않는다.
"""

import ast
from base64 import b64encode
from hashlib import sha256
import math
from pathlib import Path
import unittest
import zlib

from xenova_vehicle.storage import mapper_state as module
from xenova_vehicle.storage.mapper_state import encode_mapper_state, decode_mapper_state, mapper_state_roots
from xenova_vehicle.storage.contracts import PersistenceError, StorageContext, StorageJob, canonical
from xenova_vehicle.storage.records import ContextRecord, EvidenceTable, CheckpointPayload


def state():
    anchor, offset, resolution = [0.125, -.25], [-65, -1], .125
    origin = [math.fsum((value, index*resolution)) for value,index in zip(anchor,offset)]
    return dict(profile='mapper-pipeline-state-1', reference_provenance={'a'*64:7}, alignment_provenance=9,
        spec=dict(width=4,height=8,resolution_m=resolution,origin_xy=origin),
        atlas=dict(profile='atlas-rolling-1',lattice_origin_xy_m=anchor,roi_origin_cell=offset,
            tile_cells=64,live_restore='RETAINED_EVIDENCE_ONLY',policy_sha256='b'*64))


def raw_pages(value):
    """오류 metadata라도 원 hash/압축이 맞는 page를 독립 구성해 decoder 경계를 검산한다."""
    raw=canonical(value);compressed=zlib.compress(raw,6);key=sha256(raw).hexdigest()
    assert len(compressed)<=2304
    page=ContextRecord(canonical(dict(kind='METRIC',body=dict(profile='mapper-state-page-1',
        state_sha256=key,state_length=len(raw),compressed_length=len(compressed),index=0,count=1,
        chunk_b64=b64encode(compressed).decode('ascii')))))
    reference=dict(profile='mapper-state-1',encoding='ZLIB_CANONICAL_JSON_1',state_sha256=key,
        state_length=len(raw),compressed_length=len(compressed),pages=[[0,page.sha256]])
    return reference,(page,)


class AtlasStateMetadataTests(unittest.TestCase):
    def test_legacy_roots_and_canonical_pages_are_unchanged_without_optional_atlas(self):
        value=state();value.pop('atlas')
        before=canonical(value)
        self.assertEqual(mapper_state_roots(value),(7,9))
        reference,pages=encode_mapper_state(value)
        expected,originals=raw_pages(value)
        self.assertEqual(reference,expected)
        self.assertEqual(tuple(p.payload for p in pages),tuple(p.payload for p in originals))
        decoded,_=decode_mapper_state(dict(mapper_state=reference),pages)
        self.assertEqual(canonical(decoded),before)
        self.assertNotIn('atlas',decoded)
        self.assertEqual(mapper_state_roots({'profile':'opaque-legacy'}),())
        self.assertEqual(mapper_state_roots(None),())

    def test_signed_valid_small_atlas_adds_no_body_or_roots_and_roundtrips_original_values(self):
        value=state();before=canonical(value)
        self.assertEqual(mapper_state_roots(value),(7,9))
        reference,pages=encode_mapper_state(value)
        decoded,_=decode_mapper_state(dict(mapper_state=reference),pages)
        self.assertEqual(decoded,value)
        self.assertEqual(canonical(value),before)
        self.assertEqual(mapper_state_roots(decoded),(7,9))
        self.assertLess(len(before),1024)
        self.assertEqual(set(decoded['atlas']),{'profile','lattice_origin_xy_m','roi_origin_cell',
            'tile_cells','live_restore','policy_sha256'})
        # 내부 producer의 tuple spec도 기존 canonical codec에서 JSON list로 바뀐다.
        value['spec']['origin_xy']=tuple(value['spec']['origin_xy'])
        reference,pages=encode_mapper_state(value)
        self.assertIsInstance(decode_mapper_state(dict(mapper_state=reference),pages)[0]['spec']['origin_xy'],list)

    def test_exact_extension_profile_keys_and_no_free_restore_or_body_are_required(self):
        invalid=[]
        for key in state()['atlas']:
            value=state();del value['atlas'][key];invalid.append(value)
        for change in (dict(profile='atlas-rolling-2'),dict(tile_cells=64.),dict(tile_cells=True),
                       dict(live_restore='LIVE_FREE'),dict(policy_sha256='B'*64),dict(body='00'),dict(roots=[1])):
            value=state();value['atlas'].update(change);invalid.append(value)
        value=state();value['atlas']=None;invalid.append(value)
        value=state();value['profile']='mapper-pipeline-state-2';invalid.append(value)
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(PersistenceError):mapper_state_roots(value)
                with self.assertRaises(PersistenceError):encode_mapper_state(value)

    def test_offsets_and_spec_reject_bool_noninteger_address_overflow_and_dimension_bounds(self):
        invalid=[]
        for offset in ([True,0],[0.,0],['-1',0],[-(1<<31)*64-1,0],[(1<<31)*64-1,0],[0]):
            value=state();value['atlas']['roi_origin_cell']=offset;invalid.append(value)
        for key,value in (('width',True),('width',1),('height',65),('resolution_m',True),
                          ('resolution_m',.004),('resolution_m',1.001)):
            original=state();original['spec'][key]=value;invalid.append(original)
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(PersistenceError):mapper_state_roots(value)
                with self.assertRaises(PersistenceError):encode_mapper_state(value)
        for offset in ([-(1<<31)*64,0],[(1<<31)*64-4,0]):
            valid=state();valid['atlas']['roi_origin_cell']=offset
            valid['spec']['origin_xy']=[math.fsum((a,i*valid['spec']['resolution_m']))
                for a,i in zip(valid['atlas']['lattice_origin_xy_m'],offset)]
            self.assertEqual(mapper_state_roots(valid),(7,9))

    def test_anchor_origin_finiteness_and_exact_shared_fsum_prevent_rebasing_or_rounding(self):
        for target in ('anchor','origin'):
            for bad in (True,math.nan,math.inf,1<<2000):
                value=state()
                column=value['atlas']['lattice_origin_xy_m'] if target=='anchor' else value['spec']['origin_xy']
                column[0]=bad
                with self.subTest(target=target,bad_type=type(bad).__name__),self.assertRaises(PersistenceError):
                    mapper_state_roots(value)
        for change in ('anchor','offset','ulp'):
            value=state()
            if change=='anchor':value['atlas']['lattice_origin_xy_m'][0]+=.125
            elif change=='offset':value['atlas']['roi_origin_cell'][0]+=1
            else:value['spec']['origin_xy'][0]=math.nextafter(value['spec']['origin_xy'][0],math.inf)
            with self.subTest(change=change),self.assertRaises(PersistenceError):encode_mapper_state(value)

    def test_checksummed_invalid_pages_fail_load_codec_and_checkpoint_constructor(self):
        value=state();value['atlas']['live_restore']='LIVE_FREE'
        reference,pages=raw_pages(value)
        with self.assertRaises(PersistenceError):decode_mapper_state(dict(mapper_state=reference),pages)
        uid='60000000-0000-4000-8000-000000000001'
        context=StorageContext(uid,uid,uid,uid,uid,uid,uid,'atlas-fixture','a'*64,'b'*64,canonical({'test':'only'}))
        job=StorageJob(context,'60000000-0000-4000-8000-000000000002',100,200)
        with self.assertRaises(PersistenceError):
            CheckpointPayload.build(job,evidence=EvidenceTable(contexts=pages),scene=dict(mapper_state=reference),
                durable_cutoff_pi_us=123)

    def test_native_hooks_and_storage_runtime_imports_are_not_used(self):
        class Trap(float):
            def __float__(self):raise AssertionError('numeric hook must not run')
        value=state();value['atlas']['lattice_origin_xy_m'][0]=Trap(.125)
        with self.assertRaises(PersistenceError):mapper_state_roots(value)
        tree=ast.parse(Path(module.__file__).read_text(encoding='utf-8'))
        imports=[]
        for node in ast.walk(tree):
            if isinstance(node,ast.Import):imports.extend(item.name for item in node.names)
            elif isinstance(node,ast.ImportFrom):imports.append(node.module or '')
        self.assertFalse(any(any(part in name for part in ('runtime','numpy','mapping','geometry')) for name in imports))


if __name__=='__main__':
    unittest.main()
