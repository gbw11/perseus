"""설정 검증: 시간 값은 유한한 양수여야 한다. 잘못된 배포 설정은 요청 시점의 500 이 아니라 시작 시점 오류다."""

import pytest

from dom0_manager.config import PLATFORM_STOP_WAIT_S, Settings


@pytest.mark.parametrize("raw", ["-1", "0", "nan", "inf"])
def test_invalid_busy_timeout_fails_at_startup(raw):
    with pytest.raises(ValueError):
        Settings.from_env({"XENOVA_DRIVER": "mock", "XENOVA_PLATFORM_BUSY_TIMEOUT": raw})


def test_timeouts_must_be_finite_positive():
    with pytest.raises(ValueError):
        Settings(driver="mock", shutdown_grace_s=0)
    assert Settings.from_env({"XENOVA_DRIVER": "mock", "XENOVA_PLATFORM_BUSY_TIMEOUT": "2.5"}).platform_busy_timeout_s == 2.5


def test_platform_mode_requires_the_fixed_platform_stop_wait():
    """플랫폼 stop 은 90초 고정이고 대기 입력이 없다. platform 모드에서 다른 값은 설정 오류다. mock·xen 은 제한하지 않는다."""
    assert Settings(driver="platform").shutdown_grace_s == PLATFORM_STOP_WAIT_S == 90
    with pytest.raises(ValueError, match="XENOVA_SHUTDOWN_GRACE must be 90"):
        Settings(driver="platform", shutdown_grace_s=30)
    with pytest.raises(ValueError):
        Settings.from_env({"XENOVA_DRIVER": "platform", "XENOVA_SHUTDOWN_GRACE": "120"})
    assert Settings.from_env({"XENOVA_DRIVER": "platform", "XENOVA_SHUTDOWN_GRACE": "90"}).shutdown_grace_s == 90
    assert Settings(driver="mock", shutdown_grace_s=30).shutdown_grace_s == 30
    # 명령 한도는 xl 서브프로세스용이라 정지 대기와 무관하게 둘 수 있다
    assert Settings(driver="platform", xl_command_timeout_s=30).xl_command_timeout_s == 30


def test_worker_settings_ignore_http_console_validation():
    """Worker 는 manager 와 같은 env 파일을 읽는다. 콘솔이 켜져 있어도 Worker 는 뜨고, HTTP 프로세스의 콘솔 검증은 그대로다."""
    env = {"XENOVA_DRIVER": "platform", "XENOVA_CONSOLE_ENABLED": "1", "XENOVA_PLATFORM_BUSY_TIMEOUT": "30"}
    with pytest.raises(ValueError, match="console requires XENOVA_SERVICE_TOKEN"):
        Settings.from_env(env)
    worker = Settings.platform_from_env(env)
    assert worker.driver == "platform" and worker.platform_busy_timeout_s == 30
    assert worker.console_enabled is False and worker.service_token == ""
    http = {"XENOVA_HOST": "0.0.0.0", "XENOVA_PORT": "not-a-port", "XENOVA_SERVICE_TOKEN": "t", "XENOVA_CONSOLE_LOCK_DIR": "/tmp/locks"}
    worker = Settings.platform_from_env({**env, **http})
    assert (worker.host, worker.port, worker.service_token, worker.console_enabled, worker.console_lock_dir) == (
        "127.0.0.1", 8081, "", False, "/run/xenova/console-locks")
    with pytest.raises(ValueError, match="console must run on local dom0"):
        Settings.from_env({**env, "XENOVA_SERVICE_TOKEN": "t", "XENOVA_XL_SSH": "root@board"})
    assert Settings.platform_from_env({**env, "XENOVA_XL_SSH": "root@board"}).xl_ssh_target == "root@board"


def test_worker_settings_keep_platform_validation():
    with pytest.raises(ValueError, match="XENOVA_SHUTDOWN_GRACE must be 90"):
        Settings.platform_from_env({"XENOVA_DRIVER": "platform", "XENOVA_CONSOLE_ENABLED": "1", "XENOVA_SHUTDOWN_GRACE": "30"})
    with pytest.raises(ValueError):
        Settings.platform_from_env({"XENOVA_DRIVER": "platform", "XENOVA_PLATFORM_BUSY_TIMEOUT": "0"})
