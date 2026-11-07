"""스캔 비용 보완의 토큰 의미와 원 파일 신원 검사를 확인한다."""
import random
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import first_install_fixture  # fixed local module paths; no board operation
from test_xenova_control_request_scan import store
import xenova_execution as execution
from xenova_management import Manager


def original_text_token(value):
    """보완 전 공개 타입·C0·UTF-8 길이 판정의 differential oracle."""
    if not isinstance(value, str) or any(ord(c) < 32 for c in value):
        return False
    try:
        return 1 <= len(value.encode('utf-8')) <= 200
    except UnicodeError:
        return False


def test_token_unicode_and_utf8_boundaries_match_original():
    values = ['', '0', 'a' * 199, 'a' * 200, 'a' * 201,
              '\uac00' * 66, '\uac00' * 67, '\U0001f600' * 50, '\U0001f600' * 51,
              '\ud800', '\udfff', 'a\ud800b', '\ud800\udfff',
              'e\u0301' * 66, 'e\u0301' * 67, '\u2028', '\ufeff']
    for code in range(256):
        values.extend((chr(code), 'before' + chr(code) + 'after'))
    rng = random.Random(20261107)
    alphabet = [0, 1, 9, 10, 31, 32, 48, 65, 126, 127, 128, 159,
                0x0301, 0x2028, 0xac00, 0xd800, 0xdfff, 0xffff, 0x1f600, 0x10ffff]
    for _ in range(4096):
        values.append(''.join(chr(rng.choice(alphabet) if rng.randrange(2) else rng.randrange(0x110000))
                              for _ in range(rng.randrange(221))))
    assert all(Manager._text_token(value) == original_text_token(value) for value in values)
    assert Manager._text_token('0') is True


@pytest.mark.parametrize('value', [None, False, True, 0, 1, 1.5, b'key', bytearray(b'key'), [], {}, ()])
def test_non_string_tokens_still_reject(value):
    assert Manager._text_token(value) is original_text_token(value) is False


@pytest.mark.parametrize('name', ['.json', 'not-a-uuid.json', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa.extra.json'])
def test_json_suffix_never_substitutes_for_uuid_filename_validation(tmp_path, name):
    path, _ = store(tmp_path, 1)
    path.rename(path.with_name(name))
    with pytest.raises(execution.ExecutionError) as caught:
        execution.read_requests(tmp_path)
    assert caught.value.error['code'] == 'RECONCILE_REQUIRED'
    assert caught.value.error['message'] == 'invalid control request filename'


def test_each_scanned_uuid_still_matches_original_record_and_hash(tmp_path):
    expected = [store(tmp_path, i, f'key-{i}')[1] for i in range(1, 8)]
    before = {path: path.read_bytes() for path in (tmp_path/'control-requests').iterdir()}
    assert execution.read_requests(tmp_path) == expected
    assert all(path.read_bytes() == body for path, body in before.items())
    assert all(Path(row['operation_id'] + '.json').stem == row['operation_id'] for row in expected)


def test_builtin_leaf_and_custom_path_keep_filename_and_missing_results(tmp_path):
    for kind in ('valid', 'missing', '.json', '..json', 'unsafe\t.json', 'wrong-identity', 'broken-json'):
        home = tmp_path/('case-' + str(len(list(tmp_path.iterdir()))))
        home.mkdir()
        path, value = store(home, 1)
        name = path.name
        if kind == 'missing':
            path.unlink()
        elif kind in ('.json', '..json', 'unsafe\t.json'):
            path.rename(path.with_name(kind))
            name = kind
        elif kind == 'wrong-identity':
            value['operation_id'] = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
            path.write_text(json.dumps(value), encoding='utf8')
        elif kind == 'broken-json':
            path.write_text('{', encoding='utf8')

        class Entries:
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def __iter__(self):
                yield SimpleNamespace(name=name)

        def result(reader):
            with patch.object(execution.os, 'scandir', return_value=Entries()):
                try:
                    return ('complete', execution.read_requests(home, reader=reader))
                except execution.ExecutionError as error:
                    return ('error', error.error)

        assert result(None) == result(execution.read_record)
