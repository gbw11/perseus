"""상주 release와 실제 코드 기반의 불변 manifest를 만든다. SDK/장치를 열지 않는다.

파일 hash 작업은 S-log 및 child local setup에서 수행한다. parser/graph/format
참조는 아래 canonical manifest 원 bytes의 SHA이며 코드 파일 SHA·진단 label과 다르다.
graph manifest는 host 요청/구현 선언이며 device 설정 readback은 아니다.
"""

import hashlib
import json
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def canonical(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'), allow_nan=False) + '\n').encode('ascii')


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _sources(names):
    return {name: _sha((PACKAGE_ROOT / name).read_bytes()) for name in names}


def release_sha256():
    """현재 배포 package의 실제 Python source 집합. README나 bytecode는 포함하지 않는다."""
    names = sorted(path.relative_to(PACKAGE_ROOT).as_posix() for path in PACKAGE_ROOT.rglob('*.py'))
    return _sha(canonical(dict(version=1, sources=_sources(names))))


def parser_manifest():
    from ..perception.yolop import PARSER_ID, YolopPostprocessConfig

    return canonical(dict(version=1, parser_id=PARSER_ID, scope='OWNED_P0_LIVE_CANDIDATES_NOT_METRIC_OR_AUTHORITY',
        configuration=YolopPostprocessConfig().to_dict(),
        sources=_sources(('perception/yolop.py', 'perception/decoders.py', 'oak/yolop_frames.py',
                          'runtime/resident_samples.py'))))


def parser_manifest_sha256():
    return _sha(parser_manifest())


def runtime_artifacts(profile, perception_profile_bytes=None,mapper_profile_bytes=None,*,jpeg_enabled=False):
    """같은 명시 profile과 local source에서 canonical 참조 및 출판할 원 manifest를 돌려준다.

    model_id는 승인 blob의 기대 SHA다. 실제 모델 bytes와 SDK Blob 검사는 A가
    기존 asset validator로 수행한다. calibration_bundle=None은 metric 미준비다.
    """
    from ..oak.yolop_asset import BLOB_SHA256
    from .raw_slot import HEADER_BYTES, METADATA_BYTES, PROFILE, SLOT_BYTES, MAX_NN_BYTES

    if type(jpeg_enabled) is not bool:
        raise ValueError('runtime JPEG graph selection must be an exact bool')
    parser = parser_manifest()
    graph_profile = profile.to_dict()
    if jpeg_enabled:
        graph_profile['jpeg_enabled'] = True
    graph = canonical(dict(version=1, scope='HOST_GRAPH_REQUEST_NOT_DEVICE_READBACK', profile=graph_profile,
        implementation=_sources(('oak/device.py', 'oak/yolop_device.py', 'oak/yolop_asset.py'))))
    format_fields = dict(version=1, profile_id=PROFILE, slot_bytes=SLOT_BYTES,
        header_bytes=HEADER_BYTES, metadata_bytes=METADATA_BYTES, max_nn_bytes=MAX_NN_BYTES,
        sources=_sources(('runtime/raw_slot.py', 'runtime/resident_samples.py')))
    if jpeg_enabled:
        format_fields['jpeg'] = dict(profile='encoded-jpeg-1', header_bytes=1024, body_bytes=256*1024,
            capture_age_us=500_000, transfer_us=250_000,
            sources=_sources(('oak/jpeg.py', 'runtime/jpeg_stream.py', 'runtime/resident_video.py')))
    formats = canonical(format_fields)
    calibration_bundle=None
    manifests={'parser-manifest.json': parser, 'graph-profile-manifest.json': graph,
               'format-table-manifest.json': formats}
    if perception_profile_bytes is not None:
        from ..perception.calibration import CalibrationProfile,canonical as calibration_canonical
        if type(perception_profile_bytes) is not bytes or not 1<=len(perception_profile_bytes)<=65536:
            raise ValueError('pinned perception calibration exceeds its bound')
        calibration=CalibrationProfile.from_dict(json.loads(perception_profile_bytes))
        if calibration.kind=='SYNTHETIC':
            raise ValueError('live runtime cannot use a synthetic calibration profile')
        raw=calibration_canonical(calibration.to_dict())
        if len(raw)>65536:
            raise ValueError('canonical perception calibration exceeds its bound')
        manifests['perception-calibration.json']=raw
        calibration_bundle=calibration.sha256
    mapper_sha=None
    if mapper_profile_bytes is not None:
        from .mapper_profile import MapperProfile
        if perception_profile_bytes is None:raise ValueError('mapper configuration requires pinned calibration bytes')
        mapper=MapperProfile.from_bytes(mapper_profile_bytes)
        mapper.check(calibration,vehicle_configuration=mapper.vehicle_configuration,
                     vehicle_calibration=calibration.vehicle_bundle_sha256)
        manifests['mapper-profile.json']=mapper.canonical_bytes
        mapper_sha=mapper.sha256
    return dict(release_sha256=release_sha256(), model_id=BLOB_SHA256, parser_id=_sha(parser),
                graph_profile_sha256=_sha(graph), format_table_sha256=_sha(formats), calibration_bundle=calibration_bundle,
                manifests=manifests,mapper_profile_sha256=mapper_sha)


def publish_runtime_artifacts(directory, artifacts):
    """새 진단 directory의 원본을 배타 출판한다. 기존 파일을 덮지 않는다. S-log 전용이다."""
    root = Path(directory)
    root.mkdir(mode=0o700)
    for name, raw in artifacts['manifests'].items():
        with (root / name).open('xb') as output:
            output.write(raw)
    return {name: dict(bytes=len(raw), sha256=_sha(raw)) for name, raw in artifacts['manifests'].items()}
