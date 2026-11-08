"""명시 설치 policy/compiled manifest pin/opt-in 진입점의 한 연결."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import pytest

from xenova_gateway.operator_profile import OperatorProfile
from xenova_gateway.config import parse
from test_verified import A,B

pytestmark=pytest.mark.skipif(not hasattr(os,'O_NOFOLLOW'),reason='POSIX installed policy')


def test_opt_in_loader_pins_compiled_clock_and_separate_endpoints(tmp_path):
    original=Path(__file__).resolve().parents[4]/'platform/apps/drive/planner/generated/primitive-manifest.json'
    manifest=tmp_path/'primitive-manifest.json';manifest.write_bytes(original.read_bytes());manifest.chmod(0o644)
    body=json.loads(manifest.read_text());profile=body['profile']
    value=dict(manual=dict(forward_mps=.1,reverse_mps=.08,steer_rad=.3),guest_clock=dict(
        timestamp_error_us=profile['guest_timestamp_error_us'],drift_ppm=profile['guest_drift_ppm'],max_roundtrip_us=1000000,
        max_offset_width_us=profile['clock_error_us'],lease_us=profile['clock_lease_us']),keyboard_profile_directory=str(tmp_path),
        planner_manifest=str(manifest),planner_asset_sha256=body['asset_sha256'])
    path=tmp_path/'operator.json';path.write_text(json.dumps(value));path.chmod(0o600)
    loaded=OperatorProfile.load(path,uid=os.geteuid());assert loaded.planner_asset_sha256==body['asset_sha256']
    settings=parse(['--profile','warm','--vm-a',A,'--vm-b',B,'--sink','serial','--vehicle-limits','/cfg/limits.json',
        '--serial-profile','feedback-3','--feedback3-profile','/cfg/feedback.json','--feedback-timeout-ms','500',
        '--odometry-config','/cfg/odom.json','--operator-profile',str(path)],env={})
    assert settings.drive_link and settings.operator_socket!=settings.ipc_socket and settings.keyboard_socket!=settings.operator_socket
    value['guest_clock']['drift_ppm']+=1;path.write_text(json.dumps(value));path.chmod(0o600)
    with pytest.raises(ValueError,match='mismatch'):OperatorProfile.load(path,uid=os.geteuid())
