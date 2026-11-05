import json
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

import pytest

from dom0_manager.observations import ObservationFile
from dom0_manager.errors import PlatformUnavailable
from xenova_display.shared import SharedFollower

BOOT = str(uuid4())


def publish(path, now, **changes):
    data = dict(schema_version=1, host_boot_id=BOOT, producer_epoch=str(uuid4()),
                status='ok', observed_mono=now, domains=[])
    data.update(changes)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data))
    temporary.chmod(0o600)
    temporary.replace(path)


def test_unchanged_file_is_not_reparsed_or_given_a_new_observation_time(tmp_path):
    path = tmp_path/'state.json';now = [100.0]
    publish(path, 99)
    reader = ObservationFile(path, clock=lambda: now[0], boot_id=BOOT,
                             owner_uid=path.stat().st_uid)
    follower = SharedFollower(reader)
    with patch.object(reader, 'read', wraps=reader.read) as read:
        assert follower.read_changed() == ([], 99)
        for _ in range(20):
            assert follower.read_changed() is None
        assert read.call_count == 1
        now[0] = 103.0
        assert follower.read_changed() is None
        with pytest.raises(PlatformUnavailable):
            reader.read()  # The old file never became a fresh source.


def test_new_frame_invalid_permissions_failure_and_recovery(tmp_path):
    path = tmp_path/'state.json'
    publish(path, 100)
    reader = ObservationFile(path, clock=lambda: 101, boot_id=BOOT,
                             owner_uid=path.stat().st_uid)
    follower = SharedFollower(reader)
    assert follower.read_changed() == ([], 100)
    path.chmod(0o644)
    with pytest.raises(PlatformUnavailable):
        follower.read_changed()
    follower.invalidate()
    path.chmod(0o600)
    assert follower.read_changed() == ([], 100)
    publish(path, 100.5)
    assert follower.read_changed() == ([], 100.5)
    path.unlink()
    with pytest.raises(FileNotFoundError):
        follower.read_changed()
    follower.invalidate()
    publish(path, 100.75)
    assert follower.read_changed() == ([], 100.75)


def test_same_clock_and_size_atomic_replacement_is_still_observed(tmp_path):
    path = tmp_path/'state.json';publish(path, 100)
    reader = ObservationFile(path, clock=lambda: 101, boot_id=BOOT,
                             owner_uid=path.stat().st_uid)
    follower = SharedFollower(reader)
    assert follower.read_changed() == ([], 100)
    publish(path, 100)
    assert follower.read_changed() == ([], 100)


def test_invalid_replacement_never_reuses_old_success(tmp_path):
    path = tmp_path/'state.json';publish(path, 100)
    reader = ObservationFile(path, clock=lambda: 101, boot_id=BOOT,
                             owner_uid=path.stat().st_uid)
    follower = SharedFollower(reader)
    assert follower.read_changed() == ([], 100)
    publish(path, 100, status='unavailable')
    with pytest.raises(PlatformUnavailable):
        follower.read_changed()
    follower.invalidate()
    publish(path, 100.5)
    assert follower.read_changed() == ([], 100.5)
