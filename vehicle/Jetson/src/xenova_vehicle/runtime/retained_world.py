"""원 Scene/commit의 FREE만 별도 World 후보 사본에 투영한다.

LIVE grid, 원 CELL/Pi 시각과 source 순번은 바꾸지 않는다. 이 함수는 새 관측,
localization 또는 이동권위를 만들지 않으며 최종 World source/adoption gate는 caller다.
rolling 본문은 명시한 실제 owner의 기존 archive pin 안에서 현재 ROI만 읽는다.
"""

from dataclasses import asdict, dataclass
from hashlib import sha256

from ..mapping.local_map import EvidenceGrid, FREE, OCCUPIED, UNKNOWN, GridSpec
from ..mapping.map_lattice import read_atlas_window
from ..storage.contracts import CommitReceipt, canonical, json_bytes, uint
from ..storage.mapper_state import decode_mapper_state
from ..storage.records import CELL, TileVersion


class RetainedWorldError(ValueError):
    """원 Scene/source/geometry/proof 사본이 서로 맞지 않는다."""


@dataclass(frozen=True)
class RetainedProjection:
    snapshot: dict
    static_retained: object
    scene_ref: bytes
    expires_j_us: int
    grant_sha256: str


def _need(value, message):
    if not value:
        raise RetainedWorldError(message)


def _frozen_origin(frozen):
    from .scene import FrozenSurvey, RestoredSurvey
    from .map_checkpoint import SourceComplete
    if type(frozen) is FrozenSurvey:
        request = frozen.request; receipt = frozen.receipt
        if receipt is None:
            return None
        _need(type(receipt) is CommitReceipt and receipt.status in ('COMMITTED', 'DUPLICATE')
            and receipt.current(request.job, request.payload, request.job.context.sha256, receipt.committed_j_us),
            'actual original frozen checkpoint COMMITTED receipt required')
        return request.job, request.payload.evidence, request.digest
    if type(frozen) is RestoredSurvey:
        complete = frozen.complete
        _need(type(complete) is SourceComplete and complete.original_commit is not None,
              'actual restored complete/commit origin required')
        _need(complete.original_commit.validate(complete.original_job, complete.manifest, complete.checkpoint_ref)
              == frozen.checkpoint_sha256, 'original complete checkpoint digest differs')
        return complete.original_job, complete.evidence, complete.original_commit.receipt.job_digest
    raise RetainedWorldError('actual FrozenSurvey or RestoredSurvey required')


def _metadata(frozen, job, evidence, pipeline):
    manifest = json_bytes(frozen.manifest, 16384)
    _need(manifest.get('profile') == 'xenova-checkpoint-1' and manifest.get('intent') == 'CHECKPOINT'
          and manifest.get('context_sha256') == job.context.sha256,
          'original checkpoint manifest/context differs')
    state, _ = decode_mapper_state(manifest.get('scene'), evidence.contexts)
    if type(state) is not dict or state.get('profile') != 'mapper-pipeline-state-1':
        return None
    if 'spec' not in state:
        return None
    fields = state['spec']
    _need(type(fields) is dict and set(fields) == {'width', 'height', 'resolution_m', 'origin_xy'},
          'exact original mapper grid spec required')
    spec = GridSpec(fields['width'], fields['height'], fields['resolution_m'], tuple(fields['origin_xy']))
    _need(state['storage_context'] == job.context.fields and state['calibration_id'] == pipeline.calibration.sha256
          and state['pipeline_profile_sha256'] == sha256(canonical(asdict(pipeline.profile))).hexdigest()
          and state['frame_id'] == pipeline.grid.frame_id and state['kind'] == pipeline.grid.kind,
          'original mapper profile/calibration/frame differs')
    window = pipeline.grid.rolling_window
    if window is None:
        _need('atlas' not in state and spec == pipeline.grid.spec, 'fixed historical grid spec differs')
    else:
        if 'atlas' not in state or pipeline._rolling_policy is None:
            return None
        old = read_atlas_window(state['atlas'], spec, policy=pipeline._rolling_policy)
        _need(old.lattice == window.lattice and (old.width, old.height) == (window.width, window.height),
              'original rolling lattice/shape differs')
    refs = tuple(tuple(item) for item in manifest['tiles'])
    _need(len(refs) <= 128 and refs == tuple(sorted(refs)) and len({item[:2] for item in refs}) == len(refs),
          'original full tile reference order differs')
    return refs


def _fixed_reader(frozen, refs, map_epoch):
    from .scene import FrozenSurvey
    tiles = frozen.fixed_tiles if type(frozen) is FrozenSurvey else frozen.complete.tiles
    if not tiles:
        return None
    _need(type(tiles) is tuple and len(tiles) <= 4 and all(type(tile) is TileVersion for tile in tiles),
          'bounded actual full fixed tile copies required')
    _need(tuple(sorted(tile.reference for tile in tiles)) == refs, 'fixed copies differ from original full manifest')
    entries = {(tile.tile_x, tile.tile_y): tile for tile in tiles}
    for tile in tiles:
        _need(tile.map_epoch == map_epoch and sha256(tile.payload).hexdigest() == tile.sha256,
              'original fixed tile map/hash differs')
    return lambda x, y: None if (x, y) not in entries else entries[(x, y)].payload


def _hazards(hazard, grid, context, scope, np):
    window = grid.rolling_window
    from ..storage.geometry_scope import CatalogGeometryScope
    geometry = (CatalogGeometryScope.from_context(context, 'SLOT').bytes, scope.method_manifest_sha256,
        grid.frame_id, grid.calibration_id, grid.clock_id, grid.kind,
        grid.spec if window is None else ('ROLLING', window.lattice))
    _need(hazard.geometry == geometry and hazard.spec == grid.spec and hazard.window == window,
          'actual current hazard geometry/window differs')
    size = grid.spec.width * grid.spec.height
    _need(type(hazard.occupied) is bytes and len(hazard.occupied) == (size + 7) // 8,
          'exact current hazard occupied bitset required')
    _need(not size % 8 or not hazard.occupied[-1] >> (size % 8), 'nonzero unused hazard bits')
    result = np.unpackbits(np.frombuffer(hazard.occupied, dtype=np.uint8), bitorder='little', count=size).astype(bool)
    return result.reshape(grid.state.shape), dict(hazard.global_occupied)


def _project(draft, refs, reader, hazard, pi_upper, published_j_us, np):
    grid = draft.pipeline.grid; snapshot = draft.result.map_snapshot
    shape = grid.state.shape; size = grid.spec.width * grid.spec.height
    _need(size <= 4096 and shape == (grid.spec.height, grid.spec.width), 'bounded current ROI required')
    for name, dtype in (('state', 'u1'), ('observed_us', '<u8'), ('time_known', '?'),
                        ('conflict', '?'), ('unordered_hit', '?')):
        value = getattr(grid, name)
        _need(type(value) is np.ndarray and value.shape == shape and value.dtype == np.dtype(dtype),
              'actual raw grid shape/dtype differs: ' + name)
    _need(not np.any(grid.state > 2), 'invalid raw grid state')
    _need(snapshot['spec'] == grid.spec and snapshot['revision'] == grid.revision
          and all(snapshot[key] == getattr(grid, key) for key in ('frame_id', 'calibration_id', 'clock_id', 'kind'))
          and snapshot.get('live_authority') is False and snapshot.get('world_valid') is False,
          'actual current candidate snapshot required')
    arrays = snapshot['arrays']; expected = dict(state='u1', age_ms='<u4', support='?', permission='?', conflict='?', unordered_hit='?')
    _need(type(arrays) is dict and set(arrays) == set(expected), 'exact bounded candidate snapshot arrays required')
    for name, dtype in expected.items():
        value = arrays[name]
        _need(type(value) is np.ndarray and value.shape == shape and value.dtype == np.dtype(dtype),
              'candidate array shape/dtype differs: ' + name)
    _need(not np.any(arrays['state'] > 2), 'invalid candidate state')
    copies = {name: value.copy() for name, value in arrays.items()}
    local_hazard, global_hazard = hazard
    blocked = ((grid.state == OCCUPIED) | grid.conflict | grid.unordered_hit | local_hazard)
    live = ((copies['state'] == FREE) & (grid.state == FREE) & copies['support'] & copies['permission']
            & grid.time_known & (grid.observed_us <= published_j_us))
    positions = live.nonzero()
    live[positions] &= published_j_us - grid.observed_us[positions] <= grid.free_ttl_us
    live &= ~blocked
    copies['state'][(copies['state'] == FREE) & ~live] = UNKNOWN
    mask = np.zeros(shape, dtype=bool); ref_map = {item[:2]: item for item in refs}
    bodies = {}; window = grid.rolling_window
    for index in range(size):
        row, col = divmod(index, grid.spec.width)
        gx, gy = (col, row) if window is None else window.local_to_global(index)
        tx, x = divmod(gx, 64); ty, y = divmod(gy, 64); key = tx, ty
        bits = global_hazard.get(key)
        if bits is not None and bits[(y * 64 + x) // 8] & (1 << ((y * 64 + x) % 8)):
            if copies['state'][row, col] == FREE:
                copies['state'][row, col] = UNKNOWN
            continue
        if blocked[row, col] or live[row, col]:
            continue
        if key not in bodies:
            _need(len(bodies) < 4, 'current ROI read exceeds four original tiles')
            body = reader(tx, ty)
            if body is not None:
                _need(type(body) is bytes and len(body) == CELL.size * 4096 and key in ref_map
                      and sha256(body).hexdigest() == ref_map[key][3], 'original archive body/hash differs')
            bodies[key] = body
        body = bodies[key]
        if body is None:
            continue
        cell = CELL.unpack_from(body, (y * 64 + x) * CELL.size)
        if cell[0] != 0 or not cell[1] & 1 or cell[1] & 4 or not cell[6]:
            continue
        _need(cell[7] <= cell[8] <= pi_upper, 'historical FREE Pi interval is future or reversed')
        age = min((pi_upper - cell[7] + 999) // 1000, (1 << 32) - 1)
        # 미확인 J age sentinel은 기간 하한이 아니다. 실제 기존 J age가 있으면 낮추지 않는다.
        if grid.time_known[row, col]:
            age = max(age, int(copies['age_ms'][row, col]))
        copies['age_ms'][row, col] = age
        copies['state'][row, col] = FREE; copies['support'][row, col] = True; copies['permission'][row, col] = True
        mask[row, col] = True
    for name, value in copies.items():
        copies[name] = np.frombuffer(value.tobytes(order='C'), dtype=value.dtype).reshape(shape)
    result = dict(snapshot, arrays=copies, counts=dict(unknown=int(np.count_nonzero(copies['state'] == UNKNOWN)),
        free=int(np.count_nonzero(copies['state'] == FREE)), occupied=int(np.count_nonzero(copies['state'] == OCCUPIED))))
    return result, np.frombuffer(mask.tobytes(order='C'), dtype=np.bool_).reshape(shape)


def project_retained_grid(draft, *, scene_draft, hazard_draft, source, clock_lease, published_j_us, atlas_owner=None):
    """현재 Scene의 원 FREE를 현재 ROI 사본에만 투영한다. 정상 미준비는 None이다."""
    from xenova_drive_world import schema, encode, decode
    from xenova_drive_world.mapper_clock import ClockLease
    from google.protobuf.json_format import ParseDict
    from .mapper_mapping import MappingFrameDraft
    from .mapper_scene import SceneDraft
    from .mapper_hazard import HazardDraft, HazardSnapshot
    from .scene import MapperScene
    from .atlas_archive import SceneAtlasArchive
    from .mapper_atlas import MapperAtlas, MapperAtlasNotReady
    from ..mapping.atlas import AtlasResource
    import numpy as np
    try:
        uint(published_j_us)
        _need(type(draft) is MappingFrameDraft, 'actual MappingFrameDraft required')
        if scene_draft is None or hazard_draft is None:
            return None
        _need(type(scene_draft) is SceneDraft and type(hazard_draft) is HazardDraft
              and type(scene_draft.scene) is MapperScene, 'actual Scene/Hazard candidate DTOs required')
        scene = scene_draft.scene
        if not scene.eligible or scene._frozen is None:
            return None
        frozen = scene._frozen
        _need(draft.ready and scene_draft.original_mapping is draft and scene_draft.original_result is draft.result
              and scene_draft.observed is True and scene._observation is not None
              and scene._observation.stamp == draft.frame.observation.rgb.stamp
              and scene._observation.raw_pose is draft.inputs.pose
              and hazard_draft.original is draft and hazard_draft.ready and not hazard_draft.revoke_required
              and type(hazard_draft.snapshot) is HazardSnapshot and type(draft.pipeline.grid) is EvidenceGrid
              and scene.context == draft.context and draft.frame.scope.context == draft.context
              and scene.evidence_scope is not None
              and scene.evidence_scope.raw_binding == draft.frame.scope.raw_binding
              and scene.evidence_scope.calibration == draft.frame.scope.calibration
              and scene.evidence_scope.method_manifest_sha256 == draft.frame.scope.method_manifest_sha256
              and draft.original_job.fresh(published_j_us)
              and scene_draft.prepared_j_us <= published_j_us < scene_draft.expires_j_us,
              'current original mapping/Scene/hazard or deadline differs')
        _need(type(source) is dict and type(clock_lease) is ClockLease and clock_lease is draft.inputs.clock_lease
              and clock_lease is draft.frame.clock_lease and source == draft.inputs.source == scene.source == clock_lease.source
              and clock_lease.pi_host_boot == draft.context.pi_host_boot,
              'actual current source/clock originals differ')
        grant = decode(scene.grant_bytes, schema.SceneGrant); trust = decode(clock_lease.trust_bytes, schema.WorldTrust)
        _need(grant.lifecycle_state == 2 and grant.scene.policy == 1
              and grant.source_admission_revision == clock_lease.source_revision
              and grant.pi_host_boot == draft.context.pi_host_boot and grant.gateway_instance == scene.gateway_instance
              and grant.mapper_device == source['device']
              and all(getattr(grant, name) == source[name] for name in ('vehicle', 'map_epoch', 'frame_epoch', 'odom_epoch',
                  'configuration', 'calibration', 'primitive')), 'current admitted SceneGrant/source differs')
        expires = min(grant.valid_until_pi_us, clock_lease.expires_pi_us) - trust.upper_offset_us
        _need(0 <= published_j_us < expires < 1 << 63, 'original Scene/clock lease expired')
        pi_lower, pi_upper = clock_lease.j_interval_to_pi((published_j_us, published_j_us), published_j_us)
        _need(grant.issued_pi_us <= pi_lower, 'current SceneGrant was issued in the future')
        binding = json_bytes(draft.context.source_binding, 2048)
        if binding['scene_admission_ref'] is None:
            return None
        bound_ref = ParseDict(binding['scene_admission_ref'], schema.SceneAdmissionRef(), ignore_unknown_fields=False)
        scene_ref = encode(grant.scene)
        _need(encode(bound_ref) == scene_ref, 'publication differs from current full SceneAdmissionRef')
        expected_sha = grant.restore_checkpoint_sha256 if grant.purpose else grant.frozen_evidence_sha256
        _need(frozen.checkpoint_sha256 == expected_sha.hex() and frozen.hazard_revision == scene._hazard
              == hazard_draft.revision == grant.frozen_hazard_revision, 'original frozen checkpoint/hazard differs')
        summary = decode(frozen.summary, schema.SurveySummary); summary.status = 2
        _need(encode(summary) == scene._summary and summary.request_nonce == grant.request_nonce
              and summary.survey_id == grant.scene.survey_id and summary.survey_revision == grant.frozen_survey_revision
              and summary.coverage_profile_sha256 == grant.scene.coverage_profile_sha256
              and summary.coverage_profile_sha256.hex() == scene.profile.coverage_profile_sha256
              and summary.evidence_sha256.hex() == frozen.checkpoint_sha256 and not summary.failure_flags,
              'actual applied frozen summary differs')
        origin = _frozen_origin(frozen)
        if origin is None:
            return None
        job, evidence, digest = origin
        _need(all(getattr(job.context, name) == getattr(draft.context, name) for name in ('database_uuid', 'map_epoch',
            'frame_epoch', 'odom_epoch', 'pi_host_boot', 'configuration', 'calibration', 'primitive_sha256'))
            and job.context.geometry_origin == draft.context.geometry_origin, 'historical checkpoint geometry/Pi origin differs')
        refs = _metadata(frozen, job, evidence, draft.pipeline)
        if refs is None:
            return None
        blocked = _hazards(hazard_draft.snapshot, draft.pipeline.grid, draft.context, draft.frame.scope, np)
        # published time은 계산 인자로만 전달한다. 원 draft에 새 속성/시각을 기록하지 않는다.
        grid = draft.pipeline.grid; archive = frozen.archive
        if grid.rolling_window is None:
            if archive is not None:
                raise RetainedWorldError('fixed grid cannot use rolling archive')
            reader = _fixed_reader(frozen, refs, job.context.map_epoch)
            if reader is None:
                return None
            snapshot, mask = _project(draft, refs, reader, blocked, pi_upper, published_j_us, np)
        else:
            if archive is None or atlas_owner is None:
                return None
            _need(type(archive) is SceneAtlasArchive and type(atlas_owner) is MapperAtlas
                  and archive.checkpoint_job == job and archive.checkpoint_sha256 == frozen.checkpoint_sha256
                  and archive.checkpoint_digest == digest and archive.manifest_sha256 == sha256(frozen.manifest).hexdigest()
                  and archive.tile_references == refs and archive.map_epoch == job.context.map_epoch
                  and archive.lattice == grid.rolling_window.lattice, 'original atlas archive checkpoint/lattice differs')
            from .scene import RestoredSurvey
            expected_ref = frozen.complete.checkpoint_ref if type(frozen) is RestoredSurvey else None
            _need(archive.checkpoint_ref == expected_ref, 'original archive selected checkpoint ref differs')
            with atlas_owner.read_archive(archive, grid.rolling_window) as reader:
                snapshot, mask = _project(draft, refs, reader, blocked, pi_upper, published_j_us, np)
        return RetainedProjection(snapshot, mask, scene_ref, min(expires, scene_draft.expires_j_us), sha256(scene.grant_bytes).hexdigest())
    except (MapperAtlasNotReady, AtlasResource):
        return None
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as error:
        raise RetainedWorldError(str(error)) from error
