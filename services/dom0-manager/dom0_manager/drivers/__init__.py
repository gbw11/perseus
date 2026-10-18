"""드라이버 선택. PERSEUS_DRIVER=mock|xen (S15P31E110-31)."""

from __future__ import annotations

from ..config import Settings
from .base import Driver, Provisioner
from .mock import MockDriver, MockProvisioner
from .xen import UnavailableProvisioner, XenDriver
from .xl import SubprocessRunner, Xl


def build_driver(settings: Settings) -> Driver:
    if settings.driver == "mock":
        driver = MockDriver(host_cpus=settings.host_cpus)
        if settings.mock_samples:
            driver.seed_samples()
        return driver
    xl = Xl(
        SubprocessRunner(),
        query_timeout_s=settings.xl_query_timeout_s,
        command_timeout_s=settings.xl_command_timeout_s,
    )
    return XenDriver(xl)


def build_provisioner(settings: Settings) -> Provisioner:
    if settings.driver == "mock":
        return MockProvisioner()
    return UnavailableProvisioner(settings.instance_root)


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
