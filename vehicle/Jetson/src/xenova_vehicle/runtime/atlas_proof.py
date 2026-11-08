"""현재 ROI의 CELL64 작업 사본과 전역 unresolved bitset만 보유한다.

reader(tx,ty)는 외부 API가 아닌 같은 compute owner의 내부 port다. caller가 원 pool
read-lease/불변 버전을 고정한 동안 검증된 EVIDENCE bytes 또는 부재(None)를 준다.
helper는 pool/lease/시각/ID/DB/승인을 만들지 않으며 ROI 밖 본문이나 atlas 전체를 보관하지
않는다. ROI 밖 CELL 의미 검증은 원 버전의 owner 책임이고 여기서는 읽는 CELL만 검사한다.

reframe은 overlap의 미채택 변경을 보존한다. 떠나는 ROI의 변경은 caller가 먼저
packed_tiles로 같은 AtlasDraft에 보존해야 한다. 단순 reframe은 저장/게시 ACK가 아니다.
"""

import math
from types import MappingProxyType

from ..mapping.map_lattice import GridWindow, tile_address
from ..storage.contracts import MAX_U63
from ..storage.records import CELL, TILE_BYTES


_SIDE = 64
_BITSET_BYTES = 512
_MAX_KEYS = 128
_UNKNOWN = CELL.pack(255,0,0,0.,0.,0.,0,0,0,0,0,0)
_EMPTY = _UNKNOWN * (_SIDE*_SIDE)


def _tile_key(value):
    if (type(value) is not tuple or len(value)!=2
            or any(type(v) is not int or not -(1<<31)<=v<(1<<31) for v in value)):
        raise ValueError('signed int32 global tile pair required')
    return value


def _cell(raw):
    if type(raw) is not bytes or len(raw)!=CELL.size:
        raise ValueError('immutable original CELL64 bytes required')
    values=CELL.unpack(raw)
    occupancy,flags,mask,hmin,hmax,bound,provenance,lower,upper,sequence,geometry,reserved=values
    if occupancy not in (0,100,255) or flags&~7 or mask&~63 or reserved:
        raise ValueError('invalid original cell enum/reserved')
    if not provenance:
        if occupancy!=255 or flags or any(values[2:]):
            raise ValueError('unobserved cell must preserve canonical UNKNOWN')
        return raw
    if (provenance>MAX_U63 or not mask or not all(math.isfinite(v) for v in (hmin,hmax,bound))
            or hmin>hmax or bound<0 or lower>upper or upper>MAX_U63 or not 0<sequence<=MAX_U63
            or geometry>MAX_U63 or not flags&1 and occupancy!=255):
        raise ValueError('invalid original observed CELL64')
    return raw


def _body(reader, key):
    value=reader(*key)
    if value is None:return _EMPTY
    if type(value) is not bytes or len(value)!=TILE_BYTES:
        raise ValueError('reader must return immutable full EVIDENCE bytes or None')
    return value


class AtlasProofWindow:
    """global(col,row)→원 CELL64는 최대4096개, unresolved는 최대128×512B다.

    global 주소를 받는 read/write는 현재 창 안에서만 유효하다. unresolved 주소는 창 밖도
    허용하며 이동으로 지우지 않는다. 반환 mapping은 readonly이고 bytes는 불변 공유한다.
    """

    def __init__(self, window, immutable_tile_reader, *, unresolved=None):
        if type(window) is not GridWindow or not callable(immutable_tile_reader):
            raise ValueError('actual GridWindow and internal immutable tile reader required')
        if unresolved is not None and (type(unresolved) not in (dict,MappingProxyType) or len(unresolved)>_MAX_KEYS):
            raise ValueError('bounded original unresolved tile bitsets required')
        self._window=window;self._reader=immutable_tile_reader;self._cells={};self._unresolved={}
        if unresolved is not None:
            for key,value in unresolved.items():
                _tile_key(key)
                if type(value) is not bytes or len(value)!=_BITSET_BYTES:
                    raise ValueError('immutable512-byte unresolved tile bitset required')
                if any(value):self._unresolved[key]=value
        for piece in window.pieces():
            body=_body(immutable_tile_reader,(piece.tile_x,piece.tile_y))
            for row in range(piece.height):
                for col in range(piece.width):
                    x=piece.tile_col+col;y=piece.tile_row+row
                    key=(piece.tile_x*_SIDE+x,piece.tile_y*_SIDE+y)
                    offset=(y*_SIDE+x)*CELL.size
                    self._cells[key]=_cell(body[offset:offset+CELL.size])

    @property
    def window(self):return self._window

    @property
    def cells(self):return MappingProxyType(self._cells)

    @property
    def unresolved(self):return MappingProxyType(self._unresolved)

    def _inside(self, col, row):
        if self.window.global_to_local(col,row) is None:
            raise ValueError('global cell is outside current proof window')
        return col,row

    def read(self, col, row):
        return self._cells[self._inside(col,row)]

    def write(self, col, row, cell64):
        key=self._inside(col,row);value=_cell(cell64)
        self._cells[key]=value

    def is_unresolved(self, col, row):
        tx,ty,index=tile_address(col,row);bits=self._unresolved.get((tx,ty))
        return bits is not None and bool(bits[index//8] & (1<<(index%8)))

    def mark_unresolved(self, col, row):
        tx,ty,index=tile_address(col,row);key=(tx,ty);original=self._unresolved.get(key)
        if original is None and len(self._unresolved)>=_MAX_KEYS:
            raise ValueError('global unresolved tile cap128 exceeded')
        if original is not None and original[index//8] & (1<<(index%8)):return False
        bits=bytearray(_BITSET_BYTES) if original is None else bytearray(original)
        bits[index//8]|=1<<(index%8);self._unresolved[key]=bytes(bits)
        return True

    def clear_unresolved(self, col, row):
        tx,ty,index=tile_address(col,row);key=(tx,ty);original=self._unresolved.get(key)
        if original is None or not original[index//8] & (1<<(index%8)):return False
        bits=bytearray(original);bits[index//8]&=~(1<<(index%8))
        if any(bits):self._unresolved[key]=bytes(bits)
        else:del self._unresolved[key]
        return True

    def fork(self):
        """원 reader를 호출하지 않는다. ROI/bitset dict만 분리하며 원 bytes는 공유한다."""
        result=object.__new__(AtlasProofWindow)
        result._window=self._window;result._reader=self._reader
        result._cells=dict(self._cells);result._unresolved=dict(self._unresolved)
        return result

    def reframe(self, newwindow, *, immutable_tile_reader=None):
        if type(newwindow) is not GridWindow or newwindow.lattice!=self.window.lattice:
            raise ValueError('reframe must preserve original fixed map lattice')
        reader=self._reader if immutable_tile_reader is None else immutable_tile_reader
        if not callable(reader):raise ValueError('internal immutable tile reader required')
        if newwindow==self.window:
            result=self.fork();result._reader=reader;return result
        result=AtlasProofWindow(newwindow,reader,unresolved=self._unresolved)
        for key,value in self._cells.items():
            if newwindow.global_to_local(*key) is not None:result._cells[key]=value
        return result

    def packed_tiles(self, baseline_reader):
        """현재 ROI≤4 full body를 돌려준다. ROI 밖 bytes와 원 CELL 값은 그대로 둔다.

        변경이 없는 body는 reader의 원 bytes를 그대로 반환한다. 변경 tile마다 bytearray
        하나만 만들고 결과는 immutable bytes다. 호출 자체는 pool/DB에 버전을 게시하지 않는다.
        """
        if not callable(baseline_reader):raise ValueError('current original baseline reader required')
        result={}
        for piece in self.window.pieces():
            key=(piece.tile_x,piece.tile_y);baseline=_body(baseline_reader,key);body=None
            for row in range(piece.height):
                for col in range(piece.width):
                    x=piece.tile_col+col;y=piece.tile_row+row;offset=(y*_SIDE+x)*CELL.size
                    cell=self._cells[(piece.tile_x*_SIDE+x,piece.tile_y*_SIDE+y)]
                    if cell!=baseline[offset:offset+CELL.size]:
                        if body is None:body=bytearray(baseline)
                        body[offset:offset+CELL.size]=cell
            result[key]=baseline if body is None else bytes(body)
        return result
