"""합성/검증된 YOLOP 기록을 같은 관측·국소 지도 core로 연결하는 유한 offline 진단.

기본은 pose 없는 frame-local 처리다. GT는 명시 oracle_pose일 때만 별도 파일에서
읽는다. 소스 기록을 변경하지 않으며 실제 주행·실물 보정·S22 성능 수락을 만들지 않는다.
"""

from dataclasses import asdict
import json
import math
import os
from pathlib import Path
import uuid
from time import perf_counter_ns


def _canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False) + '\n').encode('ascii')


def _read(path, cap):
    from ..simulation.dataset import _read as bounded_read
    return bounded_read(Path(path), cap)


def _calibration(path):
    from .calibration import CalibrationProfile
    return CalibrationProfile.from_dict(json.loads(_read(path, 65_536)))


def _synthetic(directory, calibration, frames):
    from ..simulation import SyntheticCamera, SyntheticDatasetWriter, SceneSnapshot, demo_scene, oracle
    scene = demo_scene()
    camera = SyntheticCamera(calibration)
    epoch = str(uuid.uuid4())
    x, y, yaw = scene.fields['track']['layout']['start_pose']
    # 차체 동역학/회피 성공을 주장하지 않는 ±4cm scripted pose다. seed 장애물을 관통하지 않는다.
    with SyntheticDatasetWriter(directory, camera.calibration, effects=camera.effects, scene=scene) as writer:
        for sequence in range(frames):
            displacement = .04 * math.sin(sequence / 8)
            snapshot = SceneSnapshot(epoch, sequence, sequence * 1_000_000 // 15,
                (x + displacement * math.cos(yaw), y + displacement * math.sin(yaw), yaw), scene)
            writer.write(camera.capture(snapshot), oracle=oracle(snapshot))
    return camera.calibration


def _oracle_rows(directory):
    """호출 자체가 oracle opt-in이다. 최대256 원 row를 frame key에 결박한다."""
    raw = _read(Path(directory) / 'oracle.jsonl', 256 * 65_536)
    lines = raw.splitlines(keepends=True)
    if len(lines) > 256 or any(not line.endswith(b'\n') or len(line) > 65_536 for line in lines):
        raise ValueError('oracle record has an incomplete or oversized tail')
    rows = {}
    for line in lines:
        value = json.loads(line)
        if value.get('kind') != 'SYNTHETIC_ORACLE' or _canonical(value) != line:
            raise ValueError('oracle record is not the separate canonical synthetic producer')
        key = value['epoch'], value['sequence'], value['sim_time_us']
        if key in rows:
            raise ValueError('duplicate oracle capture key')
        rows[key] = value
    return rows


def _oracle_pose(observation, rows, scene_sha256):
    from ..mapping.odometry import PoseEstimate, PoseSample
    stamp = observation.rgb.stamp
    if observation.kind != 'SYNTHETIC' or stamp.captured_us is None:
        raise ValueError('oracle pose requires an original synthetic capture interval')
    epoch = stamp.clock_id.removeprefix('simulation:')
    row = rows[(epoch, stamp.sequence, stamp.captured_us[0])]
    if scene_sha256 is not None and row['scene_sha256'] != scene_sha256:
        raise ValueError('oracle belongs to another original scene')
    if type(row['pose']) is not list or len(row['pose']) != 3:
        raise ValueError('oracle pose is not a finite SE2 sample')
    sample = PoseSample(epoch, stamp.clock_id, observation.calibration_id, stamp.captured_us[0],
                        *row['pose'], 0., 0., origin='ORACLE_SIMULATION')
    return PoseEstimate(sample, stamp.captured_us, stamp.captured_us)


def run_perception_bench(output, *, source='synthetic', dataset=None, calibration_path=None,
                         frames=4, oracle_pose=False, allow_incomplete=False):
    """새 출력만 만들고 최대256개 원 관측을 처리한다. 처리 실패도 초기/최종 receipt에 남긴다.

    oracle은 합성 평가 전용이며 기본은 읽지 않는다. 실제 기록에는 외부 보정이 필수고
    missing capture interval/depth bridge를 현재 시각이나 가짜 정지 pose로 채우지 않는다.
    """
    if source not in ('synthetic', 'yolop-replay'):
        raise ValueError('unsupported perception source')
    if type(frames) is not int or not 1 <= frames <= 256:
        raise ValueError('perception bench frames must be in 1..256')
    if type(oracle_pose) is not bool or type(allow_incomplete) is not bool:
        raise ValueError('perception bench options must be exact bool')
    if source == 'yolop-replay' and (dataset is None or calibration_path is None or oracle_pose):
        raise ValueError('physical replay requires dataset/calibration and forbids oracle pose')
    directory = Path(output).expanduser().absolute()
    directory.mkdir(mode=0o700)
    receipt_path = directory / 'perception-bench-result.json'
    report = dict(schema_version=1, scope='OFFLINE_PERCEPTION_LOCAL_MAP_NOT_DRIVING', source=source,output=str(directory),
        complete=False, exit_code=1, error=None, frames_requested=frames, frames_processed=0,
        source_dataset=None if dataset is None else str(Path(dataset).absolute()), source_complete=None,
        oracle_pose=oracle_pose, pose_mode='ORACLE_SIMULATION' if oracle_pose else 'FRAME_LOCAL',
        calibration_id=None, calibration_kind=None, diagnostic_images=0, last_summary=None,
        metric_ready=False, world_valid=False, live_authority=False,
        assumptions=dict(physical_calibration_accepted=False, synthetic_vehicle_dynamics_validated=False,
                         source_model_accuracy_evaluated=False, performance_accepted=False))
    with receipt_path.open('xb') as output_file:
        output_file.write(_canonical(report))
    try:
        from .calibration import synthetic_calibration
        from .pipeline import PerceptionPipeline, PipelineProfile, render_diagnostics
        from .free_space import TargetClass
        if source == 'synthetic':
            from ..simulation import SyntheticDatasetReader
            if dataset is None:
                calibration = synthetic_calibration() if calibration_path is None else _calibration(calibration_path)
                dataset = directory / 'dataset'
                _synthetic(dataset, calibration, frames)
            reader = SyntheticDatasetReader(dataset, allow_incomplete=allow_incomplete)
            calibration = reader.calibration
            if calibration_path is not None and _calibration(calibration_path).sha256 != calibration.sha256:
                raise ValueError('external calibration differs from the original synthetic dataset')
            observations = iter(reader)
            report['source_complete'] = reader.complete
            permission = calibration.support_polygon
            profile = PipelineProfile(permission_polygon=permission, permission_source='SYNTHETIC_INSTALL_POLICY')
            # demo의 18cm 정사각 box는 모서리 반경이 약12.73cm다.
            # core 최소 크기와 전체 충돌 extent를 별도로 제한한다.
            targets = (TargetClass(.03, .06, .15, qualified=True),)
            report['assumptions'].update(synthetic_permission_polygon=permission,
                synthetic_permission_source=profile.permission_source,
                synthetic_target_classes=[asdict(target) for target in targets],
                synthetic_scene_and_camera_measured=False)
            provenance = reader.manifest.get('scene_provenance') or {}
            scene_sha256 = provenance.get('scene_sha256')
            rows = _oracle_rows(dataset) if oracle_pose else None
        else:
            from ..recording.yolop_reader import YolopDiagnosticReader
            from .adapters import from_diagnostic_sample
            calibration = _calibration(calibration_path)
            if calibration.kind == 'SYNTHETIC':
                raise ValueError('synthetic calibration cannot qualify physical replay')
            reader = YolopDiagnosticReader(Path(dataset), allow_incomplete=allow_incomplete)
            validated = reader.validate()
            report['source_complete'] = validated['source_complete']
            report['source_validation'] = validated
            observations = (from_diagnostic_sample(sample, calibration) for sample in reader.samples())
            profile, targets, rows, scene_sha256 = PipelineProfile(), (), None, None
            report['assumptions'].update(physical_permission_source='UNCONFIGURED', physical_target_classes_accepted=False)
        report.update(source_dataset=str(Path(dataset).absolute()), calibration_id=calibration.sha256,
                      calibration_kind=calibration.kind)
        with (directory / 'calibration.json').open('xb') as output_file:
            output_file.write(_canonical(calibration.to_dict()))
        pipeline = PerceptionPipeline(calibration, profile, targets=targets)
        import cv2
        with (directory / 'stages.jsonl').open('xb') as stages:
            for ordinal in range(frames):
                read_started = perf_counter_ns()
                try:
                    observation = next(observations)
                except StopIteration:
                    break
                read_done = perf_counter_ns()
                pose = None if rows is None else _oracle_pose(observation, rows, scene_sha256)
                available = [observation.rgb.stamp.available_us]
                if observation.depth is not None:
                    available.append(observation.depth.stamp.available_us)
                if observation.nn is not None:
                    available.append(observation.nn.packet.received_j_us)
                result = pipeline.process(observation, now_us=max(available), pose=pose)
                core_done = perf_counter_ns()
                report['frames_processed'] += 1
                report['last_summary'] = result.summary
                if ordinal < 8:
                    for label, image in zip(('overlay', 'map'), render_diagnostics(result, calibration)):
                        ok, data = cv2.imencode('.png', image)
                        if not ok or data.nbytes > 2 * 1024**2:
                            raise ValueError('bounded perception diagnostic image encoding failed')
                        with (directory / f'{ordinal:04d}-{label}.png').open('xb') as output_file:
                            output_file.write(data.tobytes())
                        report['diagnostic_images'] += 1
                render_done = perf_counter_ns()
                stage = _canonical(dict(ordinal=ordinal, summary=result.summary,
                    timings_us=dict(input=(read_done-read_started)//1000,core=(core_done-read_done)//1000,
                        render=(render_done-core_done)//1000,total=(render_done-read_started)//1000)))
                if len(stage) > 65_536:
                    raise ValueError('perception stage summary exceeds 64KiB')
                stages.write(stage)
                stages.flush()
        if not report['frames_processed']:
            raise ValueError('no validated observations were available')
        report.update(complete=True, exit_code=0)
    except Exception as error:
        report.update(complete=False, exit_code=1, error=type(error).__name__ + ': ' + str(error)[:512])
    finally:
        raw = _canonical(report)
        if len(raw) > 131_072:
            raise ValueError('perception final receipt exceeds 128KiB')
        temporary = directory / '.perception-bench-result.tmp'
        with temporary.open('xb') as output_file:
            output_file.write(raw)
        os.replace(temporary, receipt_path)
    return report
