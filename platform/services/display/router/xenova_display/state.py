"""Source-aware state normalization. Never executes console, VM or driving commands."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock
import time
from uuid import uuid4

from xenova_mqtt.envelope import EnvelopeError, validate

from .config import FeedSettings
from .app_reports import report_app


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
            "observation_id": None,
            "boot_id": None, "retired_boots": set(), "channels": {}, "retired_epochs": set(),
            "app_instances": {}, "retired_apps": set(), "app_sequences": {}, "pending_inventory": {},
        } for b in settings.bindings}
        # Driving state has one source: the dom0 Gateway context (gateway.py).
        self.gateway_observation = None
        self.gateway_connected = False
        self.gateway_identity = None

    def gateway_success(self, value, *, observed_mono):
        with self.lock:
            identity = (value["authority"]["generation"],
                        value["authority"]["selected_vm_id"], value["authority"]["selected_mode"])
            if self.gateway_identity is not None:
                previous = self.gateway_identity
                if identity[0] < previous[0] or (identity[0] == previous[0] and identity != previous):
                    raise ValueError("Gateway authority regressed or conflicted")
            self.gateway_identity = identity
            self.gateway_observation = self.observation(value, source_id="gateway.context")
            self.gateway_observation["received_mono"] = observed_mono
            self.gateway_connected = True

    def gateway_failure(self):
        with self.lock:
            self.gateway_connected = False

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
            for guest in self.guests.values():
                guest["pending_inventory"].clear()
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

    def poll_success(self, domains, *, observed_mono=None):
        with self.lock:
            rows = {str(d.vm_id): d for d in domains}
            for vm_id, g in self.guests.items():
                d = rows.get(vm_id)
                domid = d.domid if d else None
                identity = getattr(d, "observation_id", None) if d else None
                if domid != g["domid"] or identity != g["observation_id"]:
                    old_execution = (g['observation_id'] or '').partition(':')[2]
                    new_execution = (identity or '').partition(':')[2]
                    # Restarting the observer alone does not reboot the guest.
                    # Clear display evidence, but allow its next live report.
                    changed_run = domid != g['domid'] or old_execution != new_execution
                    if g["boot_id"] and changed_run:
                        g["retired_boots"].add(g["boot_id"])
                    g.update(boot_id=None, channels={}, retired_epochs=set(), app_instances={}, retired_apps=set(), app_sequences={}, pending_inventory={})
                g.update(domid=domid, xen_state=d.state.value if d else "stopped", observation_id=identity,
                         run_id=f"{self.epoch}:{identity or domid}" if domid is not None else None)
            self.xen = self.observation("ok")
            if observed_mono is not None:
                self.xen["received_mono"] = observed_mono

    def poll_failure(self):
        # Preserve last observations but explicitly mark the transport unavailable.
        with self.lock:
            if self.xen is not None:
                self.xen["value"] = "unavailable"

    def ingest(self, topic, env, *, retained=False, qos=None, wire_size=None):
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
            }
            app_id = None
            if kind.startswith("app."):
                app_id = report_app(b, topic, env, retained=retained, qos=qos, wire_size=wire_size)
                if app_id is None:
                    return False
            elif allowed.get(topic) != kind or not b.agent_supported:
                return False
            if kind == "agent.inventory":
                os_info = env["payload"].get("os")
                if (not isinstance(os_info, dict) or not isinstance(os_info.get("id"), str)
                        or not 1 <= len(os_info["id"]) <= 64):
                    return False
                epoch = (env["boot_id"], env["agent_instance_id"])
                if epoch[0] in g["retired_boots"] or ("agent.heartbeat", epoch) in g["retired_epochs"]:
                    return False
                heartbeat = g["channels"].get("agent.heartbeat")
                heartbeat_topic = f"xenova/v1/vms/{vm_id}/heartbeat"
                if (heartbeat is None or heartbeat["source_epoch"] != list(epoch)
                        or self.mqtt_source(heartbeat, 15, heartbeat_topic)["status"] != "fresh"):
                    # Inventory is not liveness. Hold only a bounded OS observation
                    # until this exact Agent session reports on the same connection.
                    pending = g["pending_inventory"]
                    previous = pending.get(epoch)
                    if previous and env["seq"] <= previous["sequence"]:
                        return False
                    for key in list(pending):
                        if self.clock() - pending[key]["received_mono"] >= 15:
                            del pending[key]
                    if epoch not in pending and len(pending) >= 4:
                        return False
                    pending[epoch] = self.observation({"os": {"id": os_info["id"]}},
                        observed_at=env["sent_at"], source_id=topic, source_epoch=list(epoch),
                        sequence=env["seq"], retained=retained, _mqtt_generation=self.mqtt_generation)
                    return False  # Deferred; not yet accepted as displayed OS evidence.
            # A live heartbeat or opted-in warm supervision may establish a
            # reported boot; retained inventory never proves current liveness.
            boot = env["boot_id"]
            if boot != g["boot_id"]:
                if retained or kind in ("agent.inventory", "app.readiness") or boot in g["retired_boots"] or len(g["retired_boots"]) >= 128:
                    return False
                if g["boot_id"]:
                    g["retired_boots"].add(g["boot_id"])
                pending = {k: v for k, v in g["pending_inventory"].items() if k[0] == boot}
                g.update(boot_id=boot, channels={}, retired_epochs=set(), app_instances={}, retired_apps=set(), app_sequences={}, pending_inventory=pending)
            if retained and kind != "agent.inventory":
                return False
            epoch = (boot, env["agent_instance_id"])
            scoped = app_id is not None and b.app_topic_scope == "vm_scoped"
            key = (app_id, kind) if scoped else kind
            if scoped:
                if (app_id, epoch) in g["retired_apps"]:
                    return False
                current = g["app_instances"].get(app_id)
                if kind == "app.readiness":
                    heartbeat = g["channels"].get((app_id, "app.heartbeat"))
                    if (current != epoch or self.mqtt_source(heartbeat, 3, topic)["status"] != "fresh"):
                        return False
                elif current != epoch:
                    if current is not None:
                        if len(g["retired_apps"]) >= 128:
                            return False
                        g["retired_apps"].add((app_id, current))
                    # A publisher restart invalidates only this app's readiness.
                    for app_kind in ("app.heartbeat", "app.readiness", "app.lifecycle", "app.lifecycle.work"):
                        g["channels"].pop((app_id, app_kind), None)
                    g["app_instances"][app_id] = epoch
                    g["app_sequences"].pop(app_id, None)
                # The firmware shares one sequence across report types on each app topic.
                if env["seq"] <= g["app_sequences"].get(app_id, -1):
                    return False
                # A valid but non-progressing report cannot refresh state, but still
                # prevents a delayed older heartbeat from renewing that app's liveness.
                g["app_sequences"][app_id] = env["seq"]
            previous = g["channels"].get(key)
            if (key, epoch) in g["retired_epochs"]:
                return False
            if previous:
                if previous["source_epoch"] == list(epoch) and env["seq"] <= previous["sequence"]:
                    return False
                if scoped and kind == "app.readiness":
                    old, new = previous["value"], env["payload"]
                    if (new["applied_generation"] < old["applied_generation"]
                            or int(new["progress_seq"]) < int(old["progress_seq"])
                            or (new["readiness"] == "READY" and int(new["progress_seq"]) == int(old["progress_seq"]))):
                        return False
                if scoped and kind == "app.lifecycle":
                    old, new = previous["value"], env["payload"]
                    if (new["applied_generation"] < old["applied_generation"]
                            or int(new["check_seq"]) <= int(old["check_seq"])
                            or int(new["progress_seq"]) < int(old["progress_seq"])):
                        return False
                if previous["source_epoch"] != list(epoch):
                    if len(g["retired_epochs"]) >= 128:
                        return False
                    g["retired_epochs"].add((key, tuple(previous["source_epoch"])))
            if scoped and kind == "app.lifecycle":
                work_key = (app_id, "app.lifecycle.work")
                payload = env["payload"]
                if payload["readiness"] != "READY":
                    g["channels"].pop(work_key, None)
                elif previous is not None and payload["applied_generation"] != previous["value"]["applied_generation"]:
                    g["channels"].pop(work_key, None)
                    if int(payload["progress_seq"]) > int(previous["value"]["progress_seq"]):
                        g["channels"][work_key] = self.observation(payload, observed_at=env["sent_at"],
                            source_id=topic, source_epoch=list(epoch), sequence=env["seq"],
                            retained=False, _mqtt_generation=self.mqtt_generation)
                elif previous is None or int(payload["progress_seq"]) > int(previous["value"]["progress_seq"]):
                    g["channels"][work_key] = self.observation(payload, observed_at=env["sent_at"],
                        source_id=topic, source_epoch=list(epoch), sequence=env["seq"],
                        retained=False, _mqtt_generation=self.mqtt_generation)
            g["channels"][key] = self.observation(env["payload"], observed_at=env["sent_at"],
                source_id=topic, source_epoch=list(epoch), sequence=env["seq"], retained=retained,
                _mqtt_generation=self.mqtt_generation)
            if kind == "agent.heartbeat":
                inventory = g["channels"].get("agent.inventory")
                if inventory and inventory["source_epoch"] != list(epoch):
                    del g["channels"]["agent.inventory"]
                    inventory = None
                pending = g["pending_inventory"].pop(epoch, None)
                g["pending_inventory"].clear()
                inventory_topic = f"xenova/v1/vms/{vm_id}/inventory"
                if (pending and self.clock() - pending["received_mono"] < 15
                        and pending["_mqtt_generation"] == self.mqtt_generation
                        and self.topic_available(inventory_topic)
                        and (inventory is None or pending["sequence"] > inventory["sequence"])):
                    # Keep the original inventory receive time, never rejuvenate it.
                    g["channels"]["agent.inventory"] = pending
            return True

    def app_snapshot(self, g, b, app_id, xen_source):
        topic = b.app_topic(app_id)
        scoped = b.app_topic_scope == "vm_scoped"
        heartbeat = g["channels"].get((app_id, "app.heartbeat") if scoped else "app.heartbeat")
        source = self.mqtt_source(heartbeat, 3, topic)
        state = "unknown"
        xen_live = xen_source["status"] == "fresh" and g["xen_state"] == "running"
        if xen_live and source["status"] == "fresh":
            state = "failed" if heartbeat["value"]["state"] == "error" else "ready"
        observation = g["channels"].get((app_id, "app.readiness")) if scoped else None
        readiness_source = self.mqtt_source(observation, .3, topic,
            supported=scoped and b.report_profile == "verified-drive-1")
        current = (xen_live and state == "ready" and readiness_source["status"] == "fresh")
        warm = scoped and b.report_profile == "warm-drive-1"
        lifecycle = g["channels"].get((app_id, "app.lifecycle")) if warm else None
        lifecycle_source = self.mqtt_source(lifecycle, .3, topic, supported=warm)
        work = g["channels"].get((app_id, "app.lifecycle.work")) if warm else None
        work_source = self.mqtt_source(work, .3, topic, supported=warm)
        lifecycle_current = (deepcopy(lifecycle["value"]) if lifecycle is not None
            and xen_live and lifecycle_source["status"] == "fresh"
            and (lifecycle["value"]["readiness"] != "READY" or work_source["status"] == "fresh") else None)
        if warm and state == "ready" and (lifecycle_current is None or lifecycle_current["readiness"] != "READY"):
            state = "unknown"  # Fresh supervision/qualification is not completed work.
        return {"app_id": app_id, "topic": topic, "app_state": state, "report_profile": b.report_profile,
                "reported_app_state": heartbeat["value"]["state"] if heartbeat else None,
                "source": source,
                "lifecycle": {"reported": deepcopy(lifecycle["value"]) if lifecycle else None,
                    "current": lifecycle_current, "source": lifecycle_source,
                    "calculation_source": work_source, "execution_verified": False, "authority_verified": False},
                "readiness": {"reported": deepcopy(observation["value"]) if observation else None,
                    "current": deepcopy(observation["value"]) if current else None,
                    "source": readiness_source, "execution_verified": False, "authority_verified": False}}

    @staticmethod
    def app_summary(apps):
        """Conservative old VM-level summary; never infer mode/owner from an app."""
        rows = list(apps.values())
        sources = [a["source"] for a in rows]
        status = next((s for s in ("disconnected", "unknown", "stale")
                       if any(r["status"] == s for r in sources)), "fresh")
        subscription = next((s for s in ("denied", "error", "unavailable", "pending", "unsupported", "untracked")
                             if any(r["subscription_status"] == s for r in sources)), "granted")
        ages = [r["age_ms"] for r in sources]
        source = {"status": status, "subscription_status": subscription,
                  "age_ms": max(ages) if all(a is not None for a in ages) else None}
        state = ("failed" if any(a["app_state"] == "failed" for a in rows)
                 else "ready" if all(a["app_state"] == "ready" for a in rows) else "unknown")
        return source, state

    def snapshot(self):
        with self.lock:
            self.sequence += 1
            xen_source = self.source(self.xen, 3, connected=self.xen is not None and self.xen["value"] == "ok")
            guests = []
            for vm_id, g in self.guests.items():
                b = g["binding"]
                channels = g["channels"]
                apps = {app_id: self.app_snapshot(g, b, app_id, xen_source) for app_id in b.report_app_ids}
                if b.app_topic_scope == "legacy":
                    app_source = apps[b.app_id]["source"]
                    app_state = apps[b.app_id]["app_state"]
                    reported_app_state = apps[b.app_id]["reported_app_state"]
                else:
                    app_source, app_state = self.app_summary(apps)
                    reported_app_state = None
                inv = channels.get("agent.inventory")
                observed_os = inv["value"]["os"]["id"] if inv else None
                guests.append({"vm_id": vm_id, "role": b.role, "app_id": b.app_id,
                    "app_topic_scope": b.app_topic_scope, "apps": apps,
                    "run_id": g["run_id"], "guest_boot_id": g["boot_id"],
                    "xen_state": g["xen_state"], "operation": None,
                    "os": {"expected": b.expected_os, "observed": observed_os,
                           "match": observed_os == b.expected_os if observed_os else None,
                           "evidence": "agent.inventory" if inv else None,
                           "current_run_verified": False},
                    "app_state": app_state, "reported_app_state": reported_app_state,
                    "agent_supported": b.agent_supported,
                    "sources": {"xen": xen_source, "app": app_source,
                        "agent": self.mqtt_source(channels.get("agent.heartbeat"), 15,
                            f"xenova/v1/vms/{vm_id}/heartbeat", supported=b.agent_supported),
                        "inventory": self.mqtt_source(inv, 30,
                            f"xenova/v1/vms/{vm_id}/inventory", supported=b.agent_supported)},
                    "screen_source": {"kind": "status", "handle": None}})
            # No Gateway read yet (e.g. no socket on a development PC), failure or
            # expiry all stay unknown; there is no MQTT or retained fallback.
            source = self.source(self.gateway_observation, 3, connected=self.gateway_connected)
            drive = {"active_vm_id": None, "mode": "unknown", "ai_status": "unknown",
                     "source": source, "authority": None}
            if source["status"] == "fresh":
                drive.update(deepcopy(self.gateway_observation["value"]))
            return {"schema_version": 1, "type": "display.state", "source_epoch": self.epoch,
                    "sequence": self.sequence, "generated_at": utc_now(),
                    "mqtt_connected": self.mqtt_connected, "binding_scope": self.settings.binding_scope,
                    "mqtt_subscriptions": dict(self.subscriptions or {}), "vms": guests, "drive": drive}
