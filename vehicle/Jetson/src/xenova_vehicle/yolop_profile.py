"""YOLOP+RGB/native-depth 벤치 전용 설정. no-NN capture v1과 별개다."""

from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path

from .capture_profile import CaptureProfile, _constant, _unique


PROFILE_ID = "oak-yolop-rgb-depth-bench-v1"


@dataclass(frozen=True)
class YolopBenchProfile:
    """NN 기한은 벤치 초깃값이다. 실제 추론률이나 운영 준비의 수락값이 아니다."""

    device_mxid: str
    asset_dir: Path
    asset_manifest: Path
    fps: int = 15
    max_bytes: int = 64 * 1024**2
    min_free_bytes: int = 8 * 1024**3
    startup_timeout_s: float = 90
    stall_timeout_s: float = 0.5
    close_timeout_s: float = 15
    sync_tolerance_us: int = 10_000
    nn_first_timeout_s: float = 15
    nn_stall_timeout_s: float = 5
    diagnostic_samples: int = 3

    def __post_init__(self):
        CaptureProfile(**{name: getattr(self, name) for name in CaptureProfile.__dataclass_fields__})
        for name in ("asset_dir", "asset_manifest"):
            path = Path(getattr(self, name)).expanduser().absolute()
            object.__setattr__(self, name, path)
        for name, lower, upper in (("nn_first_timeout_s", 0.1, 15), ("nn_stall_timeout_s", 0.1, 15)):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or not lower <= value <= upper:
                raise ValueError(f"{name} must be finite and in {lower}..{upper}")
        if type(self.diagnostic_samples) is not int or not 1 <= self.diagnostic_samples <= 8:
            raise ValueError("diagnostic_samples must be in 1..8")

    def to_dict(self):
        result = asdict(self)
        result.update({name: str(getattr(self, name)) for name in ("asset_dir", "asset_manifest")})
        return {"schema_version": 1, "profile_id": PROFILE_ID, "nn_requirement": "REQUIRED",
                "jpeg_enabled": False, **result}


def load_yolop_profile(path: Path) -> YolopBenchProfile:
    """명시적 파일 입력만 허용한다. 상대 자산 경로는 profile 파일 기준이다."""
    path = Path(path).expanduser().absolute()
    with path.open("rb") as source:
        raw = source.read(16_385)
    if len(raw) > 16_384:
        raise ValueError("YOLOP bench profile exceeds 16KiB")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("invalid YOLOP bench profile JSON") from exc
    fields = set(YolopBenchProfile.__dataclass_fields__)
    if not isinstance(value, dict) or set(value) - fields - {
            "schema_version", "profile_id", "nn_requirement", "jpeg_enabled"}:
        raise ValueError("unknown YOLOP bench profile fields")
    if (type(value.get("schema_version")) is not int or value["schema_version"] != 1
            or value.get("profile_id") != PROFILE_ID or value.get("nn_requirement") != "REQUIRED"
            or value.get("jpeg_enabled") is not False):
        raise ValueError("explicit no-JPEG REQUIRED YOLOP bench profile required")
    if any(name not in value for name in ("device_mxid", "asset_dir", "asset_manifest")):
        raise ValueError("device_mxid, asset_dir and asset_manifest are required")
    values = {name: value[name] for name in fields if name in value}
    for name in ("asset_dir", "asset_manifest"):
        if not isinstance(values[name], str) or not values[name]:
            raise ValueError(f"{name} must be an explicit path")
        values[name] = path.parent / Path(values[name]).expanduser()
    return YolopBenchProfile(**values)
