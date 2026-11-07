"""명시 승인한 OAK 한 대의 수집 벤치 설정. 운영 주행 profile은 아니다."""

from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import re


PROFILE_ID = "oak-rgb-depth-bench-v1"
_GIB = 1024**3


@dataclass(frozen=True)
class CaptureProfile:
    """카메라 선택·유한 자원과 기한. 임계값은 실기 수락 전 벤치 초깃값이다."""

    device_mxid: str
    fps: int = 15
    max_bytes: int = _GIB
    min_free_bytes: int = 8 * _GIB
    startup_timeout_s: float = 90
    stall_timeout_s: float = 0.5
    close_timeout_s: float = 15
    sync_tolerance_us: int = 10_000

    def __post_init__(self) -> None:
        if (type(self.device_mxid) is not str
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", self.device_mxid)
                or self.device_mxid.startswith("REPLACE_")):
            raise ValueError("device_mxid must explicitly identify the approved OAK; replace the example value")
        if type(self.fps) is not int or self.fps != 15:
            raise ValueError("this bench graph requires fps=15")
        for name, lower, upper in (("max_bytes", 1024**2, 4 * _GIB),
                                   ("min_free_bytes", 8 * _GIB, 1024 * _GIB),
                                   ("sync_tolerance_us", 0, 10_000)):
            value = getattr(self, name)
            if type(value) is not int or not lower <= value <= upper:
                raise ValueError(f"{name} must be an integer in {lower}..{upper}")
        for name, lower, upper in (("startup_timeout_s", 0.1, 90),
                                   ("stall_timeout_s", 0.1, 0.5),
                                   ("close_timeout_s", 0.1, 15)):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or not lower <= value <= upper:
                raise ValueError(f"{name} must be finite and in {lower}..{upper}")

    def to_dict(self) -> dict:
        return {"schema_version": 1, "profile_id": PROFILE_ID,
                "nn_requirement": "ABSENT", "jpeg_enabled": False, **asdict(self)}


def _unique(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate profile key: {key}")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise ValueError(f"nonfinite JSON constant: {value}")


def load_profile(path: Path) -> CaptureProfile:
    """16KiB 이하 version1 JSON만 읽는다. 알 수 없는 필드·암묵 장치 선택을 거절한다."""
    with Path(path).open("rb") as source:
        raw = source.read(16_385)
    if len(raw) > 16_384:
        raise ValueError("capture profile exceeds 16KiB")
    try:
        obj = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("invalid capture profile JSON") from exc
    allowed = set(CaptureProfile.__dataclass_fields__) | {
        "schema_version", "profile_id", "nn_requirement", "jpeg_enabled"}
    if not isinstance(obj, dict) or set(obj) - allowed:
        raise ValueError("capture profile must be an object with only documented fields")
    if type(obj.get("schema_version")) is not int or obj["schema_version"] != 1:
        raise ValueError("capture profile requires schema_version=1")
    if obj.get("profile_id") != PROFILE_ID or obj.get("nn_requirement") != "ABSENT":
        raise ValueError("capture requires the explicit no-NN bench profile")
    if obj.get("jpeg_enabled") is not False:
        raise ValueError("this capture graph requires jpeg_enabled=false")
    values = {name: obj[name] for name in CaptureProfile.__dataclass_fields__ if name in obj}
    if "device_mxid" not in values:
        raise ValueError("device_mxid is required; no first-device fallback")
    return CaptureProfile(**values)
