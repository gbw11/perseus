"""관측 어댑터: 플랫폼 registry 의도(Manager list/get) + 템플릿 capability + Xen 도메인(xl list).

플랫폼 Manager 의 list·get 은 전역 잠금을 쥐고 VM 마다 Xen 을 관측한다(observe=True, VM 당 최대 15초). 그래서
- 의도는 intent_refresh_s(기본 60초)마다만 list 로 새로 읽어 캐시하고(임시 우회. 그 한 번도 VM 수 × 최대 15초 잠금이다),
- 실행 상태는 매 주기 잠금 없는 xl list 로 읽으며,
- 판정 직전에는 그 VM 만 get 으로 재확인한다(confirm). 캐시가 오래돼도 사용자가 끈 VM 을 재시작하지 않는다.
플랫폼에 "Xen 을 관측하지 않는 list(observe=false)" 또는 잠금 없는 의도 조회가 생기면 캐시 없이 그것을 쓴다(플랫폼 파트 요청, 외부 의존).

Zephyr VM 이 app.heartbeat 를 보내는 펌웨어인지는 템플릿 카탈로그의 capability `app_heartbeat` 로 안다(Manager templates).
그 capability 가 아직 없는 템플릿은 감시기가 신호를 한 번 받은 뒤 armed 로 다룬다(ArmingStore).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any
from uuid import UUID

from ..drivers.platform import ManagerLike, raise_for_envelope, view_to_domain
from ..drivers.xen import XenDriver
from ..drivers.xl import Xl
from ..errors import Dom0ManagerError, VMNotFound
from .model import VmObservation

log = logging.getLogger(__name__)

APP_HEARTBEAT_CAPABILITY = "app_heartbeat"


def _intent(view: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": str(view.get("name") or ""),
        "guest_kind": str(view.get("guest_kind") or "linux"),
        "desired_state": str(view.get("desired_state") or "unknown"),
        "intent_applicable": bool(view.get("intent_applicable")),
        "intent_revision": view.get("intent_revision") if isinstance(view.get("intent_revision"), int) else None,
        "lifecycle": str(view.get("lifecycle") or ""),
        "template": (str(view.get("template_id") or ""), str(view.get("template_version") or "")),
    }


class PlatformObserver:
    def __init__(self, manager: ManagerLike, xl: Xl, *, intent_refresh_s: float = 60.0, clock: Callable[[], float] = time.monotonic) -> None:
        self._manager = manager
        self._xen = XenDriver(xl)
        self._refresh_s = intent_refresh_s
        self._clock = clock
        self._intents: dict[UUID, dict[str, Any]] | None = None
        self._capabilities: dict[tuple[str, str], dict[str, Any]] = {}
        self._capability_errors: set[tuple[str, str]] = set()  # 잘못된 값을 이미 로그한 템플릿
        self._refreshed_at: float | None = None

    # ---- 주기 관측 ----

    def __call__(self) -> list[VmObservation]:
        now = self._clock()
        if self._intents is None or self._refreshed_at is None or now - self._refreshed_at >= self._refresh_s:
            try:
                self._refresh_intents(now)
            except Dom0ManagerError as exc:
                if self._intents is None:
                    raise
                log.warning("intent refresh skipped, keeping cache from %.0fs ago: %s", now - self._refreshed_at, exc.message)
        domains = {d.vm_id: d for d in self._xen.list_domains()}
        return [self._merge(vm_id, intent, domains.get(vm_id)) for vm_id, intent in self._intents.items()]

    def _refresh_intents(self, now: float) -> None:
        views = raise_for_envelope(self._manager.call("list"), context="recovery list").get("vms") or []
        intents: dict[UUID, dict[str, Any]] = {}
        for view in views:
            try:
                intents[UUID(str(view.get("vm_id")))] = _intent(view)
            except ValueError:
                log.warning("registry view without a valid vm_id skipped: %r", view.get("vm_id"))
        try:
            templates = raise_for_envelope(self._manager.call("templates"), context="recovery templates").get("templates") or []
            self._capabilities = {
                (str(t.get("template_id") or t.get("id") or ""), str(t.get("template_version") or t.get("version") or "")): dict(t.get("capabilities") or {})
                for t in templates
            }
        except Dom0ManagerError as exc:  # capability 는 보조 정보다. 없으면 armed 만으로 간다
            log.warning("template capabilities unavailable, keeping previous: %s", exc.message)
        self._intents, self._refreshed_at = intents, now

    def _merge(self, vm_id: UUID, intent: dict[str, Any], domain) -> VmObservation:
        fields = {k: v for k, v in intent.items() if k != "template"}
        # 템플릿이 capability 를 선언하지 않으면 None(모름): 감시기가 armed 기록으로 정한다. 선언했으면 JSON boolean 만 믿는다.
        # "false" 같은 문자열을 bool() 로 바꾸면 True 가 되어 starter 를 앱 대상으로 재시작하게 된다
        template = intent["template"]
        declared = self._capabilities.get(template, {}).get(APP_HEARTBEAT_CAPABILITY)
        error = None
        if declared is not None and not isinstance(declared, bool):
            error = f"template {template[0]}@{template[1]}: capability {APP_HEARTBEAT_CAPABILITY}={declared!r} is not a JSON boolean"
            if template not in self._capability_errors:
                self._capability_errors.add(template)
                log.error("%s; its Zephyr VMs are reported OBSERVATION_UNAVAILABLE until the catalog is fixed", error)
            declared = None
        expected = declared
        return VmObservation(
            vm_id=vm_id,
            present=domain is not None,
            xen_state=domain.state if domain else None,
            domid=domain.domid if domain else None,
            app_heartbeat_expected=expected,
            capability_error=error,
            **fields,
        )

    # ---- 판정 직전 재확인 ----

    def confirm(self, vm_id: UUID) -> VmObservation | None:
        """Manager.get 한 번(잠금 + 이 VM 만 Xen 관측). 등록이 없으면 None. 캐시도 이 결과로 갱신한다."""
        envelope = self._manager.call("get", {"vm_id": str(vm_id)})
        try:
            view = raise_for_envelope(envelope, context=f"recovery confirm {vm_id}").get("vm") or {}
        except VMNotFound:
            if self._intents is not None:
                self._intents.pop(vm_id, None)
            return None
        intent = _intent(view)
        if self._intents is not None:
            self._intents[vm_id] = intent
        return self._merge(vm_id, intent, view_to_domain(view))
