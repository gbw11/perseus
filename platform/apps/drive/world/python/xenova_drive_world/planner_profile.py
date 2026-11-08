"""설치된 primitive manifest와 Pi↔VM clock 정책의 일치를 검사한다.

파일 접근·장치 조회·런타임 진단은 수행하지 않는다. 실행 중인 C 바이너리가
이 asset으로 빌드됐다는 배포 증명과 물리 clock 오차의 실측 수락은 별도다.
"""
import hashlib
import json
import re


MAX_MANIFEST_BYTES = 32768
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_POLICY_FIELDS = {'timestamp_error_us', 'drift_ppm', 'max_roundtrip_us',
                  'max_offset_width_us', 'lease_us'}
_PAIRED_FIELDS = {'timestamp_error_us': 'guest_timestamp_error_us',
                  'drift_ppm': 'guest_drift_ppm',
                  'max_offset_width_us': 'clock_error_us',
                  'lease_us': 'clock_lease_us'}


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate manifest key')
        value[key] = item
    return value


def _constant(_value):
    raise ValueError('non-finite manifest number')


def verify_manifest(raw: bytes, expected_asset_sha256: str, guest_policy: dict) -> None:
    """원 generator의 지문과 compiled clock 한계를 검산하고 불일치 시 ValueError.

    ``raw``는 loader가 소유권·경로를 확인한 최대32KiB JSON이다. 고정 asset
    SHA는 operator 설치 정책에서 오며 manifest 자신을 신뢰 근거로 쓰지 않는다.
    검사에는 JSON canonical profile·generator 지문·clock 다섯 한계만 사용한다.
    primitive C 파일이나 설치된 실행 바이너리의 bytes는 검사하지 않는다.
    """
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_MANIFEST_BYTES:
        raise ValueError('bounded manifest bytes required')
    if type(expected_asset_sha256) is not str or not _SHA.fullmatch(expected_asset_sha256):
        raise ValueError('pinned asset SHA256 required')
    if type(guest_policy) is not dict or set(guest_policy) != _POLICY_FIELDS:
        raise ValueError('exact paired guest clock policy')
    limits = {'timestamp_error_us': 999999, 'drift_ppm': 999999,
              'max_roundtrip_us': 1000000, 'max_offset_width_us': 2**31-1,
              'lease_us': 1000000}
    if any(type(guest_policy[key]) is not int or not 0 < guest_policy[key] <= limit
           for key, limit in limits.items()):
        raise ValueError('bounded positive guest clock policy')
    try:
        manifest = json.loads(raw.decode('utf-8'), object_pairs_hook=_object,
                              parse_constant=_constant)
        if type(manifest) is not dict or type(manifest.get('profile')) is not dict:
            raise ValueError('manifest profile object required')
        for key in ('profile_sha256', 'asset_sha256', 'generator_sha256'):
            if type(manifest.get(key)) is not str or not _SHA.fullmatch(manifest[key]):
                raise ValueError('manifest SHA256 required')
        profile = manifest['profile']
        canonical = json.dumps(profile, sort_keys=True, separators=(',', ':'),
                               allow_nan=False).encode('utf-8')
    except (UnicodeError, TypeError, OverflowError, RecursionError) as error:
        raise ValueError('invalid bounded manifest JSON') from error
    profile_sha = hashlib.sha256(canonical).hexdigest()
    asset_sha = hashlib.sha256(canonical + b'\0' + manifest['generator_sha256'].encode('ascii')).hexdigest()
    if profile_sha != manifest['profile_sha256'] or asset_sha != manifest['asset_sha256']:
        raise ValueError('manifest generator fingerprint mismatch')
    if asset_sha != expected_asset_sha256:
        raise ValueError('installed planner asset pin mismatch')
    for policy_key, profile_key in _PAIRED_FIELDS.items():
        if type(profile.get(profile_key)) is not int or profile[profile_key] != guest_policy[policy_key]:
            raise ValueError('compiled and Pi guest clock policy mismatch: ' + policy_key)
    # lattice_input.c의 actual receipt branch가 쓰는 현재 독립 RTT 상한이다.
    if guest_policy['max_roundtrip_us'] != 1000000:
        raise ValueError('compiled guest clock roundtrip mismatch')
