"""드라이버 선택. XENOVA_DRIVER=mock|xen|platform (S15P31E110-31).

- mock: 인메모리. 보드·Xen 없이 개발
- xen: xl 직접 조회(읽기 전용). 명령은 501
- platform: Xenova 플랫폼 관리 라이브러리(xenova_management.Manager). registry + Xen 생명주기 (S15P31E110-115)
"""

from __future__ import annotations

from ..config import Settings
from .base import Driver, Provisioner
from .mock import MockDriver, MockProvisioner
from .xen import UnavailableProvisioner, XenDriver
from .xentop import Xentop
from .xl import CommandRunner, SshRunner, SubprocessRunner, Xl


def _runner(settings: Settings) -> CommandRunner:
    return SshRunner(settings.xl_ssh_target) if settings.xl_ssh_target else SubprocessRunner()


def _xl(settings: Settings, runner: CommandRunner | None = None) -> Xl:
    return Xl(
        runner or _runner(settings),
        query_timeout_s=settings.xl_query_timeout_s,
        command_timeout_s=settings.xl_command_timeout_s,
    )


def _xentop(settings: Settings, runner: CommandRunner | None = None) -> Xentop:
    # xentop -b -i 1 은 즉시 끝난다. 조회 한도(5초)를 그대로 쓴다
    return Xentop(runner or _runner(settings), timeout_s=settings.xl_query_timeout_s)


def build_driver(settings: Settings) -> Driver:
    if settings.driver == "mock":
        driver = MockDriver(host_cpus=settings.host_cpus)
        if settings.mock_samples:
            driver.seed_samples()
        return driver
    runner = _runner(settings)
    if settings.driver == "xen":
        return XenDriver(_xl(settings, runner), xentop=_xentop(settings, runner))
    from .platform import PlatformDriver, load_manager, load_records_loader

    manager = load_manager(settings.platform_lib, busy_timeout_s=settings.platform_busy_timeout_s, read_fork=settings.platform_read_fork)
    return PlatformDriver(
        manager,
        _xl(settings, runner),
        xentop=_xentop(settings, runner),
        records_loader=load_records_loader(settings.platform_lib, manager),
    )


def build_provisioner(settings: Settings, driver: Driver | None = None) -> Provisioner:
    if settings.driver == "mock":
        return MockProvisioner()
    if settings.driver == "xen":
        return UnavailableProvisioner(settings.instance_root)
    from .platform import PlatformDriver, PlatformProvisioner, load_manager

    manager = (
        driver.manager
        if isinstance(driver, PlatformDriver)
        else load_manager(settings.platform_lib, busy_timeout_s=settings.platform_busy_timeout_s, read_fork=settings.platform_read_fork)
    )
    return PlatformProvisioner(manager)


__all__ = [
    "Driver",
    "Provisioner",
    "MockDriver",
    "MockProvisioner",
    "XenDriver",
    "UnavailableProvisioner",
    "build_driver",
    "build_provisioner",
]
