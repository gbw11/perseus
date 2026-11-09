"""PC 새 실행 준비용 코스 목록. 제품 import·실행·IPC·현재 지도 변경은 없다.

지원 근거는 저장소의 원 fixture/profile 또는 기존 simulator tracks.json이다.
내보내는 viewer 전용 계획은 실행기가 소비하는 제품 설정/API가 아니다.
"""
from dataclasses import asdict,dataclass
from hashlib import sha256
import json
from pathlib import Path

from snapshot import SnapshotError,read_file,json_object

SCENE='vehicle/Jetson/src/xenova_vehicle/simulation/fixtures/jetson-demo.json'
PROFILE='vehicle/Jetson/src/xenova_vehicle/simulation/fixtures/experiment-profile.json'
PROFILE_TEST='vehicle/Jetson/tests/unit/test_simulation_profile.py'
TRACKS='platform/apps/simulator/xenova_simulator/tracks.json'
LEGACY_MAIN='platform/apps/simulator/xenova_simulator/__main__.py'
NATIVE='JETSON_NATIVE_DEPTH_FIXTURE'
LEGACY='LEGACY_MJPEG_SIMULATOR'
PLAN_SCHEMA='viewer-course-preparation-1'


@dataclass(frozen=True)
class Artifact:
    path: str
    bytes: int
    sha256: str


@dataclass(frozen=True)
class Course:
    key: str
    name: str
    family: str
    track_id: str
    artifacts: tuple[Artifact,...]
    description: str
    limitations: tuple[str,...]


@dataclass(frozen=True)
class Catalog:
    root: Path
    entries: tuple[Course,...]
    unavailable: tuple[str,...]

    def prepare(self,key,*,requested_laps=2):
        """원 파일을 다시 대조한 표시용 계획을 반환한다. 새 run/권한을 만들지 않는다."""
        course=next((entry for entry in self.entries if entry.key==key),None)
        if type(key) is not str or course is None:raise SnapshotError('COURSE_NOT_IN_SUPPORTED_CATALOG')
        if type(requested_laps) is not int or not 1<=requested_laps<=16:raise SnapshotError('COURSE_REQUESTED_LAPS_1_TO_16')
        for expected in course.artifacts:
            _,actual=_read(self.root,expected.path)
            if actual!=expected:raise SnapshotError('COURSE_DEFINITION_CHANGED: '+expected.path)
        if course.family==NATIVE:
            recipe=dict(purpose='FIXTURE_PROFILE_CHECK_ONLY_NOT_DRIVING',cwd='vehicle/Jetson',
                environment=dict(PYTHONPATH='src'),
                argv=['python','-m','unittest','discover','-s','tests/unit','-p','test_simulation_profile.py','-v'],
                note='기존 fixture/profile 의미시험 명령이다. simulator 새 run 또는 폐루프 주행을 시작하지 않는다.')
            definition=dict(scene_path=SCENE,profile_path=PROFILE)
        else:
            recipe=dict(purpose='LEGACY_NEW_SIMULATOR_PROCESS_MANUAL_ONLY',cwd='.',
                environment=dict(PYTHONPATH='platform/apps/simulator;platform/services/vm-agent/mqtt-client;platform/services/gateway'),
                argv=['python','-m','xenova_simulator','--track',course.track_id,'--input','frames',
                    '--frame-listen','127.0.0.1:7600','--camera','127.0.0.1:8090'],
                note='Windows PC용 경로 예시. 별도 legacy 실행환경·카메라 extra·브로커/계정 설정이 필요하며 이 뷰어는 실행하지 않는다.')
            definition=dict(tracks_path=TRACKS,track_id=course.track_id)
        return dict(schema=PLAN_SCHEMA,state='PREPARED_NOT_EXECUTED',
            selected_course=dict(key=course.key,name=course.name,family=course.family,track_id=course.track_id),
            definition=definition,source_artifacts=[asdict(item) for item in course.artifacts],
            request=dict(scope='NEW_RUN_ONLY',requested_laps=requested_laps,
                lap_policy='SAME_RUN_AND_MAP_ACROSS_LAPS',automatic_lap_count_execution='NOT_CONNECTED'),
            connection=dict(simulator_start_ipc='NOT_CONNECTED',current_run_applied=False,
                jetson_map_modified=False,nn_or_source_restarted=False),
            manual_recipe=recipe,limitations=list(course.limitations),
            excluded=['HISTORICAL_SCENE_RECORDING_SELECTION','JETSON_SAVED_MAP_SELECTION','RUNNING_COURSE_HOT_SWAP'])


def plan_bytes(value):
    """다운로드할 PC 개발 계획 JSON이다. 제품 wire나 실행 요청으로 발행하지 않는다."""
    return json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False).encode('utf-8')


def _read(root,name,cap=1024*1024):
    raw=read_file(root,name,cap)
    return raw,Artifact(name,len(raw),sha256(raw).hexdigest())


def _native(root):
    scene_raw,scene_artifact=_read(root,SCENE,65536);profile_raw,profile_artifact=_read(root,PROFILE,16384)
    _,test_artifact=_read(root,PROFILE_TEST,65536)
    scene=json_object(scene_raw);profile=json_object(profile_raw)
    track=scene.get('track');source=profile.get('source');qualification=profile.get('qualification')
    if (type(track) is not dict or track.get('id')!='jetson-demo' or type(track.get('name')) is not str or not 1<=len(track['name'])<=256 or
            profile.get('profile')!='course-development-profile-1' or type(source) is not dict or
            type(source.get('scene')) is not dict or type(qualification) is not dict or
            set(qualification)!={'measured','physical_admission'} or
            qualification['measured'] is not False or qualification['physical_admission'] is not False):
        raise SnapshotError('NATIVE_COURSE_PROFILE_SHAPE')
    pin=source['scene']
    if (pin.get('file')!='jetson-demo.json' or pin.get('track_id')!=track['id'] or
            pin.get('size_bytes')!=len(scene_raw) or pin.get('sha256')!=scene_artifact.sha256 or
            source.get('commit')!=scene.get('source_commit') or pin.get('source_path')!=scene.get('source_path') or
            pin.get('source_sha256')!=scene.get('source_sha256')):
        raise SnapshotError('NATIVE_COURSE_PROFILE_SOURCE_MISMATCH')
    return Course('native:'+track['id'],track['name'],NATIVE,track['id'],(scene_artifact,profile_artifact,test_artifact),
        '권장 · 저장소의 native RGB640×360/RIGHT depth640×480 공통 실험 fixture/profile',
        ('NATIVE_RUN_START_IPC_NOT_CONNECTED','UNMEASURED_SYNTHETIC_PROFILE_NOT_PHYSICAL_ADMISSION',
         'PROFILE_TEST_IS_NOT_CLOSED_LOOP_DRIVING','NO_JETSON_MAP_OR_NN_SOURCE_RESET'))


def _legacy(root):
    raw,artifact=_read(root,TRACKS);_,main=_read(root,LEGACY_MAIN,65536)
    value=json_object(raw);tracks=value.get('tracks')
    if type(tracks) is not list or not 1<=len(tracks)<=32:raise SnapshotError('LEGACY_COURSE_LIST_BOUND')
    courses=[];ids=set()
    for track in tracks:
        if type(track) is not dict:raise SnapshotError('LEGACY_COURSE_ENTRY')
        identity=track.get('id');name=track.get('name');points=track.get('centerline')
        if (type(identity) is not str or not 1<=len(identity)<=64 or not all(c in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in identity) or
                identity in ids or type(name) is not str or not 1<=len(name)<=256 or
                type(points) is not list or not 3<=len(points)<=4096):raise SnapshotError('LEGACY_COURSE_ENTRY')
        ids.add(identity)
        courses.append(Course('legacy:'+identity,name,LEGACY,identity,(artifact,main),
            '별도 legacy simulator의 --track 선택 · 현재 native-depth/Feedback3 HIL 코스와 구분',
            ('LEGACY_MJPEG_320x240_NOT_NATIVE_DEPTH','LEGACY_WRITE_ONLY_FRAMES_NOT_FEEDBACK3',
             'CURRENT_JETSON_HIL_INTEGRATION_NOT_CONNECTED','BROKER_AND_SEPARATE_LEGACY_ENV_REQUIRED',
             'NO_RUNNING_TRACK_SELECT_OR_RACE_RESET_COMMAND')))
    return courses


def load_catalog(repository_root):
    """현재 저장소의 지원 정의만 읽는다. 한 계열이 없으면 원 오류와 함께 제외한다."""
    root=Path(repository_root).resolve();entries=[];errors=[]
    for name,loader in ((NATIVE,_native),(LEGACY,_legacy)):
        try:
            result=loader(root);entries.extend(result if type(result) is list else (result,))
        except (OSError,ValueError,KeyError,TypeError) as error:errors.append(name+': '+str(error))
    return Catalog(root,tuple(entries),tuple(errors))
