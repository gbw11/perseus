"""고정 MAP 격자의 정수 주소와 이동 ROI를 분리한다. 관측/권한/시각은 만들지 않는다.

anchor와 해상도는 지도 동안 고정한다. ROI 이동은 signed cell offset만 바꾸며,
음수 주소도 floor division으로 같은 64cell tile에 대응한다.
"""

from dataclasses import dataclass
from hashlib import sha256
import json
import math

from .local_map import GridSpec


TILE_SIDE = 64
_MIN_CELL = -(2**31)*TILE_SIDE
_MAX_CELL = (2**31)*TILE_SIDE-1


def _cell(value):
    if type(value) is not int or not _MIN_CELL <= value <= _MAX_CELL:
        raise ValueError('signed global cell exceeds original int32 tile address')
    return value


def tile_address(col, row):
    """global(col,row)를 (signed tile_x,tile_y,row-major cell index)로 바꾼다."""
    tx, x = divmod(_cell(col), TILE_SIDE)
    ty, y = divmod(_cell(row), TILE_SIDE)
    return tx, ty, y*TILE_SIDE+x


@dataclass(frozen=True, slots=True)
class MapLattice:
    """초기 GridSpec의 anchor와 해상도다. ROI origin을 새 anchor로 삼지 않는다."""
    anchor_xy: tuple
    resolution_m: float

    def __post_init__(self):
        GridSpec(1, 1, self.resolution_m, self.anchor_xy)

    @classmethod
    def from_spec(cls, spec):
        if type(spec) is not GridSpec: raise ValueError('original GridSpec required')
        return cls(spec.origin_xy, spec.resolution_m)

    def origin(self, offset_xy):
        if type(offset_xy) is not tuple or len(offset_xy) != 2:
            raise ValueError('signed two-cell offset required')
        return tuple(math.fsum((anchor, _cell(offset)*self.resolution_m))
            for anchor, offset in zip(self.anchor_xy, offset_xy))

    def cell_at(self, xy):
        """유한 nominal 위치의 ROI 선택용 주소다. whole-cell 통행 검증을 대신하지 않는다."""
        if (type(xy) is not tuple or len(xy) != 2
                or any(type(v) not in (int,float) or not math.isfinite(v) for v in xy)):
            raise ValueError('finite nominal map position required')
        return tuple(_cell(math.floor((value-anchor)/self.resolution_m))
            for value, anchor in zip(xy, self.anchor_xy))


@dataclass(frozen=True, slots=True)
class TileSlice:
    """한 ROI와 tile의 교집합이다. 셀별 Python 객체를 만들지 않는 array copy 경계다."""
    tile_x: int
    tile_y: int
    roi_col: int
    roi_row: int
    tile_col: int
    tile_row: int
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class GridWindow:
    """같은 고정 격자의 최대64x64 창. offset은 (global col,global row)다."""
    lattice: MapLattice
    offset_xy: tuple
    width: int
    height: int

    def __post_init__(self):
        if type(self.lattice) is not MapLattice or type(self.offset_xy) is not tuple or len(self.offset_xy) != 2:
            raise ValueError('original lattice and signed cell offset required')
        for value in (self.width, self.height):
            if type(value) is not int or not 1 <= value <= TILE_SIDE:
                raise ValueError('rolling world ROI must fit 64x64 cells')
        for offset, size in zip(self.offset_xy, (self.width,self.height)):
            _cell(offset); _cell(offset+size-1)
        self.spec

    @classmethod
    def from_spec(cls, spec):
        if type(spec) is not GridSpec: raise ValueError('original GridSpec required')
        return cls(MapLattice.from_spec(spec), (0,0), spec.width, spec.height)

    @property
    def spec(self):
        return GridSpec(self.width, self.height, self.lattice.resolution_m, self.lattice.origin(self.offset_xy))

    def shifted(self, offset_xy):
        """새 절대 cell offset이다. 반복 float 덧셈이나 anchor 재설정이 아니다."""
        return GridWindow(self.lattice, offset_xy, self.width, self.height)

    def local_to_global(self, index):
        if type(index) is not int or not 0 <= index < self.width*self.height:
            raise ValueError('local cell index outside original ROI')
        row, col = divmod(index, self.width)
        return self.offset_xy[0]+col, self.offset_xy[1]+row

    def global_to_local(self, col, row):
        col, row = _cell(col)-self.offset_xy[0], _cell(row)-self.offset_xy[1]
        return row*self.width+col if 0 <= col < self.width and 0 <= row < self.height else None

    def pieces(self):
        """최대 네 tile의 exact 교집합. 순서는 tile_y, tile_x다."""
        x0, y0 = self.offset_xy; x1, y1 = x0+self.width, y0+self.height
        result=[]
        for ty in range(y0//TILE_SIDE, (y1-1)//TILE_SIDE+1):
            for tx in range(x0//TILE_SIDE, (x1-1)//TILE_SIDE+1):
                left, top = max(x0,tx*TILE_SIDE), max(y0,ty*TILE_SIDE)
                right, bottom = min(x1,(tx+1)*TILE_SIDE), min(y1,(ty+1)*TILE_SIDE)
                result.append(TileSlice(tx,ty,left-x0,top-y0,left-tx*TILE_SIDE,top-ty*TILE_SIDE,
                    right-left,bottom-top))
        return tuple(result)


@dataclass(frozen=True, slots=True)
class RollingPolicy:
    """명시한 local ROI 배치 정책이다. 정합/센서 오차나 원 관측 기한을 줄이지 않는다."""
    margin_cells: int
    lookahead_cells: int

    def __post_init__(self):
        if type(self.margin_cells) is not int or not 1 <= self.margin_cells <= 31:
            raise ValueError('explicit ROI margin must be in 1..31 cells')
        if type(self.lookahead_cells) is not int or not 0 <= self.lookahead_cells <= 32:
            raise ValueError('explicit ROI lookahead must be in 0..32 cells')

    @property
    def sha256(self):
        raw=json.dumps(dict(margin_cells=self.margin_cells,lookahead_cells=self.lookahead_cells),
            sort_keys=True,separators=(',',':')).encode('ascii')
        return sha256(raw).hexdigest()

    def check_window(self, window):
        if (type(window) is not GridWindow or 2*self.margin_cells >= min(window.width,window.height)
                or self.lookahead_cells > min(window.width,window.height)//2):
            raise ValueError('ROI margin/lookahead exceeds original window')

    def window_for(self, window, position_xy, yaw):
        """caller가 정합한 nominal MAP pose만 전달한다. 그대로면 같은 원 window를 반환한다."""
        self.check_window(window)
        if type(yaw) not in (int,float) or not math.isfinite(yaw):raise ValueError('finite nominal MAP heading required')
        window.lattice.cell_at(position_xy)
        distance=self.lookahead_cells*window.lattice.resolution_m
        target=window.lattice.cell_at((position_xy[0]+distance*math.cos(yaw),position_xy[1]+distance*math.sin(yaw)))
        col,row=target[0]-window.offset_xy[0],target[1]-window.offset_xy[1]
        margin=self.margin_cells
        if margin <= col < window.width-margin and margin <= row < window.height-margin:return window
        return window.shifted((target[0]-window.width//2,target[1]-window.height//2))


def atlas_state_fields(window, policy):
    """현재 창의 작은 versioned META다. tile 본문/관측/권한/새 식별자는 넣지 않는다."""
    if type(policy) is not RollingPolicy:raise ValueError('explicit original rolling policy required')
    policy.check_window(window)
    return dict(profile='atlas-rolling-1',lattice_origin_xy_m=list(window.lattice.anchor_xy),
        roi_origin_cell=list(window.offset_xy),tile_cells=TILE_SIDE,
        live_restore='RETAINED_EVIDENCE_ONLY',policy_sha256=policy.sha256)


def read_atlas_window(value, spec, *, policy=None):
    """검산된 바깥 state.spec와 주소를 대조한다. 원 CELL/Scene/현재성 승인은 별도다."""
    names={'profile','lattice_origin_xy_m','roi_origin_cell','tile_cells','live_restore','policy_sha256'}
    if (type(value) is not dict or set(value)!=names or value['profile']!='atlas-rolling-1'
            or type(value['tile_cells']) is not int or value['tile_cells']!=TILE_SIDE
            or value['live_restore']!='RETAINED_EVIDENCE_ONLY' or type(spec) is not GridSpec):
        raise ValueError('exact supported atlas metadata and original grid spec required')
    anchor,offset=value['lattice_origin_xy_m'],value['roi_origin_cell'];key=value['policy_sha256']
    if (type(anchor) is not list or len(anchor)!=2 or type(offset) is not list or len(offset)!=2
            or type(key) is not str or len(key)!=64 or any(c not in '0123456789abcdef' for c in key)):
        raise ValueError('original bounded lattice/policy metadata required')
    window=GridWindow(MapLattice(tuple(anchor),spec.resolution_m),tuple(offset),spec.width,spec.height)
    if window.spec!=spec:raise ValueError('ROI origin differs from immutable lattice and signed cell offset')
    if policy is not None:
        if type(policy) is not RollingPolicy or key!=policy.sha256:raise ValueError('original rolling policy SHA differs')
        policy.check_window(window)
    return window
