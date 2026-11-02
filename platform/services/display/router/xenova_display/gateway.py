"""Read the existing Gateway context only; never submit/commit/resume control."""
import time
from pathlib import Path
from uuid import UUID


def canonical(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("invalid Gateway UUID")
    return value


def bounded_integer(value, maximum=2**63-1):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError("invalid Gateway integer")
    return value


def normalize(context, *, host_boot_id, drive_ids):
    """Project current control evidence, not physical movement or device success."""
    if not isinstance(context, dict):
        raise ValueError("Gateway context must be an object")
    if canonical(context.get("host_boot_id")) != host_boot_id:
        raise ValueError("Gateway context is from another host boot")
    owner = canonical(context.get("owner"))
    if owner not in drive_ids:
        raise ValueError("Gateway owner is outside registered drive VMs")
    generation = bounded_integer(context.get("generation"), 0xffffffff)
    if not generation:
        raise ValueError("Gateway generation must be positive")
    mode, phase = context.get("mode"), context.get("phase")
    if mode not in ("AUTO", "MANUAL", "AUTO_READY", "SAFE_STOP"):
        raise ValueError("invalid Gateway mode")
    if phase not in ("APPLIED", "PENDING", "DISARMED", "FAILED"):
        raise ValueError("invalid Gateway phase")
    permitted, uncertain = context.get("output_permitted"), context.get("storage_uncertain")
    if type(permitted) is not bool or type(uncertain) is not bool:
        raise ValueError("missing Gateway permission evidence")
    targets = context.get("targets")
    if not isinstance(targets, dict) or set(targets) != set(drive_ids):
        raise ValueError("Gateway drive pair does not match display bindings")
    for row in targets.values():
        if row is None:
            continue
        if not isinstance(row, dict):
            raise ValueError("invalid Gateway execution binding")
        canonical(row.get("execution_id")); canonical(row.get("boot_id"))
        bounded_integer(row.get("intent_revision")); bounded_integer(row.get("lifecycle_generation"))
    # Selected owner persists across restart. Only explicitly permitted, applied
    # AUTO/MANUAL output may become the display's active driving VM.
    active = (owner if not uncertain and permitted and phase == "APPLIED"
              and mode in ("AUTO", "MANUAL") and targets[owner] is not None else None)
    return {"active_vm_id": active, "mode": "unknown" if uncertain else mode,
            "authority": {"selected_vm_id": owner, "selected_mode": mode,
                          "generation": generation, "phase": phase,
                          "output_permitted": permitted, "storage_uncertain": uncertain}}


class GatewayReader:
    def __init__(self, settings, *, client=None, clock=time.monotonic, host_boot_id=None):
        # Lazy import: default MQTT installations need no Gateway client package.
        if client is None:
            from xenova_gateway.client import GatewayClient
            client = GatewayClient(settings.gateway_socket, budget_s=settings.gateway_budget_s)
        self.client = client
        self.clock = clock
        self.budget = settings.gateway_budget_s
        self.host_boot_id = canonical(host_boot_id or Path('/proc/sys/kernel/random/boot_id').read_text().strip())
        self.drive_ids = {str(b.vm_id) for b in settings.bindings if b.role != "interaction"}

    def read(self):
        started = self.clock()
        context = self.client.context()  # Existing bounded, root peer-verified UDS client.
        elapsed = self.clock() - started
        if not 0 <= elapsed <= self.budget:
            raise ValueError("Gateway context exceeded observation budget")
        value = normalize(context, host_boot_id=self.host_boot_id, drive_ids=self.drive_ids)
        # Freshness begins at request start, never at a delayed callback/readout.
        return value, started
