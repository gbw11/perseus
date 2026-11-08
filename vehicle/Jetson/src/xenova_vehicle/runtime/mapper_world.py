"""현재 compute 후보와 원 센서·정합 자료를 공용 World 후보 bytes에 연결한다.

전역 경계는 현재 grid 영역이며 reference는 채택한 열린 basis 그대로다. 경로·차로
선택이나 scene 승인을 만들지 않는다. None은 발행 보류다. 최종 원 draft/current
검사와 sequence 채택은 caller의 짧은 adoption lock/outbox 경계에 남는다.
"""

import math
import struct

from .mapper_mapping import MappingFrameDraft
from .mapper_catalogs import MappingState
from .map_checkpoint import FrameEvidence
from ..mapping.alignment import MapOdomAlignment
from ..mapping.odometry import PoseEstimate
from ..mapping.road_basis import RoadBasisSnapshot
from ..perception.calibration import CalibrationProfile
from ..perception.pipeline import PerceptionResult
from ..storage.contracts import StorageContext, json_bytes
from ..transport.sensor_samples import SensorSamples, VehicleEvidence
from ..transport.world_adapter import (WorldBuildError, WorldSource, build_world,
    lane_model_from_road, _f32, _f32_up)


class _NotReady(Exception):
    pass


def _need(value):
    if not value:
        raise _NotReady


def _uint(value, *, positive=False):
    if type(value) is not int or not int(positive) <= value < 2**63:
        raise WorldBuildError('original bounded unsigned World sequence/time required')


def _upper(value):
    """부호에 관계없이 원 좌표 이상인 float32 경계를 사용한다."""
    result = _f32(value)
    if result < value:
        bits = struct.unpack('<I', struct.pack('<f', result))[0]
        bits = 1 if result == 0 else bits + (1 if result > 0 else -1)
        result = struct.unpack('<f', struct.pack('<I', bits))[0]
    _need(math.isfinite(result))
    return result


def _domain(spec, count):
    """같은 x의 두 edge로 wire grid 직사각형을 표현한다. 차로 outer와 독립이다."""
    _need(2 <= count <= 64 and 2 <= spec.width <= 64 and 2 <= spec.height <= 64)
    x0, y0 = map(_f32, spec.origin_xy)
    resolution = _f32(spec.resolution_m)
    x1, y1 = x0 + spec.width * resolution, y0 + spec.height * resolution
    right, top = _upper(x1), _upper(y1)
    xs = tuple(_f32(x0 + (right-x0)*i/(count-1)) for i in range(count))
    _need(xs[0] == x0 and xs[-1] == right and all(a < b for a, b in zip(xs, xs[1:])))
    _need(y0 < top and right >= x1 and top >= y1)
    error = max(math.hypot(x0+x*resolution-(spec.origin_xy[0]+x*spec.resolution_m),
                          y0+y*resolution-(spec.origin_xy[1]+y*spec.resolution_m))
                for x in (0, spec.width) for y in (0, spec.height))
    error += math.hypot(right-x1, top-y1)
    return tuple((x, top) for x in xs), tuple((x, y0) for x in xs), error


def _preferred(spec, road):
    """현재 outer 사이 nominal cell-center cost 표시다. 셀 상태/통행권은 바꾸지 않는다."""
    import numpy as np
    h = road.hypotheses[0]
    polygon = h.left_outer + tuple(reversed(h.right_outer))
    x0, y0 = map(_f32, spec.origin_xy)
    resolution = _f32(spec.resolution_m)
    x, y = np.meshgrid(x0+(np.arange(spec.width)+.5)*resolution,
                       y0+(np.arange(spec.height)+.5)*resolution)
    inside = np.zeros((spec.height, spec.width), dtype=bool)
    for a, b in zip(polygon, polygon[1:]+polygon[:1]):
        if a[1] != b[1]:
            inside ^= ((a[1] > y) != (b[1] > y)) & (x < a[0]+(y-a[1])*(b[0]-a[0])/(b[1]-a[1]))
    _need(bool(inside.any()))
    return inside


def _heading_bound(basis, road):
    # reference endpoint box의 가능한 방향도 포함한다. 짧은 chord를 새 기한/폭으로 우회하지 않는다.
    bound = road.hypotheses[0].yaw_error_rad
    for i, (a, b) in enumerate(zip(basis.points, basis.points[1:])):
        length = math.dist(a, b)
        error = basis.point_error_m[i] + basis.point_error_m[i+1]
        bound = max(bound, math.pi if error >= length else math.asin(error/length))
    return bound


def _current(draft, state, basis, context, source, calibration, sensors, vehicle, lease, now):
    from xenova_drive_world.mapper_clock import ClockLease
    _need(draft.ready and type(draft.result) is PerceptionResult and type(draft.frame) is FrameEvidence)
    _need(draft.context == context and draft.inputs.storage_context == context)
    candidate = draft.catalog_draft
    _need(candidate is not None and candidate.state is state and candidate.context == context
          and candidate.original_job is draft.original_job and draft.original_job.fresh(now))
    _need(type(source) is dict and source == draft.inputs.source == sensors.context.source)
    _need(type(lease) is ClockLease and lease is draft.inputs.clock_lease and lease is draft.frame.clock_lease
          and lease.source == source and lease.pi_host_boot == context.pi_host_boot)
    _need(sensors.context.calibration == calibration and sensors.current_vehicle(vehicle, now))
    _need(vehicle is draft.inputs.vehicle and source['calibration'] == calibration.vehicle_bundle_sha256)
    _need(context.geometry_origin_fields is not None)
    origin = context.geometry_origin_fields
    _need(all(origin[key] == value for key, value in (
        ('vehicle', source['vehicle']), ('pi_host_boot', sensors.context.pi_host_boot),
        ('gateway_instance', sensors.context.gateway_instance), ('pi_session', sensors.context.pi_session),
        ('device_session', sensors.context.device_session))))
    _need(all(source[key] == getattr(context, field) for key, field in (
        ('mapper_instance', 'origin_mapper_instance'), ('map_epoch', 'map_epoch'), ('frame_epoch', 'frame_epoch'),
        ('odom_epoch', 'odom_epoch'), ('configuration', 'configuration'), ('calibration', 'calibration'),
        ('primitive', 'primitive_sha256'))))
    original = json_bytes(draft.original_job.context_bytes, 65536)
    _need(original.get('context_sha256') == context.sha256 and original.get('storage_context') == context.fields
          and original.get('source') == source)
    frame, result = draft.frame, draft.result
    raw = frame.scope.raw_binding
    binding = json_bytes(context.source_binding, 2048)
    _need(frame.scope.context == context and frame.scope.calibration == calibration
          and binding['raw_binding_sha256'] == raw.sha256 and binding['mapper_worker'] == context.mapper_worker
          and source['clock_epoch'] == raw.fields['clock_epoch']
          and draft.original_job.source_generation == raw.sha256)
    _need(draft.pipeline.calibration == calibration and calibration.kind == 'LIVE'
          and frame.observation.kind == result.summary.get('kind') == 'LIVE')
    _need(not draft.pipeline._restore_pending and result.summary.get('localization_qualified') is True
          and result.summary.get('quarantined') is False)
    snapshot = result.map_snapshot
    _need(snapshot['kind'] == 'LIVE' and snapshot['calibration_id'] == calibration.sha256
          and snapshot['clock_id'] == source['clock_epoch'] and draft.pipeline.grid is not None
          and snapshot['frame_id'] == draft.pipeline.grid.frame_id
          and snapshot['revision'] == draft.pipeline.grid.revision)
    stamp = frame.observation.rgb.stamp
    _need(result.tape is not None and stamp.key == result.summary.get('rgb_source_key')
          and result.tape.source_key == stamp.key)
    model = state.view.lane
    association = state.lane.current_association
    _need(model is not None and state.view.lane_status in ('NEW', 'UNIQUE')
          and state.lane.context == context and state.lane.latest is model
          and association is not None and association.status in ('NEW', 'UNIQUE')
          and association.source is model.source and association.catalog_revision+1 == model.model_revision
          and state.lane.revision == model.model_revision)
    _need(model.source.stamp == stamp and model.source.input_kind == 'LIVE' and model.source.qualified
          and model.source.domain == (calibration.sha256, context.map_epoch, snapshot['frame_id'], context.odom_epoch)
          and model.original_context_sha256 == context.sha256 and model.road.source_key == stamp.key
          and model.source.map_revision == snapshot['revision']
          and all(identity < state.lane.durable_hwm for identity in model.lane_ids))
    _need(state.view.road_basis is basis and basis.status == 'READY' and basis.latest_lane_source is model.source
          and basis.lane_ids == model.lane_ids and basis.latest_model_revision == model.model_revision
          and state.road_basis.snapshot() == basis and 2 <= len(basis.points) <= 64)
    capture = frame.observation.capture_interval
    _need(capture is not None and type(draft.inputs.pose) is PoseEstimate and capture == draft.inputs.pose.captured_us)
    lease.j_interval_to_pi(capture, now_j_us=now)
    _need(lease.pi_interval_to_j(frame.pi_interval, now_j_us=now)[0] <= capture[0]
          and capture[1] <= lease.pi_interval_to_j(frame.pi_interval, now_j_us=now)[1])
    alignment = draft.pipeline._alignment
    _need(type(alignment) is MapOdomAlignment and alignment.has_metadata and alignment.valid_at(now)
          and result.summary.get('alignment_metadata') == alignment.fields()
          and alignment.registration_revision == model.source.registration_revision)
    return result, snapshot, model, capture, alignment


def build_mapping_world(draft, catalog_state, basis, *, context, source, calibration,
                        sensors, current_vehicle, clock_lease, sequence, completed_j_us, published_j_us,
                        scene_draft=None,hazard_draft=None,atlas_owner=None):
    """원 채택 후보를 build_world에 전달한다. None은 원 자료/기한/표현의 NOT_READY다.

    sensors는 같은 compute 회차의 실제 admission owner다. 외부 source 교체 여부와
    sequence의 단조 채택은 caller가 최종 lock에서 다시 판단한다. 자기 시각을 읽거나
    원 source/정합 기한을 갱신하지 않는다. retained-static은 원 freeze와 현재 Scene 후보를
    추가 검산한 별도 snapshot에서만 표현하며 LIVE grid는 변경하지 않는다.
    """
    if catalog_state is None or basis is None or context is None or current_vehicle is None:
        return None
    if (type(draft) is not MappingFrameDraft or type(catalog_state) is not MappingState
            or type(basis) is not RoadBasisSnapshot or type(context) is not StorageContext
            or type(calibration) is not CalibrationProfile or type(sensors) is not SensorSamples
            or type(current_vehicle) is not VehicleEvidence):
        raise WorldBuildError('actual mapping/catalog/basis/context/calibration/sensor originals required')
    _uint(sequence, positive=True); _uint(completed_j_us); _uint(published_j_us)
    try:
        result, snapshot, model, capture, alignment = _current(draft, catalog_state, basis, context, source,
            calibration, sensors, current_vehicle, clock_lease, published_j_us)
        retained=None
        if scene_draft is not None:
            from .retained_world import project_retained_grid
            retained=project_retained_grid(draft,scene_draft=scene_draft,hazard_draft=hazard_draft,
                source=source,clock_lease=clock_lease,published_j_us=published_j_us,atlas_owner=atlas_owner)
            if retained is not None:snapshot=retained.snapshot
        snapshot_us = result.summary['now_us']
        _need(capture[1] <= snapshot_us <= completed_j_us <= published_j_us)
        mapped = alignment.apply(PoseEstimate(current_vehicle.pose_sample,
            current_vehicle.j_interval, current_vehicle.j_interval)).sample
        from xenova_drive_world import schema
        spec = snapshot['spec']
        upper, lower, grid_error = _domain(spec, len(basis.points))
        mask = _preferred(spec, model.road)
        value = schema.WorldSnapshot(major=1, minor=2, required_features=11, sequence=sequence,
            vehicle=source['vehicle'], mapper_boot=source['mapper_boot'], mapper_instance=source['mapper_instance'],
            map_epoch=context.map_epoch, frame_epoch=context.frame_epoch, odom_epoch=context.odom_epoch,
            configuration=context.configuration, calibration=context.calibration, primitive=context.primitive_sha256,
            clock_epoch=source['clock_epoch'], reference_id=basis.reference_id, reference_revision=basis.reference_revision,
            reference_odom_seq=current_vehicle.anchor.sequence, observation_seq=draft.frame.observation_sequence,
            observed_us=sum(capture)//2, completed_us=completed_j_us, published_us=published_j_us,
            complete=True, truncated=False, inflation_policy=0)
        value.origin.CopyFrom(schema.Pose(x=_f32(spec.origin_xy[0]), y=_f32(spec.origin_xy[1]), yaw=0.))
        anchor = value.reference_vehicle_pose
        anchor.x, anchor.y, anchor.yaw = map(_f32, (mapped.x, mapped.y, mapped.yaw))
        for point in basis.points: value.reference.add(x=point[0], y=point[1])
        for point in upper: value.left_boundary.add(x=point[0], y=point[1])
        for point in lower: value.right_boundary.add(x=point[0], y=point[1])
        value.lane_model.CopyFrom(lane_model_from_road(model.road, lane_ids=model.lane_ids,
            model_revision=model.model_revision, lane_revisions=model.lane_revisions, crossing=model.crossing))
        interval = alignment.observed_j_interval
        # 원 anchor 오차를 빼서 좁히지 않는다. Pi가 원 drift를 추가하는 보수성도 유지한다.
        value.localization.CopyFrom(schema.MapLocalization(registration_revision=alignment.registration_revision,
            anchor_position_bound_m=_f32_up(mapped.position_error_m+math.hypot(anchor.x-mapped.x, anchor.y-mapped.y)),
            anchor_heading_bound_rad=_f32_up(mapped.yaw_error_rad+abs(anchor.yaw-mapped.yaw)),
            geometry_position_bound_m=_f32_up(basis.geometry_error_m+grid_error),
            geometry_heading_bound_rad=_f32_up(_heading_bound(basis, model.road)),
            observed_mapper_us=sum(interval)//2, valid_for_us=alignment.valid_for_us,
            position_drift_bound_mps=_f32_up(alignment.position_drift_mps),
            heading_drift_bound_rps=_f32_up(alignment.yaw_drift_rps), quality=1,
            timestamp_error_us=max((capture[1]-capture[0]+1)//2, (interval[1]-interval[0]+1)//2)))
        if retained is not None:
            from xenova_drive_world import decode
            value.scene.CopyFrom(decode(retained.scene_ref,schema.SceneAdmissionRef))
        bound = WorldSource.from_calibration('LIVE', snapshot['frame_id'], context.frame_epoch,
            snapshot['clock_id'], calibration, capture, snapshot_us, wire_clock_epoch=source['clock_epoch'])
        static=None if retained is None else retained.static_retained
        built = build_world(snapshot, value, source=bound, preferred_road=mask,static_retained=static)
        inventory = catalog_state.view.parking
        if inventory is not None:
            _need(inventory.context == context and inventory.input_kind == 'LIVE'
                  and inventory.source_clock == snapshot['clock_id'])
            # 원 publication 후보의 geometry/age/UNKNOWN/cap을 다시 산출하지 않는다.
            value = inventory.apply(built.message())
            built = build_world(snapshot, value, source=bound, preferred_road=mask,static_retained=static)
        _need(b''.join(struct.pack('<ff', p.x, p.y) for p in built.message().reference) == basis.points_f32)
        return built
    except (_NotReady, ValueError, KeyError, OverflowError):
        return None
