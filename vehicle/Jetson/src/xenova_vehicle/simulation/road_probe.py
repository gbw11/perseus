"""원 RGB의 tape/road 오차를 관찰하는 NN 독립 offline 진단이다.

기존 추출·가설 함수를 변경하지 않는다. GT는 renderer/evaluation 파일에만 있으며
NN REQUIRED·정합·지도·odom·FREE·출력 권한을 대체하지 않는다. 시각은 원본과
진단 실행을 구분하고 UNKNOWN/오차/작업 상한을 그대로 보존한다.
"""

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
import tempfile
import time
from uuid import uuid4


MAX_DIAGNOSTIC_BYTES = 2 * 1024**2
MAX_DIAGNOSTIC_HYPOTHESES = 32
GEOMETRY_LIFETIME_US = 500_000


def j_us():
    return time.monotonic_ns() // 1000


def _canonical(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'), allow_nan=False) + '\n').encode('ascii')


def _uint(value):
    if type(value) is not int or not 0 <= value < 2**64:
        raise ValueError('original probe time must be uint64')
    return value


def road_error_diagnostics(graph, profile, result):
    """기존 private chain 계산을 읽어 원 반환 가설의 오차 항을 분해한다.

    scalar strip.error는 chain tube에, 축별 point box는 방향 계산에 사용된다.
    segment box도 원문으로 남기되 이 진단이 추출 오차를 줄이거나 다시 승인하지 않는다.
    최대32개 가설만 상세 기록하며 생략 수는 별도로 표시한다.
    """
    from ..perception import road
    from ..perception.tape import TapeGraph
    if type(graph) is not TapeGraph or type(profile) is not road.RoadProfile or type(result) is not road.RoadResult:
        raise ValueError('original typed tape/profile/road result required')
    if result.source_key != graph.source_key:
        raise ValueError('road result source differs from tape graph')
    chains, ambiguous, work = road._chains(graph, profile)
    by_ids = {chain.ids: chain for chain in chains}
    chain_fields = []
    for chain in chains:
        chain_fields.append(dict(strip_ids=chain.ids, points_xy=chain.points, tube_error_m=chain.error,
            point_error_xy=chain.point_error_xy, segment_error_xy=chain.segment_error_xy,
            gaps_s=chain.gaps, arc_length_m=road._arc(chain.points)[-1],
            segment_heading_error_rad=[road._heading_error(chain, i) for i in range(len(chain.points)-1)]))
    hypotheses = []
    for hypothesis in result.hypotheses[:MAX_DIAGNOSTIC_HYPOTHESES]:
        roles = [None if not ids else by_ids[ids] for ids in hypothesis.strip_ids]
        divider = roles[1]
        if divider is None:
            raise ValueError('original hypothesis has no observed divider')
        used = [chain for chain in roles if chain is not None]
        station_rows = []
        for station, point, left, right, inferred in zip(hypothesis.station_s, hypothesis.divider,
                hypothesis.left_outer, hypothesis.right_outer, hypothesis.inferred):
            _, index = road._at(divider.points, station)
            a, b = divider.points[index:index+2]
            angle = road._angle(a, b)
            normal = -math.sin(angle), math.cos(angle)
            distances = [sum((outer[k]-point[k])*normal[k] for k in (0, 1)) for outer in (left, right)]
            station_rows.append(dict(station_s=station, divider_segment=index, divider_xy=point,
                left_outer=left, right_outer=right, inferred=inferred,
                normal_distances_m=distances, heading_error_rad=road._heading_error(divider, index)))
        yaw = max(row['heading_error_rad'] for row in station_rows)
        maximum_width = max(abs(value) for row in station_rows for value in row['normal_distances_m'])
        step = max(b-a for a, b in zip(hypothesis.station_s, hypothesis.station_s[1:]))
        terms = dict(chain_tube_max_m=max(chain.error for chain in used),
            station_error_m=profile.station_error_m,
            missing_outer_prior_m=(profile.lane_width_max_m-profile.lane_width_min_m)/2
                if any(chain is None for chain in roles) else 0.,
            heading_width_pad_m=maximum_width*math.sin(yaw),
            curvature_station_pad_m=profile.max_curvature_1pm*step*step/8)
        total = sum(terms.values())
        hypotheses.append(dict(local_id=hypothesis.local_id, strip_ids=hypothesis.strip_ids,
            station_count=len(station_rows), supported_s=(hypothesis.station_s[0], hypothesis.station_s[-1]),
            supported_length_m=hypothesis.station_s[-1]-hypothesis.station_s[0],
            inferred_stations=sum(hypothesis.inferred), width_intervals_m=hypothesis.width_intervals_m,
            maximum_normal_width_m=maximum_width, maximum_station_step_m=step,
            original_error_m=hypothesis.error_m, original_yaw_error_rad=hypothesis.yaw_error_rad,
            error_terms_m=terms, reconstructed_error_m=total,
            arithmetic_residual_m=hypothesis.error_m-total,
            geometry_budget_m=profile.max_geometry_error_m,
            exceeds_geometry_budget=hypothesis.error_m > profile.max_geometry_error_m,
            stations=station_rows))
    return dict(chains=chain_fields, dashed_ambiguous=ambiguous, chain_work=work,
        hypotheses=hypotheses, original_hypotheses=len(result.hypotheses),
        omitted_detailed_hypotheses=max(0, len(result.hypotheses)-len(hypotheses)),
        scalar_terms_scope='existing_road_hypothesis_formula_read_only_no_bound_reduction')


def probe_road(observation, calibration, *, tape_profile=None, road_profile=None,
               geometry_expires_us=None, clock=j_us):
    """원 NV12→기존 depth 가림 후보→tape→road를 한 번 실행하고 순수 JSON 사본을 반환한다.

    depth는 같은 원 frame pair일 때만 기존 RIGHT 분석/보수 RGB mask로 쓴다.
    GT box/pose나 NN mask는 받지 않는다. 별도 진단 상세 계산 시간도 구분한다.
    """
    from ..perception.calibration import CalibrationProfile
    from ..perception.inputs import PerceptionInput
    from ..perception.tape import TapeProfile, extract_tape
    from ..perception.road import RoadProfile, infer_road
    from ..perception.depth import analyze_depth
    from ..perception.occlusion import box_occlusion
    if (type(observation) is not PerceptionInput or type(calibration) is not CalibrationProfile
            or observation.rgb.encoding != 'NV12' or observation.calibration_id != calibration.sha256
            or observation.nn is not None or observation.nn_key is not None):
        raise ValueError('original NN-independent NV12/calibration input required')
    if (observation.rgb.width, observation.rgb.height) != (calibration.rgb.width, calibration.rgb.height):
        raise ValueError('original RGB dimensions differ from calibration')
    tape_profile = TapeProfile() if tape_profile is None else tape_profile
    road_profile = RoadProfile() if road_profile is None else road_profile
    if type(tape_profile) is not TapeProfile or type(road_profile) is not RoadProfile:
        raise ValueError('exact original tape/road configuration required')
    capture = observation.rgb.stamp.captured_us
    if geometry_expires_us is not None:
        _uint(geometry_expires_us)
        if capture is None or not capture[1] < geometry_expires_us <= capture[0]+GEOMETRY_LIFETIME_US:
            raise ValueError('geometry deadline differs from original bounded capture')
    ticks = []
    def now():
        value = _uint(clock())
        if ticks and value < ticks[-1]:
            raise ValueError('probe monotonic clock regressed')
        ticks.append(value)
        return value
    started = now()
    bgr = observation.rgb.array()
    decoded = now()
    depth = observation.depth
    evidence = None
    if depth is not None:
        if (depth.stamp.sequence != observation.rgb.stamp.sequence
                or depth.stamp.captured_us != capture or depth.exposure_us != observation.rgb.exposure_us
                or (depth.width, depth.height) != (calibration.right.width, calibration.right.height)):
            raise ValueError('depth cannot substitute a foreign/unpaired RGB occlusion observation')
        evidence = analyze_depth(depth.array(), calibration.right, calibration.plane, stamp=depth.stamp,
            calibration_id=calibration.sha256, input_kind=observation.kind, profile=calibration)
    depth_done = now()
    mask = box_occlusion(() if evidence is None else evidence.hit_boxes, calibration.rgb)
    mask_done = now()
    graph = extract_tape(bgr, calibration.rgb, calibration.plane, tape_profile,
        stamp=observation.rgb.stamp, occlusion_mask=mask)
    tape_done = now()
    road = infer_road(graph, road_profile)
    road_done = now()
    diagnostic = road_error_diagnostics(graph, road_profile, road)
    done = now()
    value = dict(profile='road-geometry-probe-1', scope='OFFLINE_NN_INDEPENDENT_NO_RUNTIME_ADMISSION',
        nn_executed=False, nn_requirement_changed=False, metric_ready=False, world_valid=False,
        live_authority=False, free_positive=False, odometry_sequence_restore='NOT_IMPLEMENTED_IN_THIS_PROBE',
        input_kind=observation.kind, calibration_sha256=calibration.sha256,
        original_rgb=dict(stamp=asdict(observation.rgb.stamp), payload_sha256=observation.rgb.sha256,
            exposure_us=observation.rgb.exposure_us),
        original_depth=None if depth is None else dict(stamp=asdict(depth.stamp), payload_sha256=depth.sha256),
        original_geometry_expires_us=geometry_expires_us,
        original_time_scope='preserved_original_clock_and_interval_not_retimed_to_probe',
        probe_started_j_us=started, probe_completed_j_us=done,
        timings_us=dict(nv12_decode=decoded-started, depth=depth_done-decoded,
            occlusion=mask_done-depth_done, tape=tape_done-mask_done, road=road_done-tape_done,
            diagnostic_recomputation=done-road_done),
        configuration=dict(tape=asdict(tape_profile), road=asdict(road_profile)),
        depth_occlusion=None if evidence is None else dict(mode=evidence.mode,
            hits=len(evidence.hit_boxes), omitted_hits=evidence.omitted_hit_count,
            omitted_patches=evidence.omitted_patch_count, input_kind=evidence.input_kind),
        tape=graph.descriptor(), road=road.descriptor(), error_diagnostics=diagnostic)
    raw = _canonical(value)
    if len(raw) > MAX_DIAGNOSTIC_BYTES:
        raise ValueError('road diagnostic exceeds bounded2MiB; no partial success')
    return json.loads(raw)


def load_road_sample(case_directory, *, calibration_path=None):
    """대표 runner의 원 input.json/packed bytes/hash를 읽는다. NN·GT 파일은 입력으로 읽지 않는다."""
    from ..perception.calibration import CalibrationProfile
    from ..perception.inputs import FrameStamp, ImageFrame, PerceptionInput
    directory = Path(case_directory)
    calibration_path = directory.parent/'calibration.json' if calibration_path is None else Path(calibration_path)
    if (directory/'input.json').stat().st_size > 8192:
        raise ValueError('original input descriptor exceeds8192B')
    metadata_raw = (directory/'input.json').read_bytes()
    metadata = json.loads(metadata_raw)
    if calibration_path.stat().st_size > 65536:
        raise ValueError('original calibration exceeds65536B')
    calibration_raw = calibration_path.read_bytes()
    calibration = CalibrationProfile.from_dict(json.loads(calibration_raw))
    def image(field, filename):
        item = metadata[field]
        if item is None:
            return None
        if type(item) is not dict or set(item) != {'stamp', 'width', 'height', 'encoding', 'exposure_us', 'bytes', 'sha256'}:
            raise ValueError('original image metadata fields differ')
        stamp_fields = dict(item['stamp'])
        interval = stamp_fields['captured_us']
        stamp_fields['captured_us'] = None if interval is None else tuple(interval)
        stamp = FrameStamp(**stamp_fields)
        if item['encoding'] != ('RAW16_LE' if field == 'depth' else 'NV12'):
            raise ValueError('original RGB/RIGHT packed encoding differs')
        limit = 640*480*2 if field == 'depth' else 640*360*3//2
        path = directory/filename
        if type(item['bytes']) is not int or item['bytes'] > limit or path.stat().st_size != item['bytes']:
            raise ValueError('original packed image size differs')
        raw = path.read_bytes()
        if len(raw) != item['bytes'] or sha256(raw).hexdigest() != item['sha256']:
            raise ValueError('original packed image hash differs')
        return ImageFrame(stamp, item['width'], item['height'], item['encoding'], raw, item['exposure_us'])
    observation = PerceptionInput(metadata['kind'], metadata['calibration_id'],
        image('rgb', 'rgb.nv12'), image('depth', 'input-depth.raw16le'))
    if observation.calibration_id != calibration.sha256:
        raise ValueError('original whole calibration SHA differs')
    origin = dict(input_descriptor_sha256=sha256(metadata_raw).hexdigest(),
        calibration_file_sha256=sha256(calibration_raw).hexdigest(), scope='original_packed_sensor_only_no_nn_or_gt')
    receipt_path = directory/'capture-receipt.json'
    if receipt_path.exists():
        if receipt_path.stat().st_size > 8192:
            raise ValueError('original capture receipt exceeds8192B')
        raw = receipt_path.read_bytes()
        receipt = json.loads(raw)
        expires = int(receipt['expires_j_us'])
        if (receipt['rgb_sha256'] != observation.rgb.sha256 or receipt['calibration_id'] != calibration.sha256
                or tuple(map(int, receipt['captured_j_us'])) != observation.rgb.stamp.captured_us):
            raise ValueError('original capture receipt does not describe the RGB')
        origin.update(original_geometry_expires_us=expires, capture_receipt_sha256=sha256(raw).hexdigest())
    return observation, calibration, origin


@dataclass(frozen=True)
class RoadProbeCase:
    name: str
    scene: object
    pose: tuple
    evaluation: str


def road_probe_cases(scene):
    """GT 도형은 새 RGB를 그리는 데만 쓴다. 추출기에는 renderer pixels/depth만 전달한다."""
    from .scene import Scene, canonical
    if type(scene) is not Scene:
        raise ValueError('original Scene required')
    pose = tuple(scene.fields['track']['layout']['start_pose'])
    c, s = math.cos(pose[2]), math.sin(pose[2])
    def world(x, y):
        return [pose[0]+c*x-s*y, pose[1]+s*x+c*y]
    def make(name, on, off, *, branch=False, box=False):
        fields = scene.fields
        layout = fields['track']['layout']
        # apron은 기존 자동 edge/divider를 지운 뒤 명시 paint만 그리기 위한 renderer 자산이다.
        layout['paved_areas'] = [[world(x, y) for x, y in ((-.3,-1.),(2.3,-1.),(2.3,1.),(-.3,1.))]]
        lines = [[world(.2, y), world(1.9, y)] for y in (-.3, .3)]
        start = .25
        while start < 1.9:
            lines.append([world(start, 0.), world(min(1.9, start+on), 0.)])
            start += on+off
        if branch:
            lines.extend(([world(.75,0.),world(1.0,.12)], [world(.75,0.),world(1.0,-.12)]))
        layout['paint_lines'] = lines
        layout['obstacle_seeds'] = ([dict(id='divider-occluder', kind='box', x=world(.85,0.)[0],
            y=world(.85,0.)[1], w=.18, h=.18)] if box else [])
        return RoadProbeCase(name, Scene(canonical(fields)), pose,
            'RENDERER_EVALUATION_ONLY_NOT_LANE_OR_OBSTACLE_INPUT')
    empty = scene.fields
    empty['track']['layout']['obstacle_seeds'] = []
    original = RoadProbeCase('P01-source-empty', Scene(canonical(empty)), pose,
        'SOURCE_SCENE_RENDERER_ONLY_AFTER_DASH_BOUNDARY_FIX')
    return (original, make('P01-dash-025-025', .25, .25), make('P01-short-dash', .05, .25),
        make('P01-long-gap', .25, .60), make('P01-branch', .25, .25, branch=True),
        make('P03-divider-box-occlusion', .25, .25, box=True))


def run_road_probe(profile, scene, output_root, *, original_case_directory=None):
    """새 유한 진단 폴더에 원 packed 입력과 결과를 저장한다. 성공은 도구 완료만 뜻한다."""
    from .profile import DevelopmentProfile
    from .wall_sensor import WallPoseSample, WallSyntheticCamera
    if type(profile) is not DevelopmentProfile:
        raise ValueError('explicit DevelopmentProfile required')
    profile.check_scene(scene)
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix='road-probe-', dir=root))
    def save(directory, name, raw):
        with (directory/name).open('xb') as stream:
            stream.write(raw)
        return dict(path=str((directory/name).relative_to(output)), bytes=len(raw), sha256=sha256(raw).hexdigest())
    rows = []
    epoch, clock_epoch = str(uuid4()), str(uuid4())
    jobs = []
    if original_case_directory is not None:
        observation, calibration, origin = load_road_sample(original_case_directory)
        jobs.append(('ORIGINAL-P01', observation, calibration, origin, None))
    for sequence, case in enumerate(road_probe_cases(scene), 1):
        local_profile = profile.for_scene(case.scene)
        owner = WallSyntheticCamera(local_profile, case.scene, epoch=epoch, clock_epoch=clock_epoch)
        try:
            sample = WallPoseSample.sample(epoch, sequence, lambda: case.pose)
            capture = owner.render_diagnostic(sample, expires_j_us=sample.captured_j_us[0]+GEOMETRY_LIFETIME_US)
            jobs.append((case.name, capture.observation, owner.calibration,
                dict(renderer_receipt=capture.receipt, original_geometry_expires_us=capture.expires_j_us,
                     scene_sha256=case.scene.sha256,
                     profile_sha256=local_profile.sha256, evaluation_scope=case.evaluation), case))
        finally:
            owner.close()
    for name, observation, calibration, origin, case in jobs:
        directory = output/name
        directory.mkdir()
        result = probe_road(observation, calibration, geometry_expires_us=origin.get('original_geometry_expires_us'))
        files = [save(directory, 'probe.json', _canonical(result)),
            save(directory, 'origin.json', _canonical(origin)),
            save(directory, 'calibration.json', _canonical(calibration.to_dict())),
            save(directory, 'rgb.nv12', observation.rgb.payload)]
        if observation.depth is not None:
            files.append(save(directory, 'depth.raw16le', observation.depth.payload))
        if case is not None:
            files.append(save(directory, 'evaluation-only-scene.json', case.scene.canonical_bytes))
        rows.append(dict(name=name, tape_status=result['tape']['status'], road_status=result['road']['status'],
            road_reasons=result['road']['reasons'], files=files))
    report = dict(profile='road-probe-suite-1', status='DIAGNOSTIC_COMPLETE',
        quality_accepted=False, live_authority=False, nn_executed=False,
        odometry_sequence_restore='NOT_IMPLEMENTED_IN_THIS_PROBE', cases=rows)
    save(output, 'result.json', _canonical(report))
    return dict(report, output=str(output))
