"""bounded ROI의 원 CELL/overlap/export와 전역 unresolved 주소를 검사한다.

reader/store는 불변 EVIDENCE bytes의 계산 local fixture다. pool lease, actual LOAD,
시각 freshness, source/scene/주행 권한을 생성하거나 수락하는 시험이 아니다.
"""

from hashlib import sha256
import math
import unittest

from xenova_vehicle.mapping.map_lattice import GridWindow, MapLattice, tile_address
from xenova_vehicle.runtime.atlas_proof import AtlasProofWindow
from xenova_vehicle.storage.records import CELL, TILE_BYTES


UNKNOWN = CELL.pack(255,0,0,0.,0.,0.,0,0,0,0,0,0)
EMPTY = UNKNOWN*4096


def cell(identity=17, *, occupancy=100):
    return CELL.pack(occupancy,1,2,-.125,.25,.03125,identity,123456,123499,91,71,0)


def body_at(values):
    result=bytearray(EMPTY)
    for index,value in values.items():result[index*64:(index+1)*64]=value
    return bytes(result)


class Reader:
    def __init__(self, bodies=None):
        self.bodies={} if bodies is None else dict(bodies)
        self.calls=[]

    def __call__(self, x, y):
        self.calls.append((x,y))
        return self.bodies.get((x,y))


class AtlasProofTests(unittest.TestCase):
    def window(self, origin=(0,0), width=4, height=4):
        return GridWindow(MapLattice((.125,-.375),.025),origin,width,height)

    def test_signed_address_reads_only_current_roi_and_shares_immutable_original_cells(self):
        original=cell()
        reader=Reader({(-1,-1):body_at({4095:original})})
        proof=AtlasProofWindow(self.window((-1,-1),64,64),reader)
        self.assertEqual(set(reader.calls),{(-1,-1),(0,-1),(-1,0),(0,0)})
        self.assertEqual(len(reader.calls),4)
        self.assertEqual(len(proof.cells),4096)
        self.assertEqual(proof.read(-1,-1),original)
        self.assertEqual(CELL.unpack(proof.read(-1,-1))[6:],CELL.unpack(original)[6:])
        self.assertEqual(proof.read(0,0),UNKNOWN)
        with self.assertRaises(ValueError):proof.read(-2,-1)
        with self.assertRaises(TypeError):proof.cells[(-1,-1)]=UNKNOWN
        with self.assertRaises(AttributeError):proof.window=self.window()
        packed=proof.packed_tiles(reader)
        self.assertEqual(len(packed),4)
        self.assertTrue(all(type(value) is bytes and len(value)==TILE_BYTES for value in packed.values()))
        self.assertIs(packed[(-1,-1)],reader.bodies[(-1,-1)])

    def test_fork_and_reframe_keep_unpublished_overlap_without_mutating_original(self):
        reader=Reader();proof=AtlasProofWindow(self.window((-3,-3),8,8),reader)
        original=cell(23,occupancy=0);proof.write(-1,-1,original)
        proof.mark_unresolved(-130,100)
        before_calls=len(reader.calls)
        fork=proof.fork()
        self.assertEqual(len(reader.calls),before_calls)
        self.assertIs(fork.read(-1,-1),proof.read(-1,-1))
        fork.write(-1,-1,cell(24))
        self.assertEqual(proof.read(-1,-1),original)
        shifted=proof.reframe(self.window((-1,-1),8,8))
        self.assertEqual(shifted.read(-1,-1),original)
        self.assertEqual(shifted.read(6,6),UNKNOWN)
        self.assertTrue(shifted.is_unresolved(-130,100))
        self.assertIs(shifted.unresolved[(-3,1)],proof.unresolved[(-3,1)])
        self.assertTrue(shifted.clear_unresolved(-130,100))
        self.assertTrue(proof.is_unresolved(-130,100))
        same=proof.reframe(proof.window)
        same.write(-1,-1,UNKNOWN)
        self.assertEqual(proof.read(-1,-1),original)

    def test_export_then_cross_two_tiles_and_return_preserves_exact_old_proof_and_off_roi_bytes(self):
        reader=Reader()
        # 원 ROI 밖 marker도 CELL/Pi/sequence/revision bytes 그대로 보존해야 한다.
        marker=cell(101);reader.bodies[(0,0)]=body_at({4095:marker})
        proof=AtlasProofWindow(self.window(),reader)
        original=cell(102,occupancy=0);proof.write(1,1,original)
        proof.mark_unresolved(-1,64)
        exported=proof.packed_tiles(reader)
        reader.bodies.update(exported)
        first=reader.bodies[(0,0)];first_hash=sha256(first).hexdigest()
        for number,origin in enumerate(((65,0),(130,-65)),start=103):
            proof=proof.reframe(self.window(origin),immutable_tile_reader=reader)
            proof.write(origin[0]+1,origin[1]+1,cell(number))
            packed=proof.packed_tiles(reader)
            self.assertLessEqual(len(packed),4)
            reader.bodies.update(packed)
            self.assertTrue(proof.is_unresolved(-1,64))
        returned=proof.reframe(self.window(),immutable_tile_reader=reader)
        self.assertEqual(returned.read(1,1),original)
        self.assertEqual(reader.bodies[(0,0)][4095*64:],marker)
        self.assertEqual(sha256(reader.bodies[(0,0)]).hexdigest(),first_hash)
        self.assertTrue(returned.is_unresolved(-1,64))
        self.assertEqual(len(returned.cells),16)

    def test_global_unresolved_bitsets_remain_bounded_atomic_readonly_and_independent_of_roi(self):
        bits=b'\x01'+bytes(511)
        source={(index,0):bits for index in range(128)}
        proof=AtlasProofWindow(self.window(),Reader(),unresolved=source)
        source.clear()
        self.assertEqual(len(proof.unresolved),128)
        before=dict(proof.unresolved)
        with self.assertRaises(ValueError):proof.mark_unresolved(128*64,0)
        self.assertEqual(dict(proof.unresolved),before)
        self.assertFalse(proof.mark_unresolved(0,0))
        self.assertTrue(proof.clear_unresolved(0,0))
        self.assertFalse(proof.clear_unresolved(0,0))
        self.assertTrue(proof.mark_unresolved(128*64,0))
        self.assertTrue(proof.is_unresolved(128*64,0))
        self.assertEqual(len(proof.unresolved),128)
        with self.assertRaises(TypeError):proof.unresolved[(0,0)]=bits
        shifted=proof.reframe(self.window((-64,-64)))
        self.assertEqual(dict(shifted.unresolved),dict(proof.unresolved))
        self.assertTrue(shifted.clear_unresolved(128*64,0))
        self.assertTrue(proof.is_unresolved(128*64,0))

    def test_cell_validation_rejects_malformed_fields_before_any_current_value_changes(self):
        proof=AtlasProofWindow(self.window(),Reader());original=cell();proof.write(0,0,original)
        malformed=[bytearray(original),memoryview(original),original[:-1],original+b'\0']
        fields=list(CELL.unpack(original))
        for index,value in ((0,1),(1,8),(2,0),(3,math.nan),(4,-1.),(5,-1.),(6,1<<63),
                            (7,123500),(8,1<<63),(9,0),(10,1<<63),(11,1)):
            changed=list(fields);changed[index]=value;malformed.append(CELL.pack(*changed))
        malformed.append(CELL.pack(0,0,0,0.,0.,0.,0,0,0,0,0,0))
        for raw in malformed:
            with self.subTest(raw=bytes(raw)[:8]),self.assertRaises(ValueError):proof.write(0,0,raw)
            self.assertEqual(proof.read(0,0),original)
        for coords in ((True,0),(0.,0),('0',0),(-1,0),(4,0)):
            with self.subTest(coords=coords),self.assertRaises(ValueError):proof.write(*coords,original)
        self.assertEqual(proof.read(0,0),original)
        signed_zero=CELL.pack(255,0,0,-0.,0.,0.,0,0,0,0,0,0)
        proof.write(0,0,signed_zero)
        self.assertEqual(proof.read(0,0),signed_zero)
        self.assertNotEqual(proof.read(0,0),UNKNOWN)
        self.assertEqual(proof.packed_tiles(Reader())[(0,0)][:64],signed_zero)

    def test_reader_bitset_shape_and_lattice_boundaries_reject_without_fabricating_missing_proof(self):
        window=self.window()
        for value in (b'',bytes(TILE_BYTES-1),bytes(TILE_BYTES+1),bytearray(EMPTY),memoryview(EMPTY)):
            with self.subTest(kind=type(value),size=len(value)),self.assertRaises(ValueError):
                AtlasProofWindow(window,lambda *_:value)
        invalid_bits=({(0,0):bytes(511)},{(0,0):bytearray(512)},
            {(True,0):bytes(512)},{(1<<31,0):bytes(512)},
            {(index,0):bytes(512) for index in range(129)})
        for value in invalid_bits:
            with self.subTest(keys=len(value)),self.assertRaises(ValueError):
                AtlasProofWindow(window,Reader(),unresolved=value)
        proof=AtlasProofWindow(window,Reader())
        with self.assertRaises(ValueError):proof.reframe(GridWindow(MapLattice((0.,0.),.025),(0,0),4,4))
        with self.assertRaises(ValueError):proof.packed_tiles(lambda *_:bytes(12))
        self.assertEqual(proof.read(0,0),UNKNOWN)
        bad=body_at({0:CELL.pack(0,0,0,0.,0.,0.,0,0,0,0,0,0)})
        with self.assertRaises(ValueError):AtlasProofWindow(window,lambda *_:bad)
        self.assertEqual(tile_address(-1,-1),(-1,-1,4095))
