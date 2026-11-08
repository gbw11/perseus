"""실제 생성 manifest와 Pi 설치 clock 정책이 서로 다른 배포를 거절한다."""
import hashlib
import json
from pathlib import Path

import pytest

from xenova_drive_world.planner_profile import MAX_MANIFEST_BYTES, verify_manifest


MANIFEST = Path(__file__).resolve().parents[1] / 'planner/generated/primitive-manifest.json'


def installed_pair():
    raw = MANIFEST.read_bytes()
    manifest = json.loads(raw)
    profile = manifest['profile']
    policy = dict(timestamp_error_us=profile['guest_timestamp_error_us'],
                  drift_ppm=profile['guest_drift_ppm'], max_roundtrip_us=1000000,
                  max_offset_width_us=profile['clock_error_us'], lease_us=profile['clock_lease_us'])
    return raw, manifest['asset_sha256'], policy


def test_actual_generated_asset_and_reformatted_same_manifest_share_the_installed_pair():
    raw, pin, policy = installed_pair()
    assert verify_manifest(raw, pin, policy) is None
    reformatted = json.dumps(json.loads(raw), sort_keys=True, separators=(',', ':')).encode()
    assert verify_manifest(reformatted, pin, policy) is None


@pytest.mark.parametrize('key', ['timestamp_error_us', 'drift_ppm', 'max_roundtrip_us',
                               'max_offset_width_us', 'lease_us'])
def test_pi_policy_cannot_silently_differ_from_the_compiled_profile(key):
    raw, pin, policy = installed_pair()
    policy[key] -= 1
    with pytest.raises(ValueError, match='mismatch'):
        verify_manifest(raw, pin, policy)


def test_manifest_cannot_supply_its_own_trusted_asset_pin():
    raw, _, policy = installed_pair()
    with pytest.raises(ValueError, match='pin mismatch'):
        verify_manifest(raw, '0' * 64, policy)


def test_profile_edit_needs_both_a_regenerated_asset_and_a_new_external_install_pin():
    raw, pin, policy = installed_pair()
    manifest = json.loads(raw)
    manifest['profile']['guest_drift_ppm'] += 1
    with pytest.raises(ValueError, match='fingerprint mismatch'):
        verify_manifest(json.dumps(manifest).encode(), pin, policy)
    canonical = json.dumps(manifest['profile'], sort_keys=True, separators=(',', ':')).encode()
    manifest['profile_sha256'] = hashlib.sha256(canonical).hexdigest()
    manifest['asset_sha256'] = hashlib.sha256(canonical + b'\0' + manifest['generator_sha256'].encode()).hexdigest()
    policy['drift_ppm'] += 1
    with pytest.raises(ValueError, match='pin mismatch'):
        verify_manifest(json.dumps(manifest).encode(), pin, policy)


@pytest.mark.parametrize('raw', [b'null', b'[]', b'{}', b'\xff', b' ' * (MAX_MANIFEST_BYTES + 1),
                               b'{"profile":{"x":NaN}}'])
def test_invalid_or_unbounded_installed_manifest_is_not_a_policy(raw):
    _, pin, policy = installed_pair()
    with pytest.raises(ValueError):
        verify_manifest(raw, pin, policy)


def test_duplicate_json_keys_are_rejected_before_hashing_the_merged_object():
    raw, pin, policy = installed_pair()
    value = json.dumps(json.loads(raw)).encode()
    marker = b'"guest_drift_ppm": ' + str(policy['drift_ppm']).encode()
    assert value.count(marker) == 1
    duplicate = value.replace(marker, marker + b', ' + marker)
    with pytest.raises(ValueError, match='duplicate'):
        verify_manifest(duplicate, pin, policy)


def test_boolean_and_extra_policy_fields_do_not_become_numeric_clock_bounds():
    raw, pin, policy = installed_pair()
    for invalid in ({**policy, 'drift_ppm': True}, {**policy, 'assumed_valid': True}):
        with pytest.raises(ValueError):
            verify_manifest(raw, pin, invalid)
