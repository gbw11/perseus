"""같은 Jetson의 원 pose 표본을 합성 RGB·RIGHT 관측으로 만든다.

GT pose/scene은 renderer 내부에만 전달한다. 출력은 SYNTHETIC이며 실제 OAK 노출,
장치 시계, 실측 보정으로 승격하지 않는다. 물리/렌더 owner의 원 monotonic 구간과
소비 기한을 보존하며, 오래 걸린 렌더를 완료 시각으로 새 관측처럼 만들지 않는다.
"""

from dataclasses import dataclass
import threading
import time
import uuid

from ..perception.inputs import FrameStamp, ImageFrame, PerceptionInput
from .capture import DepthEffects, SyntheticCamera
from .scene import Scene, SceneSnapshot


MAX_CAPTURE_LIFETIME_US = 500_000
U64_MAX = 2**64 - 1


def j_us():
    return time.monotonic_ns() // 1000


def _uint(value):
    if type(value) is not int or not 0 <= value <= U64_MAX:
        raise ValueError('wall sensor time/sequence must be uint64')
    return value


def _uuid(value):
    if type(value) is not str:
        raise ValueError('wall sensor epoch must be a canonical nonzero UUID')
    parsed = uuid.UUID(value)
    if str(parsed) != value or parsed.int == 0:
        raise ValueError('wall sensor epoch must be a canonical nonzero UUID')


def _interval(value):
    if type(value) is not tuple or len(value) != 2:
        raise ValueError('original wall capture interval must be an exact pair')
    lower, upper = map(_uint, value)
    if lower > upper:
        raise ValueError('original wall capture interval regressed')
    return lower, upper


@dataclass(frozen=True)
class WallPoseSample:
    """물리 owner가 원 J 두 번 사이에 읽은 후륜축 pose. renderer 입력 전용이다.

read_pose 호출 시 상태가 한 tick으로 고정되도록 호출자가 물리 owner의 lock 또는
단일 스레드 소유를 보장한다. 이 값 자체를 odometry/VehicleSample로 보내지 않는다.
"""

    epoch: str
    sequence: int
    captured_j_us: tuple[int, int]
    pose: tuple[float, float, float]

    def __post_init__(self):
        import math
        _uuid(self.epoch)
        _uint(self.sequence)
        _interval(self.captured_j_us)
        if (type(self.pose) is not tuple or len(self.pose) != 3
                or any(type(v) not in (float, int) or not math.isfinite(v) for v in self.pose)):
            raise ValueError('frozen rear-axle pose must be three finite builtin numbers')

    @classmethod
    def sample(cls, epoch, sequence, read_pose, *, clock=j_us):
        """floor(monotonic_ns/1000) clock의 원 양자화 구간으로 감싼다.

        마지막 표본이 속한 1us bucket 전체를 최초 upper에 포함한다. 같은 us로 읽혀도
        실제 구간이 0이었다고 주장하지 않는다. 소비자가 구간/E를 사후 갱신하지 않는다.
        """
        before = _uint(clock())
        pose = read_pose()
        after = _uint(clock())
        if after < before:
            raise ValueError('original wall pose sampling clock regressed')
        return cls(epoch, sequence, (before, _uint(after + 1)), pose)


@dataclass(frozen=True)
class WallCapture:
    """GT가 없는 owned sensor 입력과 원 기한. NN enqueue/수신 시각과 별개다."""

    observation: PerceptionInput
    profile_sha256: str
    captured_j_us: tuple[int, int]
    completed_j_us: int
    expires_j_us: int

    def __post_init__(self):
        self._check(allow_expired=False)

    def _check(self, *, allow_expired):
        if (type(self.profile_sha256) is not str or len(self.profile_sha256) != 64
                or any(c not in '0123456789abcdef' for c in self.profile_sha256)):
            raise ValueError('wall capture requires its original experiment profile SHA256')
        lower, upper = _interval(self.captured_j_us)
        _uint(self.completed_j_us)
        _uint(self.expires_j_us)
        if (not lower <= upper <= self.completed_j_us
                or not upper < self.expires_j_us <= lower + MAX_CAPTURE_LIFETIME_US
                or not allow_expired and self.completed_j_us >= self.expires_j_us):
            raise ValueError('wall capture completion/lifetime exceeds the original capture bound')
        value = self.observation
        if type(value) is not PerceptionInput or value.kind != 'SYNTHETIC' or value.nn is not None:
            raise ValueError('wall capture must retain an unprocessed SYNTHETIC input')
        rgb, depth = value.rgb, value.depth
        if (depth is None or (rgb.width, rgb.height, rgb.encoding) != (640, 360, 'NV12')
                or (depth.width, depth.height, depth.encoding) != (640, 480, 'RAW16_LE')
                or rgb.stamp.sequence != depth.stamp.sequence
                or rgb.stamp.stream_id != 'rgb_video' or depth.stamp.stream_id != 'depth_right'):
            raise ValueError('wall capture must contain one exact RGB/RIGHT pair')
        if (not rgb.stamp.source_id.startswith('sim-wall:')
                or not rgb.stamp.clock_id.startswith('jetson-monotonic:')):
            raise ValueError('wall capture must declare its explicit development source/clock')
        for frame in (rgb, depth):
            if (frame.stamp.captured_us != self.captured_j_us
                    or frame.stamp.available_us != self.completed_j_us or frame.exposure_us != 0):
                raise ValueError('wall capture changed its original pose-sampling interval/availability')

    @property
    def receipt(self):
        """수집 식별자·해시만 반환한다. scene/pose/목표/free GT는 포함하지 않는다."""
        value, stamp = self.observation, self.observation.rgb.stamp
        return dict(input_kind=value.kind, profile_sha256=self.profile_sha256,
            calibration_id=value.calibration_id, source_id=stamp.source_id, clock_id=stamp.clock_id,
            sequence=str(stamp.sequence), captured_j_us=list(map(str, self.captured_j_us)),
            completed_j_us=str(self.completed_j_us), expires_j_us=str(self.expires_j_us),
            rgb_sha256=value.rgb.sha256, depth_sha256=value.depth.sha256,
            timestamp_basis='original_host_pose_sample_not_OAK_exposure', live_authority=False)


@dataclass(frozen=True)
class WallDiagnosticCapture(WallCapture):
    """만료 원본도 보존하는 별도 진단 값. 실시간 수락·publish를 제공하지 않는다."""

    def __post_init__(self):
        self._check(allow_expired=True)

    @property
    def expired(self):
        return self.completed_j_us >= self.expires_j_us

    @property
    def receipt(self):
        return dict(super().receipt, diagnostic_only=True, expired=self.expired)


class WallSyntheticCamera:
    """원 sample당 한 번 렌더한다. 만료/중복을 새 순번·시각으로 재발급하지 않는다.

profile은 명시적 개발 전용이며 운영 A/ResidentPerception에서 자동 선택하지 않는다.
동기 렌더/native 호출 중단은 바깥 supervisor 책임이다. 출력/receipt에는 GT가 없다.
"""

    def __init__(self, profile, scene, *, epoch, clock_epoch, effects=DepthEffects(), clock=j_us,
                 motion_profile=None):
        from .profile import DevelopmentProfile
        if type(profile) is not DevelopmentProfile or type(scene) is not Scene:
            raise ValueError('explicit immutable development profile/scene required')
        _uuid(epoch)
        _uuid(clock_epoch)
        profile.check_scene(scene)
        calibration = profile.calibration()
        if motion_profile is not None:
            from .motion_profile import DevelopmentMotionProfile
            if type(motion_profile) is not DevelopmentMotionProfile:
                raise ValueError('explicit exact development motion profile required')
            motion_profile.check_development_profile(profile)
            calibration = motion_profile.bound_calibration
        self._profile, self._scene = profile, scene
        self._motion_profile = motion_profile
        self._epoch = epoch
        self._source_id = 'sim-wall:' + epoch
        self._clock_id = 'jetson-monotonic:' + clock_epoch
        self._clock = clock
        self._owner = threading.get_ident()
        # whole vehicle manifest와 결박할 때는 최초 pixel 생산부터 같은 bound calibration을
        # 쓴다. 과거 neutral 관측의 calibration ID를 바꿔 현재 원본처럼 재발급하지 않는다.
        self._renderer = SyntheticCamera(calibration, effects)
        self._calibration = self._renderer.calibration
        if motion_profile is not None and self._calibration.sha256 != calibration.sha256:
            raise ValueError('sensor effects changed the pinned development motion calibration')
        self._last_sequence = self._last_upper = self._last_clock = None
        self._closed = False
        self._counts = dict(attempted=0, emitted=0, diagnostic_emitted=0,
                            expired_before_render=0, expired_after_render=0)

    def _owned(self):
        if threading.get_ident() != self._owner:
            raise RuntimeError('wall sensor belongs to one renderer thread')

    def _now(self):
        now = _uint(self._clock())
        if self._last_clock is not None and now < self._last_clock:
            raise ValueError('actual wall sensor clock regressed')
        self._last_clock = now
        return now

    @property
    def calibration(self):
        return self._calibration

    @property
    def source_id(self):
        return self._source_id

    @property
    def clock_id(self):
        return self._clock_id

    @property
    def motion_profile_sha256(self):
        return None if self._motion_profile is None else self._motion_profile.sha256

    @property
    def counts(self):
        self._owned()
        return dict(self._counts)

    def capture(self, sample, *, expires_j_us):
        """원 pose J구간과 호출자가 정한 원 E를 보존한다. 만료면 None을 반환한다."""
        return self._render(sample, expires_j_us=expires_j_us, diagnostic=False)

    def render_diagnostic(self, sample, *, expires_j_us):
        """만료된 정지 장면도 원 시각으로 보존한다. live 드롭을 품질 성공으로 덮지 않는다.

        후속 NN은 별도로 처음부터 정한 원 NN 기한을 사용해야 한다. 렌더 완료 때
        capture/NN 기한을 새로 발급하지 않는다. 실시간 경로는 capture를 사용한다.
        """
        return self._render(sample, expires_j_us=expires_j_us, diagnostic=True)

    def _render(self, sample, *, expires_j_us, diagnostic):
        self._owned()
        if self._closed:
            raise RuntimeError('wall sensor is closed')
        if type(sample) is not WallPoseSample or sample.epoch != self._epoch:
            raise ValueError('wall sensor requires its original pose-sampling session')
        lower, upper = sample.captured_j_us
        _uint(expires_j_us)
        if not upper < expires_j_us <= lower + MAX_CAPTURE_LIFETIME_US:
            raise ValueError('original wall sensor E exceeds the capture lifetime')
        if (self._last_sequence is not None and (sample.sequence <= self._last_sequence
                or lower <= self._last_upper)):
            raise ValueError('wall sensor sequence/capture regressed or was already consumed')
        now = self._now()
        if now < upper:
            raise ValueError('wall sensor received a future pose sample')
        self._last_sequence, self._last_upper = sample.sequence, upper
        self._counts['attempted'] += 1
        if now >= expires_j_us:
            self._counts['expired_before_render'] += 1
            if not diagnostic:
                return None
        # 기존 offline producer는 pixel 렌더에만 사용한다. 출력 stamp는 실제 원 pose
        # 표본으로 명시한 별도 SYNTHETIC producer가 만들며 SDK/device 시각을 쓰지 않는다.
        snapshot = SceneSnapshot(self._epoch, sample.sequence, upper, sample.pose, self._scene)
        rendered = self._renderer.capture(snapshot)
        completed = self._now()
        if completed >= expires_j_us:
            self._counts['expired_after_render'] += 1
            if not diagnostic:
                return None
        def image(frame, stream):
            stamp = FrameStamp(self._source_id, stream, sample.sequence, self._clock_id,
                               sample.captured_j_us, completed)
            return ImageFrame(stamp, frame.width, frame.height, frame.encoding, frame.payload, 0)
        observation = PerceptionInput('SYNTHETIC', self.calibration.sha256,
            image(rendered.rgb, 'rgb_video'), image(rendered.depth, 'depth_right'))
        capture_type = WallDiagnosticCapture if diagnostic else WallCapture
        capture = capture_type(observation, self._profile.sha256, sample.captured_j_us,
                               completed, expires_j_us)
        self._counts['diagnostic_emitted' if diagnostic else 'emitted'] += 1
        return capture

    def close(self):
        """동일 owner가 더는 렌더하지 않게 한다. Device/운영 모델에는 관여하지 않는다."""
        self._owned()
        self._closed = True
        self._renderer = self._scene = None
