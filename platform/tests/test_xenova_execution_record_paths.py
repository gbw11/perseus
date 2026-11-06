"""Execution JSON reads still validate the entire path on every call."""
import json
import os

import pytest

from xenova_execution import ExecutionError, read_record
from xenova_runtime import PlatformError


@pytest.fixture
def record_path(tmp_path):
    parent = tmp_path / 'runtime' / 'observations'
    parent.mkdir(parents=True)
    path = parent / 'vm.json'
    path.write_text(json.dumps({'schema_version': '1.0', 'generation': 1}))
    return path


def test_atomic_replacement_is_read_again_without_cached_contents(record_path):
    assert read_record(record_path)['generation'] == 1
    replacement = record_path.with_name('replacement.json')
    replacement.write_text(json.dumps({'schema_version': '1.0', 'generation': 2}))
    os.replace(replacement, record_path)
    assert read_record(record_path)['generation'] == 2


@pytest.mark.parametrize('level', ['file', 'parent', 'ancestor'])
def test_symlink_at_any_level_is_rejected(record_path, level):
    original = {'file': record_path, 'parent': record_path.parent,
                'ancestor': record_path.parent.parent}[level]
    target = original.with_name(original.name + '-real')
    original.rename(target)
    original.symlink_to(target, target_is_directory=level != 'file')
    with pytest.raises(PlatformError):
        read_record(record_path)


def test_parent_replacement_after_success_is_not_trusted(record_path):
    assert read_record(record_path)['generation'] == 1
    parent = record_path.parent
    target = parent.with_name('replaced-parent')
    parent.rename(target)
    parent.symlink_to(target, target_is_directory=True)
    with pytest.raises(PlatformError):
        read_record(record_path)


def test_hard_link_is_rejected(record_path):
    os.link(record_path, record_path.with_name('second-link.json'))
    with pytest.raises(PlatformError):
        read_record(record_path)


def test_noncanonical_parent_traversal_is_rejected(record_path):
    child = record_path.parent / 'child'
    child.mkdir()
    with pytest.raises(PlatformError):
        read_record(child / '..' / record_path.name)


def test_relative_path_is_rejected(record_path, monkeypatch):
    monkeypatch.chdir(record_path.parent)
    with pytest.raises(PlatformError):
        read_record(type(record_path)(record_path.name))


@pytest.mark.parametrize('contents', ['{broken', '[]', '{"schema_version":"2.0"}'])
def test_corruption_is_not_missing_or_previous_success(record_path, contents):
    assert read_record(record_path)['generation'] == 1
    record_path.write_text(contents)
    with pytest.raises(ExecutionError) as caught:
        read_record(record_path)
    assert caught.value.error['code'] == 'RECONCILE_REQUIRED'


def test_missing_and_dangling_link_remain_distinct(record_path):
    record_path.unlink()
    assert read_record(record_path) is None
    record_path.symlink_to(record_path.with_name('missing.json'))
    with pytest.raises(PlatformError):
        read_record(record_path)


def test_directory_is_not_a_record(record_path):
    record_path.unlink()
    record_path.mkdir()
    with pytest.raises(PlatformError):
        read_record(record_path)
