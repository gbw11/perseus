from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

DISPLAY_DRIVE_TOPIC = "xenova/v1/display/drive-state"
DISPLAY_DRIVE_TYPE = "display.drive_state"


class Binding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    vm_id: UUID
    role: str
    expected_os: str
    app_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    agent_supported: bool = False

    @field_validator("vm_id")
    @classmethod
    def non_dom0(cls, value):
        if value.int == 0:
            raise ValueError("dom0 cannot be a display guest")
        return value

    @field_validator("role")
    @classmethod
    def role_value(cls, value):
        if value not in {"primary_drive", "backup_drive", "interaction"}:
            raise ValueError("unknown VM role")
        return value

    @field_validator("expected_os")
    @classmethod
    def os_value(cls, value):
        if value not in {"zephyr", "debian"}:
            raise ValueError("unsupported expected OS")
        return value


class BindingProfile(BaseModel):
    """Explicit candidate/deployment provenance; not proof of a real registry match."""
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1]
    scope: Literal["candidate", "deployment"]
    bindings: list[Binding] = Field(min_length=3, max_length=3)

    @field_validator("schema_version", mode="before")
    @classmethod
    def schema_integer(cls, value):
        if type(value) is not int:
            raise ValueError("schema_version must be integer 1")
        return value


def load_bindings(path, *, allow_candidate=False):
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, list):
        # Existing configurations remain supported, but are not called registry-verified.
        return tuple(Binding.model_validate(row) for row in raw), "legacy"
    profile = BindingProfile.model_validate(raw)
    if profile.scope == "candidate" and not allow_candidate:
        raise ValueError("candidate bindings require XENOVA_DISPLAY_ALLOW_CANDIDATE_BINDINGS=1")
    return tuple(profile.bindings), profile.scope


@dataclass(frozen=True)
class FeedSettings:
    bindings: tuple[Binding, ...]
    token: str
    mqtt_host: str = "127.0.0.1"
    mqtt_port: int = 1883
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    control_source: str = "dom0-gateway"
    binding_scope: Literal["legacy", "candidate", "deployment", "configured"] = "configured"

    def __post_init__(self):
        if not self.token.strip():
            raise ValueError("XENOVA_DISPLAY_TOKEN is required")
        if len(self.bindings) != 3:
            raise ValueError("exactly three VM bindings are required")
        for field in ("vm_id", "role", "app_id"):
            if len({getattr(b, field) for b in self.bindings}) != 3:
                raise ValueError(f"duplicate {field}")
        for b in self.bindings:
            expected = "debian" if b.role == "interaction" else "zephyr"
            if b.expected_os != expected:
                raise ValueError("VM role/OS mismatch")

    @classmethod
    def from_env(cls):
        bindings, scope = load_bindings(os.environ["XENOVA_DISPLAY_BINDINGS"],
            allow_candidate=os.environ.get("XENOVA_DISPLAY_ALLOW_CANDIDATE_BINDINGS") == "1")
        return cls(
            bindings=bindings,
            token=os.environ.get("XENOVA_DISPLAY_TOKEN", ""),
            mqtt_host=os.environ.get("XENOVA_DISPLAY_MQTT_HOST", "127.0.0.1"),
            mqtt_port=int(os.environ.get("XENOVA_DISPLAY_MQTT_PORT", "1883")),
            mqtt_username=os.environ.get("XENOVA_DISPLAY_MQTT_USERNAME"),
            mqtt_password=os.environ.get("XENOVA_DISPLAY_MQTT_PASSWORD"),
            control_source=os.environ.get("XENOVA_DISPLAY_CONTROL_SOURCE", "dom0-gateway"),
            binding_scope=scope,
        )
