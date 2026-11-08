"""유한 grid의 지지·통행 정책·점유 근거와 원 age를 따로 보관한다.

raw pixel/도로색은 FREE 근거가 아니다. certificate의 실제 검산은 depth/free 모듈
책임이며 이 저장기는 원 source의 중복·clock/epoch 혼합·만료 전이를 관리한다.
occupied는 미검출/timeout으로 FREE가 되지 않고 새 독립-view 증거가 필요하다.
"""

from dataclasses import dataclass
import math

from ..perception.inputs import FrameStamp


UNKNOWN, FREE, OCCUPIED = 0, 1, 2


@dataclass(frozen=True)
class GridSpec:
    width: int = 64
    height: int = 64
    resolution_m: float = .05
    origin_xy: tuple[float, float] = (0., -1.6)

    def __post_init__(self):
        for value in (self.width,self.height):
            if type(value) is not int or not 1 <= value <= 128:
                raise ValueError('grid dimensions must be in 1..128')
        if type(self.resolution_m) not in (int,float) or not math.isfinite(self.resolution_m) or not .005 <= self.resolution_m <= 1:
            raise ValueError('grid resolution outside the explicit profile')
        if (type(self.origin_xy) is not tuple or len(self.origin_xy) != 2
                or any(type(v) not in (int,float) or not math.isfinite(v) or abs(v)>10000 for v in self.origin_xy)):
            raise ValueError('invalid grid origin')

    def bounds(self, col, row):
        if not 0 <= col < self.width or not 0 <= row < self.height:
            raise ValueError('cell outside grid')
        x,y = self.origin_xy
        r = self.resolution_m
        return x+col*r, y+row*r, x+(col+1)*r, y+(row+1)*r


class SourceGate:
    """stream별 HWM으로 오래된 capture를 새 관측으로 세지 않는다. 무제한 set은 없다."""

    def __init__(self, capacity=16):
        if type(capacity) is not int or not 1 <= capacity <= 64:
            raise ValueError('invalid source registry cap')
        self.capacity = capacity
        self._heads = {}

    def accept(self, stamp, payload_sha):
        if type(stamp) is not FrameStamp or type(payload_sha) is not str or len(payload_sha) != 64:
            raise ValueError('source gate requires original stamp and content hash')
        scope = stamp.source_id,stamp.stream_id,stamp.clock_id
        previous = self._heads.get(scope)
        if previous is not None:
            seq,digest,interval = previous
            if stamp.sequence < seq:
                return False
            if stamp.sequence == seq:
                if (payload_sha,stamp.captured_us) != (digest,interval):
                    raise ValueError('same source key has different bytes or capture time')
                return False
            if stamp.captured_us is not None and interval is not None and stamp.captured_us[0] < interval[0]:
                raise ValueError('source capture time moved backwards')
        elif len(self._heads) >= self.capacity:
            raise ValueError('source registry full; explicit context replacement required')
        self._heads[scope] = stamp.sequence,payload_sha,stamp.captured_us
        return True


class EvidenceGrid:
    """하나의 고정 frame/보정/clock에 묶인 grid. atlas/DB/wire 권한은 만들지 않는다."""

    def __init__(self, spec=GridSpec(), *, frame_id, calibration_id, clock_id, kind,
                 free_ttl_us=500_000, occupied_ttl_us=5_000_000, independent_view_m=.03):
        import numpy as np
        if type(spec) is not GridSpec or not frame_id or not calibration_id or not clock_id:
            raise ValueError('grid requires explicit frame/calibration/clock')
        if kind not in ('LIVE','REPLAY','SYNTHETIC'):
            raise ValueError('explicit grid input kind required')
        for value in (free_ttl_us,occupied_ttl_us):
            if type(value) is not int or not 1 <= value < 2**63:
                raise ValueError('invalid map age profile')
        if not math.isfinite(independent_view_m) or independent_view_m <= 0:
            raise ValueError('invalid independent-view separation')
        self.spec,self.frame_id,self.calibration_id,self.clock_id,self.kind = spec,frame_id,calibration_id,clock_id,kind
        self.free_ttl_us,self.occupied_ttl_us,self.independent_view_m = free_ttl_us,occupied_ttl_us,independent_view_m
        shape = spec.height,spec.width
        self.support = np.zeros(shape,dtype=np.bool_)
        self.permission = np.zeros(shape,dtype=np.bool_)
        self.state = np.zeros(shape,dtype=np.uint8)
        self.observed_us = np.zeros(shape,dtype=np.uint64)
        self.time_known = np.zeros(shape,dtype=np.bool_)
        self.conflict = np.zeros(shape,dtype=np.bool_)
        self.hit_origin = np.zeros((*shape,2),dtype=np.float64)
        self.unordered_hit = np.zeros(shape,dtype=np.bool_)
        self.hit_available_us = np.zeros(shape,dtype=np.uint64)
        self.hit_view = {}
        self.revision = 0
        self.applied_hits = self.applied_free = 0

    def set_domain(self, support, permission):
        import numpy as np
        for array in (support,permission):
            if not isinstance(array,np.ndarray) or array.shape != self.state.shape or array.dtype != np.bool_:
                raise ValueError('domain layers must be same-shape bool arrays')
        self.support[:] = support
        self.permission[:] = permission

    def _time(self, stamp):
        if type(stamp) is not FrameStamp or stamp.clock_id != self.clock_id:
            raise ValueError('map source clock mismatch')
        return None if stamp.captured_us is None else stamp.captured_us[0]

    def touched(self, bounds):
        """닫힌 XY box와 접촉하는 모든 cell. 작은 객체를 center sampling으로 누락하지 않는다."""
        if len(bounds) != 4 or any(not math.isfinite(v) for v in bounds) or bounds[0]>bounds[2] or bounds[1]>bounds[3]:
            raise ValueError('invalid hit XY bounds')
        x0,y0,x1,y1 = bounds
        ox,oy = self.spec.origin_xy
        r = self.spec.resolution_m
        c0 = max(0,math.ceil((x0-ox)/r)-1)
        c1 = min(self.spec.width-1,math.floor((x1-ox)/r))
        r0 = max(0,math.ceil((y0-oy)/r)-1)
        r1 = min(self.spec.height-1,math.floor((y1-oy)/r))
        return tuple((c,row) for row in range(r0,r1+1) for c in range(c0,c1+1))

    def add_hits(self, boxes_xy, stamp, *, view_id, origin_xy):
        import numpy as np
        observed = self._time(stamp)
        if type(view_id) is not str or len(view_id)>128 or len(origin_xy)!=2 or any(not math.isfinite(v) for v in origin_xy):
            raise ValueError('hit view identity/origin missing')
        touched = self._hit_mask(boxes_xy)
        count = int(np.count_nonzero(touched))
        self.conflict[touched] |= self.state[touched] == FREE
        self.state[touched] = OCCUPIED
        if observed is None:
            # 새 hit는 과거 free/hit의 known 시각을 상속하지 않는다.
            updated = touched
            self.time_known[updated] = False
            self.observed_us[updated] = 0
            self.unordered_hit[updated] = True
            self.hit_available_us[updated] = np.maximum(self.hit_available_us[updated], np.uint64(stamp.available_us))
        else:
            # uint64끼리 비교하여 큰 원 시각도 float promotion/반올림 없이 보존한다.
            value = np.uint64(observed)
            blocked = self.unordered_hit & (value <= self.hit_available_us)
            updated = touched & ~blocked & (~self.time_known | (value >= self.observed_us))
            self.observed_us[updated] = value
            self.time_known[updated] = True
            self.unordered_hit[updated] = False
            self.hit_available_us[updated] = np.uint64(stamp.available_us)
        self.hit_origin[updated] = origin_xy
        for index in np.flatnonzero(updated).tolist():
            self.hit_view[index] = view_id
        if count:
            self.revision += 1
            self.applied_hits += count
        return count

    def _hit_mask(self, boxes_xy):
        """닫힌 ceil-1/floor rectangle union. 변환 전 clip으로 거대 off-grid 좌표도 안전하다."""
        import numpy as np
        from ..perception.point_batch import PointBoundsBatch
        if type(boxes_xy) is PointBoundsBatch:
            lower, upper = boxes_xy.lower[:, :2], boxes_xy.upper[:, :2]
        else:
            values = np.asarray(boxes_xy if type(boxes_xy) is np.ndarray else tuple(boxes_xy), dtype=np.float64)
            if not values.size:
                return np.zeros(self.state.shape, dtype=bool)
            if values.ndim != 2 or values.shape[1] != 4 or not np.isfinite(values).all():
                raise ValueError('hit input requires finite XY bounds')
            lower, upper = values[:, :2], values[:, 2:]
            if np.any(lower > upper):
                raise ValueError('invalid hit XY bounds')
        ox, oy = self.spec.origin_xy
        r, width, height = self.spec.resolution_m, self.spec.width, self.spec.height
        with np.errstate(over='ignore', invalid='ignore'):
            # clip은 원 quotient가 grid 주변 범위일 때 아무 값도 바꾸지 않는다.
            c0 = np.maximum(0., np.ceil(np.clip((lower[:, 0] - ox) / r, -1., width+1.)) - 1).astype(np.intp)
            c1 = np.minimum(width-1., np.floor(np.clip((upper[:, 0] - ox) / r, -1., width+1.))).astype(np.intp)
            r0 = np.maximum(0., np.ceil(np.clip((lower[:, 1] - oy) / r, -1., height+1.)) - 1).astype(np.intp)
            r1 = np.minimum(height-1., np.floor(np.clip((upper[:, 1] - oy) / r, -1., height+1.))).astype(np.intp)
        keep = (c0 <= c1) & (r0 <= r1)
        c0, c1, r0, r1 = c0[keep], c1[keep]+1, r0[keep], r1[keep]+1
        # bounded depth batch는8192행이다. 기존 tuple API의 별도 상한을 새로 만들지는 않는다.
        dtype = np.int32 if len(lower) <= 8192 else np.int64
        difference = np.zeros((height+1, width+1), dtype=dtype)
        np.add.at(difference, (r0, c0), 1)
        np.add.at(difference, (r1, c0), -1)
        np.add.at(difference, (r0, c1), -1)
        np.add.at(difference, (r1, c1), 1)
        np.cumsum(difference, axis=0, dtype=dtype, out=difference)
        np.cumsum(difference, axis=1, dtype=dtype, out=difference)
        return difference[:-1, :-1] > 0

    def add_free(self, cells, stamp, *, view_id, origin_xy, certificate_complete):
        """호출자가 검산한 whole-cell certificate만 받는다. 원 time을 보존한다."""
        observed = self._time(stamp)
        if not certificate_complete or observed is None:
            return 0
        applied = 0
        for col,row in cells:
            self.spec.bounds(col,row)
            if not (self.support[row,col] and self.permission[row,col]):
                continue
            if self.time_known[row,col] and observed < int(self.observed_us[row,col]):
                continue
            if self.state[row,col] == OCCUPIED:
                if self.unordered_hit[row,col]:
                    self.conflict[row,col] = True
                    continue
                baseline = math.hypot(*(a-b for a,b in zip(origin_xy,self.hit_origin[row,col])))
                if baseline < self.independent_view_m:
                    self.conflict[row,col] = True
                    continue
            self.state[row,col] = FREE
            self.observed_us[row,col] = observed
            self.time_known[row,col] = True
            self.conflict[row,col] = False
            self.hit_view.pop(row*self.spec.width+col,None)
            applied += 1
        if applied:
            self.revision += 1
            self.applied_free += applied
        return applied

    def snapshot(self, now_us, *, clock_id):
        import numpy as np
        if type(now_us) is not int or not 0 <= now_us < 2**64 or clock_id != self.clock_id:
            raise ValueError('snapshot clock context mismatch')
        age = np.full(self.state.shape,np.iinfo(np.uint32).max,dtype=np.uint32)
        valid_time = self.time_known & (self.observed_us<=now_us)
        elapsed = np.full(self.state.shape,np.iinfo(np.uint64).max,dtype=np.uint64)
        elapsed[valid_time] = np.uint64(now_us)-self.observed_us[valid_time]
        age[valid_time] = np.minimum(elapsed[valid_time]//1000,np.iinfo(np.uint32).max).astype(np.uint32)
        state = self.state.copy()
        stale_free = (state==FREE)&(~valid_time | (elapsed>self.free_ttl_us))
        stale_hit = (state==OCCUPIED)&(~valid_time | (elapsed>self.occupied_ttl_us))
        state[stale_free|stale_hit] = UNKNOWN
        # 저장한 hit는 남겨 두므로 만료 후 free로 부활하지 않는다.
        state[(state==FREE)&~(self.support&self.permission)] = UNKNOWN
        arrays = dict(state=state,age_ms=age,support=self.support.copy(),permission=self.permission.copy(),
                      conflict=self.conflict.copy(),unordered_hit=self.unordered_hit.copy())
        for value in arrays.values():
            value.setflags(write=False)
        return dict(frame_id=self.frame_id,calibration_id=self.calibration_id,clock_id=self.clock_id,
                    kind=self.kind,revision=self.revision,spec=self.spec,arrays=arrays,
                    counts=dict(unknown=int(np.count_nonzero(state==UNKNOWN)),free=int(np.count_nonzero(state==FREE)),
                                occupied=int(np.count_nonzero(state==OCCUPIED))),live_authority=False,world_valid=False)
