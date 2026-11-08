"""선택형 J MQTT 설치 profile이다. 자격/비밀 기본값이나 broker 연결을 만들지 않는다."""

from dataclasses import dataclass, field
import ipaddress
import os
import stat

from ..runtime.source_identity import SourcePolicy


@dataclass(frozen=True)
class JetsonJioProfile:
    source_policy: SourcePolicy
    clock_policy: object
    device: str
    host: str
    port: int
    username: str | None = None
    password: str | None = field(default=None, repr=False)
    tof_profiles: tuple = ()
    tof_max_age_us: int = 500_000

    def __post_init__(self):
        from xenova_drive_world.mapper_clock import ClockPolicy, ascii_id, integer
        if type(self.source_policy) is not SourcePolicy or type(self.clock_policy) is not ClockPolicy:
            raise ValueError('exact pinned source/clock policies required')
        p, c = self.source_policy, self.clock_policy
        if (p.vehicle, p.configuration, p.calibration, p.primitive) != (
                c.vehicle, c.configuration, c.calibration, c.primitive) or self.device != c.device:
            raise ValueError('J source/clock/device policies differ')
        if type(self.host) is not str or str(ipaddress.IPv4Address(self.host)) != self.host:
            raise ValueError('numeric IPv4 broker address required for bounded connect')
        integer(self.port, 1, 65535)
        if self.username is not None:
            ascii_id(self.username, 128)
        if self.password is not None and (type(self.password) is not str or len(self.password) > 1024):
            raise ValueError('bounded private credential required')
        integer(self.tof_max_age_us, 1, 1_000_000)
        if type(self.tof_profiles) is not tuple or len(self.tof_profiles) > 2:
            raise ValueError('at most two explicitly installed ToF profiles required')
        seen = set()
        for row in self.tof_profiles:
            if (type(row) is not tuple or len(row) != 3 or type(row[0]) is not int
                    or row[0] not in (0, 1) or row[0] in seen):
                raise ValueError('unique sensor/leaf/revision tuple required')
            seen.add(row[0])
            if type(row[1]) is not str or len(row[1]) != 64 or any(x not in '0123456789abcdef' for x in row[1]):
                raise ValueError('pinned lowercase ToF leaf SHA required')
            integer(row[2], 1, (1 << 32) - 1)
        if self.tof_profiles and c.lookback_us < self.tof_max_age_us:
            raise ValueError('clock lookback does not cover the configured ToF age')

    @classmethod
    def from_dict(cls, value):
        """private JSON exact keys를 받는다. profile 파싱은 source/실장비 승인이 아니다."""
        from xenova_drive_world.mapper_clock import ClockPolicy
        keys = {'version', 'source_policy', 'clock_policy', 'device', 'host', 'port', 'username',
                'password', 'tof_profiles', 'tof_max_age_us'}
        if type(value) is not dict or set(value) != keys or type(value['version']) is not int or value['version'] != 1:
            raise ValueError('exact JIO profile version/fields required')
        if (type(value['source_policy']) is not dict or set(value['source_policy']) != set(SourcePolicy.__dataclass_fields__)
                or type(value['clock_policy']) is not dict or set(value['clock_policy']) != set(ClockPolicy.__dataclass_fields__)
                or type(value['tof_profiles']) is not list or any(type(row) is not list for row in value['tof_profiles'])):
            raise ValueError('exact source/clock/ToF profile fields required')
        return cls(SourcePolicy(**value['source_policy']), ClockPolicy(**value['clock_policy']), value['device'],
            value['host'], value['port'], value['username'], value['password'],
            tuple(tuple(row) for row in value['tof_profiles']), value['tof_max_age_us'])

    @classmethod
    def load(cls, path, *, owner_uid=None):
        """선택한 private 일반 파일만 읽는다. 실제 credentials는 로그/사본으로 내보내지 않는다."""
        from xenova_drive_world.mapper_clock import strict_json
        if not hasattr(os, 'O_NOFOLLOW'):
            raise ValueError('private profile loading requires a no-follow file open')
        expected_uid = os.getuid() if owner_uid is None else owner_uid
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != expected_uid or info.st_nlink != 1
                    or info.st_mode & 0o077 or not 0 < info.st_size <= 8192):
                raise ValueError('private owner-only bounded profile file required')
            raw = stream.read(8193)
        return cls.from_dict(strict_json(raw, 8192))
