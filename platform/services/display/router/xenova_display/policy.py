"""Two logical outputs. Decisions never execute VM recovery or driving commands."""
from copy import deepcopy
import hashlib
import json
import time
from threading import RLock

from pydantic import BaseModel, ConfigDict
from typing import Literal
from uuid import UUID


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy_epoch: str
    revision: int
    selection: Literal["pinned", "follow_active"]
    vm_id: UUID | None = None


class RenderAck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy_epoch: str
    revision: int
    render_revision: str


def decide_vm(vm):
    xen = vm["sources"]["xen"]["status"]
    if xen == "unknown" or vm["xen_state"] is None:
        return {"kind": "waiting", "label": "상태 수신 대기", "tone": "muted"}
    if xen != "fresh":
        return {"kind": "stale", "label": "VM 상태 수신 지연 / 연결 끊김", "tone": "warning"}
    state = vm["xen_state"]
    labels = {"crashed": ("VM 장애", "danger"), "stopped": ("정지", "muted"),
              "stopping": ("종료 중", "warning"), "paused": ("일시 정지", "warning")}
    if state in labels:
        label, tone = labels[state]
        return {"kind": state, "label": label, "tone": tone}
    if state != "running":
        return {"kind": "waiting", "label": "실행 상태 미확인", "tone": "muted"}
    if vm.get("operation") is not None:
        return {"kind": "operation", "label": "VM 작업 진행 중", "tone": "warning"}
    if vm["os"]["observed"] and vm["os"]["observed"] != vm["os"]["expected"]:
        return {"kind": "os_mismatch", "label": "OS 보고 불일치 확인 필요", "tone": "warning"}
    if vm["app_state"] == "failed":
        return {"kind": "app_error", "label": "앱 오류 · VM 실행 중", "tone": "danger"}
    if vm["sources"]["app"].get("subscription_status") in {"pending", "denied", "error", "unavailable"}:
        return {"kind": "app_source_unavailable", "label": "VM 실행 중 · 앱 보고 수신 경로 미확인", "tone": "warning"}
    if vm["app_state"] != "ready" or vm["sources"]["app"]["status"] != "fresh":
        return {"kind": "app_waiting", "label": "VM 실행 중 · 앱 상태 미확인", "tone": "warning"}
    # Graphics handles are not playable URLs. A capture adapter is a later stage.
    return {"kind": "status", "label": "앱 상태 수신 정상", "tone": "ok"}


class DisplayPolicy:
    def __init__(self, settings, epoch, clock=time.monotonic):
        self.lock = RLock()
        self.epoch = epoch
        self.clock = clock
        self.revision = 1
        self.selection = "pinned"
        self.selected = str(next(b.vm_id for b in settings.bindings if b.role == "primary_drive"))
        self.registered = {str(b.vm_id) for b in settings.bindings}
        self.acks = {}

    def select(self, request):
        with self.lock:
            if request.policy_epoch != self.epoch or request.revision != self.revision:
                raise ValueError("stale display selection")
            if request.selection == "pinned":
                if str(request.vm_id) not in self.registered:
                    raise LookupError("registered VM UUID required")
                selected = str(request.vm_id)
            else:
                if request.vm_id is not None:
                    raise LookupError("follow_active does not accept a VM UUID")
                selected = self.selected
            if (request.selection, selected) != (self.selection, self.selected):
                self.selection, self.selected = request.selection, selected
                self.revision += 1
                self.acks.clear()

    def presentation(self, state):
        with self.lock:
            drive = state["drive"]
            active = drive["active_vm_id"] if drive["source"]["status"] == "fresh" else None
            if self.selection == "follow_active" and active in self.registered:
                self.selected = active
            decisions = {v["vm_id"]: decide_vm(v) for v in state["vms"]}
            outputs = [
                {"output_id": "monitor-1", "view": "overview", "selection": "pinned", "vm_id": None},
                {"output_id": "monitor-2", "view": "detail", "selection": self.selection, "vm_id": self.selected},
            ]
            # Content identity excludes continuously changing age/capture time, but includes run/boot/source changes.
            content = {"outputs": outputs, "decisions": decisions, "drive": drive,
                       "vms": state["vms"]}
            def stable(value):
                if isinstance(value, dict):
                    return {k: stable(v) for k, v in value.items()
                            if k not in {"age_ms", "received_at", "observed_at"}}
                if isinstance(value, list):
                    return [stable(v) for v in value]
                return value
            render_revision = hashlib.sha256(json.dumps(stable(content), sort_keys=True).encode()).hexdigest()[:20]
            for output in outputs:
                ack = self.acks.get(output["output_id"])
                age = max(0, int((self.clock() - ack["_received_mono"]) * 1000)) if ack else None
                valid = ack and ack["render_revision"] == render_revision and age < 3000
                output["renderer_ack"] = ({**{k: deepcopy(v) for k, v in ack.items() if k != "_received_mono"},
                                           "age_ms": age} if valid else None)
            return {"schema_version": 1, "type": "display.presentation", "policy_epoch": self.epoch,
                    "revision": self.revision, "render_revision": render_revision,
                    "outputs": outputs, "decisions": decisions, "state": state}

    def acknowledge(self, output_id, ack, state):
        with self.lock:
            current = self.presentation(state)
            if output_id not in {"monitor-1", "monitor-2"}:
                raise LookupError("unknown output")
            if any(getattr(ack, k) != current[k] for k in ("policy_epoch", "revision", "render_revision")):
                raise ValueError("renderer acknowledged an obsolete decision")
            self.acks[output_id] = {**ack.model_dump(), "stage": "dom_rendered", "_received_mono": self.clock()}
