from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DISPLAY_DRIVE_TOPIC = "xenova/v1/display/drive-state"
DISPLAY_DRIVE_TYPE = "display.drive_state"


class Binding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    vm_id: UUID
    role: str
    expected_os: str
    app_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    app_ids: tuple[str, ...] = Field(default=(), max_length=2)
    app_topic_scope: Literal["legacy", "vm_scoped"] = "legacy"
    agent_supported: bool = False

    @model_validator(mode="after")
    def app_mapping(self):
        if self.app_topic_scope == "legacy":
            if self.app_id is None or self.app_ids:
                raise ValueError("legacy requires one app_id and no app_ids")
        elif (self.app_id is not None or len(self.app_ids) != 2
              or set(self.app_ids) != {"autonomous", "manual"}
              or self.role == "interaction" or self.expected_os != "zephyr"):
            raise ValueError("vm_scoped requires both Zephyr drive apps and no single app_id")
        return self

    @property
    def report_app_ids(self):
        return self.app_ids if self.app_topic_scope == "vm_scoped" else (self.app_id,)

    def app_topic(self, app_id):
        if app_id not in self.report_app_ids:
            raise ValueError("app is not configured for this VM")
        if self.app_topic_scope == "vm_scoped":
            return f"xenova/v1/vms/{self.vm_id}/apps/{app_id}/events"
        return f"xenova/v1/apps/{app_id}/events"

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
        if bool(self.mqtt_username) != bool(self.mqtt_password):
            raise ValueError('MQTT username and password must be configured together')
        if not self.token.strip():
            raise ValueError("XENOVA_DISPLAY_TOKEN is required")
        if len(self.bindings) != 3:
            raise ValueError("exactly three VM bindings are required")
        for field in ("vm_id", "role"):
            if len({getattr(b, field) for b in self.bindings}) != 3:
                raise ValueError(f"duplicate {field}")
        legacy_apps = [b.app_id for b in self.bindings if b.app_topic_scope == "legacy"]
        if len(legacy_apps) != len(set(legacy_apps)):
            raise ValueError("duplicate legacy app_id")
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
