"""Source-aware state normalization. Never executes console, VM or driving commands."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock
import time
from uuid import uuid4

from xenova_mqtt.envelope import EnvelopeError, validate

from .config import DISPLAY_DRIVE_TOPIC, DISPLAY_DRIVE_TYPE, FeedSettings


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class StateStore:
    def __init__(self, settings: FeedSettings, clock=time.monotonic):
        self.settings = settings
        self.clock = clock
        self.lock = RLock()
        self.epoch = str(uuid4())
        self.sequence = 0
        self.xen = None
        self.mqtt_connected = False
        # None is reserved for injected sources/tests that do not use the MQTT adapter.
        self.subscriptions = None
        self.mqtt_generation = 0
        self.guests = {str(b.vm_id): {
            "binding": b, "domid": None, "xen_state": None, "run_id": None,
            "boot_id": None, "retired_boots": set(), "channels": {}, "retired_epochs": set(),
        } for b in settings.bindings}
        self.control = None
        self.control_epoch = None
        self.control_retired = set()

    def observation(self, value, *, observed_at=None, **metadata):
        return {"value": deepcopy(value), "received_at": utc_now(), "received_mono": self.clock(),
                "observed_at": observed_at, **metadata}

    def source(self, observation, ttl, *, connected=True):
        if observation is None:
            return {"status": "unknown", "age_ms": None}
        age = max(0, int((self.clock() - observation["received_mono"]) * 1000))
        return {**{k: v for k, v in observation.items() if k not in {"value", "received_mono", "_mqtt_generation"}},
                "age_ms": age, "status": "disconnected" if not connected else "stale" if age >= ttl * 1000 else "fresh"}

    def mqtt_connection(self, connected, topics=()):
        with self.lock:
            self.mqtt_connected = connected
            self.mqtt_generation += 1
            self.subscriptions = {topic: "pending" if connected else "unavailable" for topic in topics}

    def subscription(self, topic, status):
        with self.lock:
            if self.mqtt_connected and self.subscriptions is not None and topic in self.subscriptions:
                self.subscriptions[topic] = status

    def topic_available(self, topic):
        return self.mqtt_connected and (self.subscriptions is None or self.subscriptions.get(topic) == "granted")

    def mqtt_source(self, observation, ttl, topic, *, supported=True):
        subscription = ("unsupported" if not supported else "untracked" if self.subscriptions is None
                        else self.subscriptions.get(topic, "unavailable"))
        current = observation is None or observation.get("_mqtt_generation") == self.mqtt_generation
        return {**self.source(observation, ttl, connected=supported and self.topic_available(topic) and current),
                "subscription_status": subscription}

    def poll_success(self, domains):
        with self.lock:
            rows = {str(d.vm_id): d for d in domains}
            for vm_id, g in self.guests.items():
                d = rows.get(vm_id)
                domid = d.domid if d else None
                if domid != g["domid"]:
                    if g["boot_id"]:
                        g["retired_boots"].add(g["boot_id"])
                    g.update(boot_id=None, channels={}, retired_epochs=set())
                g.update(domid=domid, xen_state=d.state.value if d else "stopped",
                         run_id=f"{self.epoch}:{domid}" if domid is not None else None)
            self.xen = self.observation("ok")

    def poll_failure(self):
        # Preserve last observations but explicitly mark the transport unavailable.
        with self.lock:
            if self.xen is not None:
                self.xen["value"] = "unavailable"

    def ingest(self, topic, env, *, retained=False):
        try:
            validate(topic, env)
        except (EnvelopeError, TypeError, ValueError):
            return False
        # Keep a malformed but authenticated publisher from growing snapshot metadata without bound.
        for field in ("boot_id", "agent_instance_id", "sent_at"):
            if not isinstance(env.get(field), str) or not 1 <= len(env[field]) <= 256:
                return False
        with self.lock:
            if not self.topic_available(topic):
                return False
            if topic == DISPLAY_DRIVE_TOPIC:
                return self._control(env, retained)
            vm_id = env.get("vm_id")
            if not isinstance(vm_id, str) or env.get("source_id") is not None:
                return False
            g = self.guests.get(vm_id)
            if g is None or g["domid"] is None or g["xen_state"] != "running":
                return False
            if self.source(self.xen, 3, connected=self.xen is not None and self.xen["value"] == "ok")["status"] != "fresh":
                return False
            b = g["binding"]
            kind = env["type"]
            allowed = {
                f"xenova/v1/vms/{vm_id}/heartbeat": "agent.heartbeat",
                f"xenova/v1/vms/{vm_id}/inventory": "agent.inventory",
                f"xenova/v1/apps/{b.app_id}/events": "app.heartbeat",
            }
            if allowed.get(topic) != kind or (kind.startswith("agent.") and not b.agent_supported):
                return False
            if kind == "app.heartbeat" and env["payload"].get("state") not in ("active", "standby", "idle", "error"):
                return False
            if kind == "agent.inventory":
                os_info = env["payload"].get("os")
                if (not isinstance(os_info, dict) or not isinstance(os_info.get("id"), str)
                        or not 1 <= len(os_info["id"]) <= 64):
                    return False
            # Only a non-retained heartbeat establishes a boot, not historical inventory.
            boot = env["boot_id"]
            if boot != g["boot_id"]:
                if retained or kind == "agent.inventory" or boot in g["retired_boots"] or len(g["retired_boots"]) >= 128:
                    return False
                if g["boot_id"]:
                    g["retired_boots"].add(g["boot_id"])
                g.update(boot_id=boot, channels={}, retired_epochs=set())
            if retained and kind != "agent.inventory":
                return False
            epoch = (boot, env["agent_instance_id"])
            key = kind
            previous = g["channels"].get(key)
            if (key, epoch) in g["retired_epochs"]:
                return False
            if previous:
                if previous["source_epoch"] == list(epoch) and env["seq"] <= previous["sequence"]:
                    return False
                if previous["source_epoch"] != list(epoch):
                    if len(g["retired_epochs"]) >= 128:
                        return False
                    g["retired_epochs"].add((key, tuple(previous["source_epoch"])))
            g["channels"][key] = self.observation(env["payload"], observed_at=env["sent_at"],
                source_id=topic, source_epoch=list(epoch), sequence=env["seq"], retained=retained,
                _mqtt_generation=self.mqtt_generation)
            return True

    def _control(self, env, retained):
        p = env["payload"]
        # Old active_app semantics must not be used to infer a VM or a driving mode.
        if (retained or env.get("source_id") != self.settings.control_source
                or env.get("vm_id") is not None or env["type"] != DISPLAY_DRIVE_TYPE):
            return False
        if p.get("mode") not in ("AUTO", "MANUAL", "SAFE_STOP", "AUTO_READY"):
            return False
        if "active_vm_id" not in p:
            return False
        active = p["active_vm_id"]
        if active is not None and (not isinstance(active, str) or active not in self.guests or self.guests[active]["binding"].role == "interaction"):
            return False
        epoch = (env["boot_id"], env["agent_instance_id"])
        if epoch in self.control_retired:
            return False
        if self.control is not None:
            if epoch == self.control_epoch and env["seq"] <= self.control["sequence"]:
                return False
            if epoch != self.control_epoch:
                if len(self.control_retired) >= 128:
                    return False
                self.control_retired.add(self.control_epoch)
        self.control_epoch = epoch
        self.control = self.observation({"active_vm_id": active, "mode": p["mode"]},
            observed_at=env["sent_at"], source_id=self.settings.control_source,
            source_epoch=list(epoch), sequence=env["seq"], _mqtt_generation=self.mqtt_generation)
        return True

    def snapshot(self):
        with self.lock:
            self.sequence += 1
            xen_source = self.source(self.xen, 3, connected=self.xen is not None and self.xen["value"] == "ok")
            guests = []
            for vm_id, g in self.guests.items():
                b = g["binding"]
                channels = g["channels"]
                app = channels.get("app.heartbeat")
                app_source = self.mqtt_source(app, 3, f"xenova/v1/apps/{b.app_id}/events")
                inv = channels.get("agent.inventory")
                observed_os = inv["value"]["os"]["id"] if inv else None
                app_state = "unknown"
                if xen_source["status"] == "fresh" and g["xen_state"] == "running" and app_source["status"] == "fresh":
                    app_state = "failed" if app["value"]["state"] == "error" else "ready"
                guests.append({"vm_id": vm_id, "role": b.role, "app_id": b.app_id,
                    "run_id": g["run_id"], "guest_boot_id": g["boot_id"],
                    "xen_state": g["xen_state"], "operation": None,
                    "os": {"expected": b.expected_os, "observed": observed_os,
                           "match": observed_os == b.expected_os if observed_os else None,
                           "evidence": "agent.inventory" if inv else None,
                           "current_run_verified": False},
                    "app_state": app_state, "reported_app_state": app["value"].get("state") if app else None,
                    "agent_supported": b.agent_supported,
                    "sources": {"xen": xen_source, "app": app_source,
                        "agent": self.mqtt_source(channels.get("agent.heartbeat"), 15,
                            f"xenova/v1/vms/{vm_id}/heartbeat", supported=b.agent_supported),
                        "inventory": self.mqtt_source(inv, 30,
                            f"xenova/v1/vms/{vm_id}/inventory", supported=b.agent_supported)},
                    "screen_source": {"kind": "status", "handle": None}})
            control_source = self.mqtt_source(self.control, 3, DISPLAY_DRIVE_TOPIC)
            drive = {"active_vm_id": None, "mode": "unknown", "ai_status": "unknown", "source": control_source}
            if control_source["status"] == "fresh":
                drive.update(self.control["value"])
            return {"schema_version": 1, "type": "display.state", "source_epoch": self.epoch,
                    "sequence": self.sequence, "generated_at": utc_now(),
                    "mqtt_connected": self.mqtt_connected, "binding_scope": self.settings.binding_scope,
                    "mqtt_subscriptions": dict(self.subscriptions or {}), "vms": guests, "drive": drive}
