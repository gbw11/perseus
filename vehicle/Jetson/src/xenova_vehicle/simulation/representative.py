"""실제 host-fed YOLOP와 frame-local 지각의 정지 대표 장면 진단이다.

GT는 renderer/별도 평가 패널에만 쓴다. 원500ms geometry E와 별도 NN E를 보존하며
늦은 관측도 원 bytes/시각으로 진단한다. offline 결과는 READY·World·이동 권한이 아니다.
"""

from dataclasses import asdict, dataclass, replace
import hashlib
import io
import json
import math
from pathlib import Path
import re
import tempfile
import time
import traceback
from uuid import uuid4

from .scene import Scene, canonical


MAX_CASES = 16
GEOMETRY_LIFETIME_US = 500_000


def j_us():
    return time.monotonic_ns() // 1000


@dataclass(frozen=True)
class RepresentativeCase:
    """명시적인 renderer 입력이다. pose와 box/slot ID는 core 입력으로 전달하지 않는다."""
    name: str
    scene: Scene
    pose: tuple
    depth_mode: str = 'NATIVE'
    expect_depth_hit: bool = False
    visual_limit: str | None = None

    def __post_init__(self):
        if (type(self.name) is not str or re.fullmatch(r'[A-Za-z0-9_-]{1,64}', self.name) is None
                or type(self.scene) is not Scene or type(self.pose) is not tuple or len(self.pose) != 3
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in self.pose)
                or self.depth_mode not in ('NATIVE', 'NONE', 'ZERO') or type(self.expect_depth_hit) is not bool
                or self.visual_limit is not None and (type(self.visual_limit) is not str or len(self.visual_limit) > 128)):
            raise ValueError('bounded explicit representative renderer case required')


def representative_cases(scene):
    """원 도로/선폭/보정은 두고 box 위치와 결측만 선언한 유한 software 입력 묶음이다."""
    if type(scene) is not Scene:
        raise ValueError('original Scene required')
    fields = scene.fields
    layout = fields['track']['layout']
    pose = tuple(layout['start_pose'])
    seed = layout['obstacle_seeds'][0]

    def changed(boxes):
        value = scene.fields
        value['track']['layout']['obstacle_seeds'] = boxes
        return Scene(canonical(value))

    def box_at(x, y, name):
        c, s = math.cos(pose[2]), math.sin(pose[2])
        return dict(seed, id=name, kind='box', x=pose[0] + c*x - s*y, y=pose[1] + s*x + c*y)

    empty = changed([])
    cases = [RepresentativeCase('P01-straight-empty', empty, pose)]
    points = fields['track']['centerline']
    index = len(points) // 8
    before, after = points[index - 1], points[(index + 1) % len(points)]
    curve_pose = (*points[index], math.atan2(after[1] - before[1], after[0] - before[0]))
    cases.append(RepresentativeCase('P02-curve-empty', empty, curve_pose))
    # 아래 좌표는 미실측 시험 입력이다. 검출/통행 수락 임계값이 아니다.
    for name, x, y in (('P03-box-near', .45, 0.), ('P03-box-middle', .65, .15), ('P03-box-far', .85, -.15)):
        cases.append(RepresentativeCase(name, changed([box_at(x, y, name)]), pose, expect_depth_hit=True))
    cases.append(RepresentativeCase('P04-adjacent-box', changed([box_at(.65, -.4, 'adjacent')]), pose))
    slot = layout['parking_slots'][0]['outline']
    sx = sum(p[0] for p in slot) / len(slot)
    low_y, high_y = min(p[1] for p in slot), max(p[1] for p in slot)
    slot_pose = (sx, low_y - .4, math.pi / 2)
    occupied = dict(seed, id='slot-box', kind='box', x=sx, y=(low_y + high_y) / 2)
    cases.extend((RepresentativeCase('P05-slot-empty', empty, slot_pose),
        RepresentativeCase('P05-slot-occupied', changed([occupied]), slot_pose, expect_depth_hit=True)))
    # 현재 renderer는 정사각 prism만 지원한다. 실제 차량 시각 모델/성능으로 세지 않는다.
    cases.append(RepresentativeCase('P06-vehicle-asset-unsupported', empty, pose,
        visual_limit='UNSUPPORTED_SCENE_ASSET_VEHICLE_VISUAL_MODEL'))
    cases.extend((RepresentativeCase('P07-depth-none', empty, pose, 'NONE'),
        RepresentativeCase('P07-depth-zero', empty, pose, 'ZERO'),
        RepresentativeCase('P08-partial-roi-box', changed([box_at(.45, .4, 'partial-roi')]), pose)))
    return tuple(cases)


def _depth_variant(observation, mode):
    if mode == 'NATIVE':
        return observation
    if mode == 'NONE':
        return replace(observation, depth=None)
    if mode == 'ZERO' and observation.depth is not None:
        return replace(observation, depth=replace(observation.depth, payload=bytes(len(observation.depth.payload))))
    raise ValueError('explicit native/absent/zero depth diagnostic mode required')


def _save(path, raw):
    with path.open('xb') as stream:
        stream.write(raw)
    return dict(path=path.name, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())


def _json(path, value):
    raw = canonical(value)
    if len(raw) > 262_144:
        raise ValueError('representative diagnostic JSON exceeds256KiB')
    return _save(path, raw)


def _png(path, image):
    import cv2
    ok, data = cv2.imencode('.png', image)
    if not ok or data.nbytes > 4 * 1024**2:
        raise ValueError('bounded diagnostic PNG encoding failed')
    return _save(path, data.tobytes())


def _save_map_arrays(directory, result):
    """원 snapshot의 여섯 배열을 무변환 NPY와 metadata로 보존한다. 재계산/GT 보충은 없다."""
    import numpy as np
    snapshot = result.map_snapshot
    arrays = snapshot['arrays']
    names = ('state', 'age_ms', 'support', 'permission', 'conflict', 'unordered_hit')
    if set(arrays) != set(names):
        raise ValueError('actual diagnostic map snapshot array inventory differs')
    shape = snapshot['spec'].height, snapshot['spec'].width
    if not 1 <= shape[0] <= 128 or not 1 <= shape[1] <= 128:
        raise ValueError('diagnostic map array exceeds bounded grid')
    files, descriptors = [], {}
    for name in names:
        array = arrays[name]
        if type(array) is not np.ndarray or array.shape != shape or array.dtype.kind not in 'bui':
            raise ValueError('actual primitive map array/shape required')
        buffer = io.BytesIO()
        np.save(buffer, array, allow_pickle=False)
        file = _save(directory/('map-'+name+'.npy'), buffer.getvalue())
        files.append(file)
        descriptors[name] = dict(**file, shape=list(shape), dtype=array.dtype.str,
            raw_row_major_bytes=array.nbytes, raw_row_major_sha256=hashlib.sha256(array.tobytes(order='C')).hexdigest())
    files.append(_json(directory/'map-arrays.json', dict(
        scope='ORIGINAL_ACTUAL_FRAME_LOCAL_MAP_SNAPSHOT', spec=asdict(snapshot['spec']),
        frame_id=snapshot['frame_id'], calibration_id=snapshot['calibration_id'],
        clock_id=snapshot['clock_id'], kind=snapshot['kind'], revision=snapshot['revision'],
        evaluated_j_us=result.summary['now_us'], arrays=descriptors,
        state_codes=dict(UNKNOWN=0, FREE=1, OCCUPIED=2), age_unit='millisecond',
        live_authority=False, gt_used=False)))
    return files


def _images(original, paired, result, calibration, case, *, geometry_e=None, completed_j_us=None, depth_caps=None):
    """RGB NN·native RIGHT·actual grid·GT를 별도 패널로 그린다. pixel 정합을 가정하지 않는다."""
    import cv2
    import numpy as np
    from ..perception.pipeline import render_diagnostics
    rgb = original.rgb.array().copy()
    nn = paired.nn
    for mask, color in ((nn.road.source, (120, 30, 120)), (nn.lane.source, (20, 220, 20))):
        selected = mask != 0
        rgb[selected] = (rgb[selected].astype(np.uint16) * 3 + np.asarray(color, dtype=np.uint16)) // 4
    for detection in nn.detections_source:
        x0, y0, x1, y1 = map(round, detection.xyxy)
        cv2.rectangle(rgb, (x0, y0), (x1, y1), (220, 80, 220), 1)
        cv2.putText(rgb, f'vehicle score={detection.score:.3f}',
            (max(0, min(450, x0)), max(12, min(358, y0-3))),
            cv2.FONT_HERSHEY_SIMPLEX, .4, (220, 80, 220), 1)
    depth = np.full((480, 640, 3), 60, dtype=np.uint8)
    if original.depth is not None:
        millimeters = original.depth.array()
        # 16mm/색단계는 표시 축만 정한다. 원 RAW16과 알고리즘 임계값은 변하지 않는다.
        display = np.minimum(millimeters.astype(np.uint32) // 16, 255).astype(np.uint8)
        depth = cv2.applyColorMap(display, cv2.COLORMAP_TURBO)
        depth[millimeters == 0] = 0
    if result is None:
        grid = np.full((360, 640, 3), 60, dtype=np.uint8)
    else:
        _, grid = render_diagnostics(result, calibration)
    gt = np.full((360, 640, 3), 30, dtype=np.uint8)
    layout = case.scene.fields['track']['layout']
    x0, y0, x1, y1 = layout['bounds']

    def pixel(x, y):
        return round((x-x0)/(x1-x0)*639), round(359-(y-y0)/(y1-y0)*359)

    line = np.asarray([pixel(*p) for p in case.scene.fields['track']['centerline']], dtype=np.int32)
    cv2.polylines(gt, [line], True, (100, 100, 100), 1)
    for box in layout['obstacle_seeds']:
        w = box['w'] / 2
        cv2.rectangle(gt, pixel(box['x']-w, box['y']+w), pixel(box['x']+w, box['y']-w), (40, 40, 220), 1)
    for slot in layout['parking_slots']:
        polygon = np.asarray([pixel(*p) for p in slot['outline']], dtype=np.int32)
        cv2.polylines(gt, [polygon], True, (80, 180, 200), 1)
    px, py, yaw = case.pose
    cv2.arrowedLine(gt, pixel(px, py), pixel(px + .25*math.cos(yaw), py + .25*math.sin(yaw)), (255, 255, 255), 2)
    panels = []
    evidence = None if result is None else result.depth
    expired = 'UNASSESSED' if geometry_e is None or completed_j_us is None else (
        'EXPIRED' if completed_j_us >= geometry_e else 'BEFORE_ORIGINAL_E')
    for index, (title, image) in enumerate((('ACTUAL YOLOP RGB / candidate only', rgb), ('NATIVE RIGHT axial mm / separate plane', depth),
            ('ACTUAL FRAME-LOCAL: FREE green / OCC red / UNKNOWN gray', grid), ('GT EVALUATION ONLY / never core input', gt))):
        # native RIGHT의4:3 비율을 유지한다. RGB plane으로 픽셀을 정합한 표시가 아니다.
        h, w = image.shape[:2]
        scale = min(640/w, 360/h)
        width, height = round(w*scale), round(h*scale)
        resized = cv2.resize(image, (width, height), interpolation=cv2.INTER_NEAREST)
        image = np.zeros((360, 640, 3), dtype=np.uint8)
        x, y = (640-width)//2, (360-height)//2
        image[y:y+height, x:x+width] = resized
        cv2.putText(image, title, (8, 18), cv2.FONT_HERSHEY_SIMPLEX, .4, (255, 255, 255), 1)
        if index == 0:
            cv2.putText(image, f'seq={original.rgb.stamp.sequence} geometry={expired}',
                (8, 36), cv2.FONT_HERSHEY_SIMPLEX, .4, (255, 255, 255), 1)
            for text, color, x in (('ROAD:purple', (120, 30, 120), 8),
                    ('LANE:green', (20, 220, 20), 190), ('VEHICLE:bbox+score', (220, 80, 220), 360)):
                cv2.putText(image, text, (x, 54), cv2.FONT_HERSHEY_SIMPLEX, .4, color, 1)
        elif index == 1:
            labels = ['640x480 -> 480x360 +80px side bars; axial display 16mm/level',
                'CLASSIFIED PIXEL MASK: NOT AVAILABLE; raw zero=black']
            if evidence is None:
                labels.append('DepthEvidence: NOT AVAILABLE')
            else:
                labels.extend((f'actual hit boxes={len(evidence.hit_boxes)} floor pixels={evidence.floor_count} invalid={evidence.invalid_count}',
                    f'omitted hits={evidence.omitted_hit_count} ray patches={len(evidence.patches)} omitted={evidence.omitted_patch_count}'))
                if depth_caps is not None:
                    labels.append(f'caps hit boxes={depth_caps.max_hit_boxes} ray patches={depth_caps.max_patches}')
            for row, text in enumerate(labels, 1):
                cv2.putText(image, text, (8, 18+row*17), cv2.FONT_HERSHEY_SIMPLEX, .34, (255, 255, 255), 1)
        panels.append(image)
    return np.vstack((np.hstack(panels[:2]), np.hstack(panels[2:])))


def _metrics(case, result, paired, *, geometry_e, nn_e, completed_j_us,
             nn_input_received_j_us=None, nn_decode_completed_j_us=None):
    import numpy as np
    evidence = result.depth
    state = result.map_snapshot['arrays']['state']
    hits = 0 if evidence is None else len(evidence.hit_boxes)
    occupied = int(np.count_nonzero(state == 2))
    free = int(np.count_nonzero(state == 1))
    unknown = int(np.count_nonzero(state == 0))
    failures, incomplete = [], []
    if case.expect_depth_hit and (hits == 0 or occupied == 0):
        failures.append('EXPECTED_BOX_ACTUAL_DEPTH_HIT_OR_FRAME_LOCAL_OCCUPIED_MISSING')
    if case.depth_mode in ('NONE', 'ZERO') and (hits or occupied or free):
        failures.append('MISSING_DEPTH_CREATED_HIT_OCCUPIED_OR_FREE')
    if case.depth_mode == 'NATIVE' and free == 0:
        incomplete.append('INCOMPLETE_FREE_EVIDENCE')
    if case.visual_limit is not None:
        incomplete.append(case.visual_limit)
    return dict(status='FAILED' if failures else 'INCOMPLETE' if incomplete else 'OBSERVED',
        failures=failures, incomplete=incomplete, actual_nn=True,
        nn_vehicle_detections=len(paired.nn.detections_source),
        nn_decoder_counts=dict(paired.nn.counts),
        neural_layer_counts=None if result.neural is None else dict(result.neural.counts),
        nn_road_positive_pixels=int(np.count_nonzero(paired.nn.road.source)),
        nn_lane_positive_pixels=int(np.count_nonzero(paired.nn.lane.source)),
        nn_box_miss_is_failure=False, nn_class_scope='single_vehicle_class_not_generic_box_detector',
        actual_depth_hit_boxes=hits, actual_frame_local_occupied_cells=occupied,
        actual_frame_local_free_cells=free, actual_frame_local_unknown_cells=unknown,
        geometry_expires_j_us=str(geometry_e), nn_expires_j_us=str(nn_e),
        completed_j_us=str(completed_j_us), geometry_expired=completed_j_us >= geometry_e,
        nn_pair_before_e=None if nn_input_received_j_us is None else
            max(nn_input_received_j_us, paired.nn.packet.received_j_us) < nn_e,
        nn_decode_before_e=None if nn_decode_completed_j_us is None else nn_decode_completed_j_us < nn_e,
        end_to_end_exceeded_nn_e=completed_j_us >= nn_e,
        nn_input_received_j_us=nn_input_received_j_us,
        nn_output_received_j_us=paired.nn.packet.received_j_us,
        nn_decode_completed_j_us=nn_decode_completed_j_us,
        free_status=result.summary['free_status'],
        free_temporal_status=result.summary['free_temporal_status'],
        free_certificates=result.summary['free_certificates'], free_ray_tests=result.summary['free_ray_tests'],
        depth_metric_qualified=False if evidence is None else evidence.metric_qualified,
        depth_mode=None if evidence is None else evidence.mode,
        depth_valid_pixels=0 if evidence is None else evidence.valid_count,
        depth_floor_pixels=0 if evidence is None else evidence.floor_count,
        depth_invalid_pixels=0 if evidence is None else evidence.invalid_count,
        depth_unknown_pixels=0 if evidence is None else evidence.unknown_count,
        omitted_hits=0 if evidence is None else evidence.omitted_hit_count,
        omitted_ray_patches=0 if evidence is None else evidence.omitted_patch_count,
        depth_pixel_classification_mask='NOT_AVAILABLE_IN_DepthEvidence',
        tape_status=result.summary['tape_status'], tape_reasons=list(result.summary['tape_reasons']),
        road_status=result.summary['road_status'], road_reasons=list(result.summary['road_reasons']),
        previous_map_scope='FRESH_FRAME_LOCAL_ONLY_NOT_PRIOR_FREE_TTL_REGRESSION',
        pixel_gt='NOT_AVAILABLE', road_iou=None, lane_iou=None, vehicle_recall=None,
        false_clearance='NOT_EVALUATED_NO_PADDED_CELL_GT',
        parking_geometry_and_clearance='NOT_EVALUATED_BY_THIS_PIPELINE',
        map_scope='ACTUAL_PERCEPTION_PIPELINE_FRAME_LOCAL_NO_ODOM_NO_RESIDENT_MAPPER_ADMISSION',
        evaluation_time_basis='actual_monotonic_no_restamp', live_authority=False, metric_ready=False,
        world_valid=False, physical_acceptance=False)


def run_representative(profile, scene, yolop_profile, output_root, *, cases=None, warmup_frames=0):
    """고정 scene profile과 실제 OAK 하나를 쓴다. 실패도 unique 출력에 원 bytes로 보존한다.

    warmup은 새 원 sample을 렌더하고 버리는 명시 단계다. 이전 pixels에 새 시각을 붙이지
    않는다. runtime 권한을 발급하지 않으며 FREE 미완성/시각 실패를 성공으로 숨기지 않는다.
    """
    from .profile import DevelopmentProfile
    from .capture import DepthEffects
    from .wall_sensor import WallPoseSample, WallSyntheticCamera
    from ..oak.sim_nn import SimNnProfile, SimNnSource
    from ..perception.free_space import TargetClass
    from ..perception.pipeline import PerceptionPipeline, PipelineProfile
    from ..yolop_profile import YolopBenchProfile
    if type(profile) is not DevelopmentProfile or type(yolop_profile) is not YolopBenchProfile:
        raise ValueError('actual development and approved YOLOP profiles required')
    profile.check_scene(scene)
    cases = representative_cases(scene) if cases is None else cases
    if (type(cases) is not tuple or not 1 <= len(cases) <= MAX_CASES
            or any(type(case) is not RepresentativeCase for case in cases)
            or len({case.name for case in cases}) != len(cases)
            or type(warmup_frames) is not int or not 0 <= warmup_frames <= 2):
        raise ValueError('bounded unique case tuple and explicit warmup0..2 required')
    root = Path(output_root).expanduser().absolute()
    root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='s23-representative-', dir=root))
    report = dict(profile='sim-representative-diagnostics-1', output=str(directory), cases=[],
        status='RUNNING', exit_code=1, actual_nn_required=True, live_authority=False,
        physical_acceptance=False, performance_accepted=False, closed_loop=False)
    _save(directory/'profile.json', profile.canonical_bytes)
    _save(directory/'scene.json', scene.canonical_bytes)
    _json(directory/'yolop-profile.json', yolop_profile.to_dict())
    epoch, clock_epoch = str(uuid4()), str(uuid4())
    sequence = 0
    source = None
    calibration = profile.calibration()
    _json(directory/'calibration.json', calibration.to_dict())
    _json(directory/'depth-effects.json', DepthEffects().fields)
    report['renderer'] = 'PRIMARY_SyntheticCamera_first_surface_via_WallSyntheticCamera_NOT_simulator_MJPEG'
    report['pixel_ground_truth'] = 'NOT_AVAILABLE_NO_IOU_INVENTED'
    report['pose_mode'] = 'FRAME_LOCAL_NO_GT_POSE_IN_CORE'
    report['evaluation_limits'] = dict(
        P06='UNSUPPORTED_SCENE_ASSET_NO_VEHICLE_VISUAL_MODEL',
        P08='PARTIAL_ROI_BOX_ONLY_NO_FULL_DISTANCE_HEIGHT_BOUNDARY_SWEEP',
        parking='NO_PARKING_BRIDGE_OR_SLOT_CLEARANCE_CLASSIFIER_IN_THIS_RUNNER',
        missing_depth='FRESH_MAP_ONLY_NO_PREVIOUS_FREE_TTL_OVERRIDE',
        metrics='NO_PIXEL_GT_IOU_OR_PADDED_FALSE_CLEARANCE_SCORE')
    context_key = hashlib.sha256(canonical(dict(scope='S23_HOST_SIM_REPRESENTATIVE',
        calibration=calibration.sha256, profile=profile.sha256, epoch=epoch, clock_epoch=clock_epoch))).hexdigest()
    try:
        source = SimNnSource(SimNnProfile(yolop_profile, 'sim-wall:'+epoch,
            'jetson-monotonic:'+clock_epoch, calibration.sha256), context_key=context_key)
        began = j_us()
        receipt = source.start(lambda stage: None, startup_deadline_us=began+int(yolop_profile.startup_timeout_s*1_000_000))
        _json(directory/'nn-source-receipt.json', receipt)
        for case in cases:
            case_dir = directory/case.name
            case_dir.mkdir()
            row = dict(name=case.name, status='FAILED', error=None, files=[], live_authority=False)
            capture = original = paired_result = joined = result = camera = None
            geometry_e = nn_e = done = pipeline_profile = None
            try:
                derived = profile.for_scene(case.scene)
                derived.check_scene(case.scene)
                if derived.calibration().sha256 != calibration.sha256:
                    raise ValueError('scene-only profile changed pinned calibration; NN source cannot alias it')
                row['files'].append(_save(case_dir/'profile.json', derived.canonical_bytes))
                row['files'].append(_save(case_dir/'scene.json', case.scene.canonical_bytes))
                row['files'].append(_json(case_dir/'evaluation-only.json', dict(pose=case.pose,
                    boxes=case.scene.fields['track']['layout']['obstacle_seeds'],
                    purpose=case.name, depth_mode=case.depth_mode, core_pose_supplied=False)))
                if case.visual_limit is not None:
                    row.update(status='NOT_EXECUTED', reason=case.visual_limit,
                        actual_nn=False, vehicle_recall=None)
                    continue
                camera = WallSyntheticCamera(derived, case.scene, epoch=epoch, clock_epoch=clock_epoch,
                    effects=DepthEffects())
                warmups = []
                for _ in range(warmup_frames):
                    sequence += 1
                    sample = WallPoseSample.sample(epoch, sequence, lambda: case.pose)
                    discarded = camera.render_diagnostic(sample, expires_j_us=sample.captured_j_us[0]+GEOMETRY_LIFETIME_US)
                    warmups.append(dict(status='DISCARDED_NEW_ORIGINAL_SAMPLE', receipt=discarded.receipt))
                _json(case_dir/'warmup.json', warmups)
                sequence += 1
                sample = WallPoseSample.sample(epoch, sequence, lambda: case.pose)
                geometry_e = sample.captured_j_us[0]+GEOMETRY_LIFETIME_US
                nn_e = sample.captured_j_us[0]+int(yolop_profile.nn_stall_timeout_s*1_000_000)
                capture = camera.render_diagnostic(sample, expires_j_us=geometry_e)
                original = _depth_variant(capture.observation, case.depth_mode)
                submission = source.capture(original, captured_j_us=capture.captured_j_us, expires_j_us=nn_e)
                if submission is None:
                    raise RuntimeError('original SIM NN source unexpectedly busy')
                while j_us() < nn_e:
                    ready = source.poll()
                    if ready:
                        paired_result, = ready
                        break
                    time.sleep(.002)
                if paired_result is None:
                    raise TimeoutError('original NN input expired without its actual full-key pair')
                decode_began = j_us()
                joined = paired_result.to_perception_input(original)
                decode_done = j_us()
                pipeline_profile = PipelineProfile(permission_polygon=calibration.support_polygon,
                    permission_source='EXPLICIT_SYNTHETIC_DIAGNOSTIC_INSTALL_POLICY')
                targets = (TargetClass(.03, .06, .15, qualified=True),)
                core = PerceptionPipeline(calibration, pipeline_profile, targets=targets)
                # 실제 now와 완료 clock을 그대로 쓴다. GT pose/goal/mask/FREE를 넣지 않는다.
                core_began = j_us()
                result = core.process(joined, now_us=core_began, pose=None, completed_clock=j_us)
                done = j_us()
                metrics = _metrics(case, result, joined, geometry_e=geometry_e, nn_e=nn_e, completed_j_us=done,
                    nn_input_received_j_us=paired_result.nn_input.received_j_us, nn_decode_completed_j_us=decode_done)
                metrics['nn_adapter_original_e_check_passed'] = True
                metrics.update(calibration_free_qualified=calibration.free_qualified,
                    targets=[asdict(target) for target in targets], work_profile=asdict(pipeline_profile),
                    timings_us=dict(render=capture.completed_j_us-capture.captured_j_us[1],
                        nn_wait=paired_result.nn_output.received_j_us-submission.clock_sample.j_after_us,
                        decode=decode_done-decode_began, core=done-core_began))
                row.update(status=metrics['status'], geometry_expired=metrics['geometry_expired'],
                    nn_pair_before_e=metrics['nn_pair_before_e'], nn_decode_before_e=metrics['nn_decode_before_e'],
                    end_to_end_exceeded_nn_e=metrics['end_to_end_exceeded_nn_e'])
                row['files'].append(_json(case_dir/'metrics.json', metrics))
                row['files'].append(_json(case_dir/'core-summary.json', result.summary))
                row['files'].extend(_save_map_arrays(case_dir, result))
            except Exception as error:
                row['error'] = dict(type=type(error).__name__, message=str(error)[:512])
                row['failed_j_us'] = j_us()
                if geometry_e is not None:
                    row['geometry_expired'] = row['failed_j_us'] >= geometry_e
                row['files'].append(_save(case_dir/'error.txt', traceback.format_exc().encode('utf-8')))
            finally:
                # 파일 I/O는 계산 뒤에 한다. 진단 저장 지연으로 원 E를 바꾸지 않는다.
                if capture is not None:
                    row['files'].append(_json(case_dir/'capture-receipt.json', capture.receipt))
                    row['files'].append(_save(case_dir/'rgb.nv12', capture.observation.rgb.payload))
                    row['files'].append(_save(case_dir/'rendered-depth.raw16le', capture.observation.depth.payload))
                if original is not None:
                    def frame_fields(frame):
                        return None if frame is None else dict(stamp=asdict(frame.stamp), width=frame.width,
                            height=frame.height, encoding=frame.encoding, exposure_us=frame.exposure_us,
                            bytes=len(frame.payload), sha256=frame.sha256)
                    row['files'].append(_json(case_dir/'input.json', dict(kind=original.kind,
                        calibration_id=original.calibration_id, depth_fault=case.depth_mode,
                        rgb=frame_fields(original.rgb), depth=frame_fields(original.depth))))
                if original is not None and original.depth is not None:
                    row['files'].append(_save(case_dir/'input-depth.raw16le', original.depth.payload))
                if paired_result is not None:
                    packet = paired_result.nn_output
                    row['files'].append(_save(case_dir/'nn-buffer.bin', packet.payload))
                    row['files'].append(_json(case_dir/'nn-packet.json', packet.descriptor()))
                    row['files'].append(_json(case_dir/'nn-input.json', paired_result.nn_input.descriptor()))
                    row['files'].append(_json(case_dir/'ticket.json', dict(key=paired_result.submission.ticket,
                        clock_sample=asdict(paired_result.submission.clock_sample),
                        geometry_expires_j_us=str(geometry_e), nn_expires_j_us=str(nn_e))))
                if joined is not None:
                    for name, mask in (('road', joined.nn.road), ('lane', joined.nn.lane)):
                        row['files'].append(_png(case_dir/(name+'-mask.png'), mask.source*255))
                    row['files'].append(_json(case_dir/'detections.json', dict(
                        source=[item.descriptor() for item in joined.nn.detections_source],
                        nn=[item.descriptor() for item in joined.nn.detections_nn],
                        counts=joined.nn.counts, parser_id=joined.nn.parser_id,
                        config_sha256=joined.nn.config_sha256)))
                    row['files'].append(_png(case_dir/'four-panels.png', _images(original, joined, result, calibration, case,
                        geometry_e=geometry_e, completed_j_us=done if done is not None else row.get('failed_j_us'),
                        depth_caps=None if pipeline_profile is None else pipeline_profile.depth)))
                if camera is not None:
                    row['renderer_counts'] = camera.counts
                    camera.close()
                row['geometry_expires_j_us'], row['nn_expires_j_us'] = geometry_e, nn_e
                _json(case_dir/'result.json', row)
                report['cases'].append(row)
    except Exception as error:
        report['error'] = dict(type=type(error).__name__, message=str(error)[:512])
        _save(directory/'error.txt', traceback.format_exc().encode('utf-8'))
    finally:
        if source is not None:
            try:
                source.close(deadline_us=j_us()+int(yolop_profile.close_timeout_s*1_000_000))
            except Exception as error:
                report['close_error'] = dict(type=type(error).__name__, message=str(error)[:512])
            report['nn_source_counts'] = source.counts
        failures = sum(row['status'] == 'FAILED' for row in report['cases'])
        incomplete = sum(row['status'] in ('INCOMPLETE', 'NOT_EXECUTED') for row in report['cases'])
        late = sum(row.get('geometry_expired', False) for row in report['cases'])
        complete = len(report['cases']) == len(cases) and not report.get('error') and not report.get('close_error')
        report.update(case_count=len(report['cases']), failures=failures, incomplete=incomplete,
            geometry_expired_cases=late, status='FAILED' if not complete or failures else 'INCOMPLETE' if incomplete or late else 'OBSERVED',
            exit_code=1 if not complete or failures else 2 if incomplete or late else 0)
        _json(directory/'result.json', report)
    return directory, report
