from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

log = logging.getLogger(__name__)

# Retired 2026-11-06: driving state is read only from the dom0 Gateway context
# (the xenova/v1/display/drive-state MQTT path had no publisher). Old deployment
# files may still set these; they are ignored, never a startup error.
RETIRED_ENV = ("XENOVA_DISPLAY_DRIVE_TRANSPORT", "XENOVA_DISPLAY_CONTROL_SOURCE")


def warn_retired_env(environ=os.environ):
    for name in RETIRED_ENV:
        value = environ.get(name)
        if value is None or (name == "XENOVA_DISPLAY_DRIVE_TRANSPORT" and value == "gateway"):
            continue  # Unset, or already the only remaining behaviour.
        log.warning("%s=%r is retired and ignored; driving state is read from the Gateway context",
                    name, value)


class Binding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    vm_id: UUID
    role: str
    expected_os: str
    app_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    app_ids: tuple[str, ...] = Field(default=(), max_length=2)
    app_topic_scope: Literal["legacy", "vm_scoped"] = "legacy"
    report_profile: Literal["verified-drive-1", "warm-drive-1"] = "verified-drive-1"
    agent_supported: bool = False

    @model_validator(mode="after")
    def app_mapping(self):
        if self.report_profile == "warm-drive-1" and self.app_topic_scope != "vm_scoped":
            raise ValueError("warm profile requires VM-scoped drive apps")
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
    binding_scope: Literal["legacy", "candidate", "deployment", "configured"] = "configured"
    gateway_socket: str = "/run/xenova-gateway/control.sock"
    gateway_budget_s: float = 0.5

    def __post_init__(self):
        if any(b.report_profile == "warm-drive-1" for b in self.bindings):
            # Explicit profile opt-in must not silently run with an older parser.
            from xenova_mqtt.lifecycle import parse  # noqa: F401
        if not PurePosixPath(self.gateway_socket).is_absolute() or '\0' in self.gateway_socket:
            raise ValueError("Gateway socket path must be absolute")
        if type(self.gateway_budget_s) not in (int, float) or not 0 < self.gateway_budget_s <= 1:
            raise ValueError("Gateway read budget must be in (0, 1] seconds")
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
        warn_retired_env()
        bindings, scope = load_bindings(os.environ["XENOVA_DISPLAY_BINDINGS"],
            allow_candidate=os.environ.get("XENOVA_DISPLAY_ALLOW_CANDIDATE_BINDINGS") == "1")
        return cls(
            bindings=bindings,
            token=os.environ.get("XENOVA_DISPLAY_TOKEN", ""),
            mqtt_host=os.environ.get("XENOVA_DISPLAY_MQTT_HOST", "127.0.0.1"),
            mqtt_port=int(os.environ.get("XENOVA_DISPLAY_MQTT_PORT", "1883")),
            mqtt_username=os.environ.get("XENOVA_DISPLAY_MQTT_USERNAME"),
            mqtt_password=os.environ.get("XENOVA_DISPLAY_MQTT_PASSWORD"),
            binding_scope=scope,
            gateway_socket=os.environ.get("XENOVA_DISPLAY_GATEWAY_SOCKET", "/run/xenova-gateway/control.sock"),
            gateway_budget_s=float(os.environ.get("XENOVA_DISPLAY_GATEWAY_BUDGET_S", "0.5")),
        )
