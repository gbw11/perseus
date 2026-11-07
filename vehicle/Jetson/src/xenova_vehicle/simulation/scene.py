"""고정 scene과 원자적인 합성 snapshot 값. 평가 GT는 관측과 별도로 돌려준다.

저장된 geometry는 지정 simulator commit의 한 코스를 정규화한 입력 자산이다.
현재 simulator의 MQTT/물리 스레드나 UI를 변경하지 않으며 실측 차체/보정이 아니다.
"""

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import uuid


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False) + '\n').encode('ascii')


def _finite(values):
    return all(type(v) in (int, float) and math.isfinite(v) for v in values)


@dataclass(frozen=True)
class Scene:
    """JSON 원 bytes만 보유한다. fields 사본을 바꿔도 실행 scene은 변하지 않는다."""

    canonical_bytes: bytes

    def __post_init__(self):
        if type(self.canonical_bytes) is not bytes or len(self.canonical_bytes) > 65_536:
            raise ValueError('synthetic scene exceeds its fixed bound')
        value = json.loads(self.canonical_bytes)
        if canonical(value) != self.canonical_bytes:
            raise ValueError('scene must retain canonical producer bytes')
        track = value['track']
        points, layout = track['centerline'], track['layout']
        if not 3 <= len(points) <= 512 or any(len(p) != 2 or not _finite(p) for p in points):
            raise ValueError('scene requires a bounded closed centerline')
        if not 0 < track['lane_width_m'] <= 4:
            raise ValueError('scene road width is invalid')
        if (len(layout['bounds']) != 4 or not _finite(layout['bounds'])
                or not layout['bounds'][0] < layout['bounds'][2] or not layout['bounds'][1] < layout['bounds'][3]):
            raise ValueError('scene bounds are invalid')
        if len(layout['obstacle_seeds']) > 64:
            raise ValueError('scene has more than 64 box surfaces')
        for box in layout['obstacle_seeds']:
            if not _finite([box[k] for k in ('x', 'y', 'w', 'h')]) or not 0 < box['w'] <= 4 or not 0 < box['h'] <= 4:
                raise ValueError('scene box geometry is invalid')
        for name in ('paved_areas', 'paint_lines'):
            if len(layout[name]) > 64 or sum(map(len, layout[name])) > 2048:
                raise ValueError('scene paint geometry exceeds its bound')
            if any(len(p) != 2 or not _finite(p) for line in layout[name] for p in line):
                raise ValueError('scene paint coordinates are invalid')

    @property
    def fields(self):
        return json.loads(self.canonical_bytes)

    @property
    def sha256(self):
        return hashlib.sha256(self.canonical_bytes).hexdigest()


@dataclass(frozen=True)
class SceneSnapshot:
    """호출자가 한 simulation tick에서 고정한 값. pose는 rear-axle world XY/yaw CCW다.

    GT pose는 renderer 내부 입력이며 PerceptionInput으로 전파하지 않는다. 다른 thread의
    mutable simulator를 읽어 이 값을 만들 때는 해당 simulator의 단일 상태 lock이 필요하다.
    """

    epoch: str
    sequence: int
    sim_time_us: int
    pose: tuple[float, float, float]
    scene: Scene

    def __post_init__(self):
        if type(self.epoch) is not str or str(uuid.UUID(self.epoch)) != self.epoch:
            raise ValueError('snapshot epoch must be a canonical UUID')
        if any(type(v) is not int or not 0 <= v < 2**64 for v in (self.sequence, self.sim_time_us)):
            raise ValueError('snapshot sequence/time must be uint64')
        if type(self.pose) is not tuple or len(self.pose) != 3 or not _finite(self.pose) or type(self.scene) is not Scene:
            raise ValueError('snapshot requires frozen finite pose and scene')


def demo_scene():
    """branch0b56의 코스 하나를 읽는다. 기존 simulator/UI 생성 파일을 덮지 않는다."""
    path = Path(__file__).with_name('fixtures') / 'jetson-demo.json'
    return Scene(path.read_bytes())


def oracle(snapshot):
    """평가 전용 GT 사본. 관측 consumer는 이 결과를 입력으로 받지 않는다."""
    fields = snapshot.scene.fields
    return dict(kind='SYNTHETIC_ORACLE', epoch=snapshot.epoch, sequence=snapshot.sequence,
                sim_time_us=snapshot.sim_time_us, pose=list(snapshot.pose), scene_sha256=snapshot.scene.sha256,
                parking_slots=fields['track']['layout']['parking_slots'],
                obstacles=fields['track']['layout']['obstacle_seeds'],
                centerline=fields['track']['centerline'])
