"""설정 검증: 시간 값은 유한한 양수여야 한다. 잘못된 배포 설정은 요청 시점의 500 이 아니라 시작 시점 오류다."""

import pytest

from dom0_manager.config import Settings


@pytest.mark.parametrize("raw", ["-1", "0", "nan", "inf"])
def test_invalid_busy_timeout_fails_at_startup(raw):
    with pytest.raises(ValueError):
        Settings.from_env({"XENOVA_DRIVER": "mock", "XENOVA_PLATFORM_BUSY_TIMEOUT": raw})


def test_timeouts_must_be_finite_positive():
    with pytest.raises(ValueError):
        Settings(driver="mock", shutdown_grace_s=0)
    assert Settings.from_env({"XENOVA_DRIVER": "mock", "XENOVA_PLATFORM_BUSY_TIMEOUT": "2.5"}).platform_busy_timeout_s == 2.5
