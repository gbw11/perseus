"""보드별 토큰의 재사용·권한·비밀 비출력 경계를 확인한다."""
import concurrent.futures
import os

import pytest

from dom0_manager import service_token


def test_token_is_private_stable_and_not_returned(tmp_path):
    path = tmp_path / "token.env"
    assert service_token.prepare(path, environ={}) == "ready"
    original = path.read_bytes()
    assert service_token._TOKEN.fullmatch(original)
    assert path.stat().st_mode & 0o777 == 0o600
    assert service_token.prepare(path, environ={}) == "existing"
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


def test_explicit_environment_token_is_preserved(tmp_path):
    path = tmp_path / "token.env"
    assert service_token.prepare(path, environ={"XENOVA_SERVICE_TOKEN": "external-token"}) == "environment"
    assert not path.exists()


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "public", "broken", "directory"])
def test_unsafe_existing_file_is_rejected_without_replacement(tmp_path, kind):
    path, other = tmp_path / "token.env", tmp_path / "other"
    other.write_bytes(b"protected sentinel")
    if kind == "symlink":
        path.symlink_to(other)
    elif kind == "hardlink":
        os.link(other, path)
    elif kind == "directory":
        path.mkdir()
    else:
        path.write_bytes(b"broken" if kind == "broken" else b"XENOVA_SERVICE_TOKEN=" + b"a" * 64 + b"\n")
        path.chmod(0o600 if kind == "broken" else 0o644)
    with pytest.raises((ValueError, OSError)):
        service_token.prepare(path, environ={})
    assert other.read_bytes() == b"protected sentinel"


def test_concurrent_starts_keep_one_complete_token(tmp_path):
    path = tmp_path / "token.env"
    # 동시 생성은 원자 게시 후 한 파일로 수렴한다.
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service_token.prepare(path, environ={}), range(2)))
    assert all(r in ("ready", "existing") for r in results)
    assert service_token._TOKEN.fullmatch(path.read_bytes())
    assert list(tmp_path.iterdir()) == [path]


def test_failure_does_not_print_secret(tmp_path, monkeypatch, capsys):
    def fail():
        raise ValueError("do-not-print-this-secret")
    monkeypatch.setattr(service_token, "prepare", fail)
    assert service_token.main() == 1
    assert "do-not-print-this-secret" not in capsys.readouterr().out
