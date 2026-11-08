"""외부 pool의 불변 LIVE64 tile과 후보 EvidenceGrid 사이를 복사한다.

pool·시계·HWM·provenance를 소유하지 않는다. caller는 이동 전 pack 결과를 자기
후보 pool에 반영해야 한다. reframe은 overlap의 현재 RAW 자료를 우선하며 누락된 새
셀만 UNKNOWN으로 둔다. NumPy는 실제 copy/codec 호출에서만 import한다.
"""

from hashlib import sha256
import struct

from .local_map import EvidenceGrid
from .map_lattice import GridWindow, TILE_SIDE


LIVE64 = struct.Struct('<BQQdd31s')
LIVE_TILE_BYTES = LIVE64.size*TILE_SIDE*TILE_SIDE
STATE_MASK, SUPPORT, PERMISSION, TIME_KNOWN = 3, 4, 8, 16
CONFLICT, UNORDERED, HAS_VIEW = 32, 64, 128
_ARRAYS = ('state', 'support', 'permission', 'observed_us', 'time_known', 'conflict',
    'hit_origin', 'unordered_hit', 'hit_available_us')


class ViewKey(str):
    """31B canonical view digest의 문자열 표시. 이미 검산한 key의 재hash를 막는다."""
    __slots__ = ()

    def __new__(cls, value):
        if type(value) is not str or len(value) != 62 or any(c not in '0123456789abcdef' for c in value):
            raise ValueError('canonical 31-byte lowercase view digest required')
        return str.__new__(cls, value)

    def __copy__(self): return self

    def __deepcopy__(self, memo): return self


def view_key(value):
    """digest 충돌은 두 view를 같게 보아 independent-view 제거를 더 제한할 뿐이다."""
    if type(value) is ViewKey: return value
    if type(value) is not str or len(value) > 128: raise ValueError('original bounded raw view ID required')
    return ViewKey(sha256(value.encode('utf-8')).digest()[:31].hex())


def _views(grid):
    count = grid.spec.width*grid.spec.height
    if type(grid.hit_view) is not dict or len(grid.hit_view) > count:
        raise ValueError('bounded original cell view map required')
    result = {}
    for index, value in grid.hit_view.items():
        if type(index) is not int or not 0 <= index < count: raise ValueError('view cell outside current ROI')
        result[index] = view_key(value)
    return result


def configure_grid(grid, window):
    """기존 spec과 같은 창만 결박한다. grid 자료·counter·원 source 시각은 바꾸지 않는다."""
    if type(grid) is not EvidenceGrid or type(window) is not GridWindow or grid.spec != window.spec:
        raise ValueError('actual same-spec grid and original lattice window required')
    old = grid.rolling_window
    if old is not None and old != window: raise ValueError('configured window must move through reframe_grid')
    views = _views(grid)
    grid.hit_view = views
    grid.rolling_window = window


def _window(grid):
    if (type(grid) is not EvidenceGrid or type(grid.rolling_window) is not GridWindow
            or grid.spec != grid.rolling_window.spec):
        raise ValueError('actual configured rolling grid required')
    return grid.rolling_window


def _dtype(np):
    return np.dtype([('flags', 'u1'), ('observed', '<u8'), ('available', '<u8'),
        ('origin', '<f8', (2,)), ('view', 'u1', (31,))], align=False)


def _arrays(grid, np):
    shape = grid.spec.height, grid.spec.width
    for name in _ARRAYS:
        array = getattr(grid, name)
        dtype = 'u1' if name == 'state' else 'u8' if name in ('observed_us', 'hit_available_us') else 'f8' if name == 'hit_origin' else '?'
        if (type(array) is not np.ndarray or array.dtype != np.dtype(dtype)
                or array.shape != (shape+(2,) if name == 'hit_origin' else shape)):
            raise ValueError('exact RAW grid shape/dtype required: '+name)
    if np.any(grid.state > 2) or not np.isfinite(grid.hit_origin).all():
        raise ValueError('RAW grid state/origin is invalid')


def _tile(body, np):
    if type(body) is not bytes or len(body) != LIVE_TILE_BYTES:
        raise ValueError('immutable exact LIVE64 tile body required')
    result = np.frombuffer(body, dtype=_dtype(np)).reshape(TILE_SIDE, TILE_SIDE)
    flags = result['flags']
    if (np.any((flags & STATE_MASK) == 3) or not np.isfinite(result['origin']).all()
            or np.any(result['view'][(flags & HAS_VIEW) == 0])):
        raise ValueError('invalid LIVE64 state/origin/unused view bytes')
    return result


def _encoded_grid(grid, np):
    _arrays(grid, np)
    result = np.zeros(grid.state.shape, dtype=_dtype(np))
    flags = grid.state.copy()
    for name, bit in (('support', SUPPORT), ('permission', PERMISSION), ('time_known', TIME_KNOWN),
            ('conflict', CONFLICT), ('unordered_hit', UNORDERED)):
        flags |= getattr(grid, name).astype(np.uint8)*bit
    result['observed'] = grid.observed_us
    result['available'] = grid.hit_available_us
    result['origin'] = grid.hit_origin
    for index, value in _views(grid).items():
        row, col = divmod(index, grid.spec.width)
        flags[row, col] |= HAS_VIEW
        result['view'][row, col] = np.frombuffer(bytes.fromhex(value), dtype=np.uint8)
    result['flags'] = flags
    return result


def _slices(piece):
    roi = (slice(piece.roi_row, piece.roi_row+piece.height), slice(piece.roi_col, piece.roi_col+piece.width))
    tile = (slice(piece.tile_row, piece.tile_row+piece.height), slice(piece.tile_col, piece.tile_col+piece.width))
    return roi, tile


def pack_live_tiles(grid, baselines):
    """현재 창과 겹친 최대4개 tile 사본. 기존 tile의 창 밖 bytes는 그대로 보존한다."""
    import numpy as np
    window = _window(grid)
    if type(baselines) is not dict: raise ValueError('external immutable tile dictionary required')
    encoded = _encoded_grid(grid, np)
    result = []
    for piece in window.pieces():
        key = piece.tile_x, piece.tile_y
        present = key in baselines
        baseline = baselines[key] if present else None
        tile = _tile(baseline, np).copy() if present else np.zeros((TILE_SIDE, TILE_SIDE), dtype=_dtype(np))
        roi_slice, tile_slice = _slices(piece)
        tile[tile_slice] = encoded[roi_slice]
        body = tile.tobytes(order='C')
        result.append((key, baseline if baseline is not None and body == baseline else body))
    return tuple(result)


def _copy_piece(target, values, roi_slice, np):
    flags = values['flags']
    target.state[roi_slice] = flags & STATE_MASK
    for name, bit in (('support', SUPPORT), ('permission', PERMISSION), ('time_known', TIME_KNOWN),
            ('conflict', CONFLICT), ('unordered_hit', UNORDERED)):
        getattr(target, name)[roi_slice] = (flags & bit) != 0
    target.observed_us[roi_slice] = values['observed']
    target.hit_available_us[roi_slice] = values['available']
    target.hit_origin[roi_slice] = values['origin']
    rows, cols = np.nonzero((flags & HAS_VIEW) != 0)
    for row, col in zip(rows.tolist(), cols.tolist()):
        index = (roi_slice[0].start+row)*target.spec.width+roi_slice[1].start+col
        target.hit_view[index] = ViewKey(values['view'][row, col].tobytes().hex())


def reframe_grid(grid, new_window, live_tiles):
    """같은 후보 객체의 배열/spec만 교체한다. overlap의 실제 RAW 값이 pool 사본보다 우선한다.

    모든 검산/사본 생성이 성공한 뒤 교체한다. 외부 pool은 읽기만 하므로 caller가 원
    창의 pack 결과를 자기 후보 pool에 먼저 반영해야 떠난 셀도 재방문할 수 있다.
    """
    import numpy as np
    old_window = _window(grid)
    if (type(new_window) is not GridWindow or new_window.lattice != old_window.lattice
            or type(live_tiles) is not dict):
        raise ValueError('same original lattice and external immutable tile dictionary required')
    _arrays(grid, np)
    original_views = _views(grid)
    if new_window == old_window: return
    target = EvidenceGrid(new_window.spec, frame_id=grid.frame_id, calibration_id=grid.calibration_id,
        clock_id=grid.clock_id, kind=grid.kind, free_ttl_us=grid.free_ttl_us,
        occupied_ttl_us=grid.occupied_ttl_us, independent_view_m=grid.independent_view_m)
    for piece in new_window.pieces():
        key = piece.tile_x, piece.tile_y
        if key in live_tiles:
            body = live_tiles[key]
            roi_slice, tile_slice = _slices(piece)
            _copy_piece(target, _tile(body, np)[tile_slice], roi_slice, np)
    x0 = max(old_window.offset_xy[0], new_window.offset_xy[0])
    y0 = max(old_window.offset_xy[1], new_window.offset_xy[1])
    x1 = min(old_window.offset_xy[0]+old_window.width, new_window.offset_xy[0]+new_window.width)
    y1 = min(old_window.offset_xy[1]+old_window.height, new_window.offset_xy[1]+new_window.height)
    if x0 < x1 and y0 < y1:
        old_slice = (slice(y0-old_window.offset_xy[1], y1-old_window.offset_xy[1]),
            slice(x0-old_window.offset_xy[0], x1-old_window.offset_xy[0]))
        new_slice = (slice(y0-new_window.offset_xy[1], y1-new_window.offset_xy[1]),
            slice(x0-new_window.offset_xy[0], x1-new_window.offset_xy[0]))
        for name in _ARRAYS: getattr(target, name)[new_slice] = getattr(grid, name)[old_slice]
        # pool의 옛 has_view도 overlap에서는 원 현재 grid의 부재/값으로 덮는다.
        for index in tuple(target.hit_view):
            gx, gy = new_window.local_to_global(index)
            if x0 <= gx < x1 and y0 <= gy < y1: del target.hit_view[index]
        for index, value in original_views.items():
            gx, gy = old_window.local_to_global(index)
            new_index = new_window.global_to_local(gx, gy)
            if new_index is not None: target.hit_view[new_index] = value
    grid.spec = target.spec
    for name in _ARRAYS: setattr(grid, name, getattr(target, name))
    grid.hit_view = target.hit_view
    grid.rolling_window = new_window
