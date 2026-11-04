"""관측 어댑터: 플랫폼 registry 의도(Manager intents/get) + 템플릿 capability + Xen 도메인(xl list).

- 의도는 매 주기 Manager intents 로 읽는다. 저장된 의도·등록만 읽고 Xen·공통 잠금을 쓰지 않는다(cli-manager-spec cmd-intents).
  intents 를 모르는 옛 Manager(INVALID_SPEC)면 경고 뒤 list(전역 잠금 + VM 마다 Xen 관측)를 intent_refresh_s 마다 읽는다.
- 의도를 못 읽으면 캐시된 등록은 건강 보고 대상으로만 남기고(등록 삭제로 해석해 retained 를 지우지 않는다) 관측에
  intent_known=False 를 단다. 감시기는 그동안 crashed·absent 를 포함한 모든 판정을 보류한다. 오래된 의도는 복구 허가가 아니다.
- 실행 상태는 매 주기 잠금 없는 xl list 로 읽는다.
- 판정 직전에는 그 VM 만 get 으로 재확인한다(confirm).

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
from ..errors import Dom0ManagerError, PlatformUnavailable, VMNotFound
from .model import VmObservation

log = logging.getLogger(__name__)

APP_HEARTBEAT_CAPABILITY = "app_heartbeat"
# 하위 계약의 허용값(cli-manager-spec cmd-intents). 기본 intents·list 목록은 REGISTERED 만 담는다
DESIRED_STATES = ("running", "stopped", "unknown")
GUEST_KINDS = ("linux", "zephyr")
LIFECYCLES = ("REGISTERED", "RETIRED", "PURGING", "PURGED")  # 단건 get 은 보관 VM 도 돌려줄 수 있다


def _registrations(data: Any, action: str) -> dict[UUID, dict[str, Any]]:
    """성공 응답의 등록 목록 전체를 검증한다. 한 행이라도 잘못되면 부분 적용하지 않고 조회 실패(PlatformUnavailable)다.
    빠진 VM 은 감시기에서 등록 해제로 처리되므로(retained 건강 삭제), 불완전한 응답을 정상 목록으로 확정하면 안 된다."""
    views = data.get("vms") if isinstance(data, dict) else None
    if not isinstance(views, list):
        raise PlatformUnavailable(f"recovery {action}: response has no vms list")
    found: dict[UUID, dict[str, Any]] = {}
    for index, view in enumerate(views):
        problem = _view_problem(view)
        if problem is None:
            vm_id = UUID(view["vm_id"])
            if vm_id in found:
                problem = f"duplicate vm_id {vm_id}"
            else:
                found[vm_id] = _intent(view)
                continue
        raise PlatformUnavailable(f"recovery {action}: registration {index} is invalid ({problem}); the whole response is ignored")
    return found


def _view_problem(view: Any, *, lifecycles: tuple[str, ...] = ("REGISTERED",)) -> str | None:
    """타입뿐 아니라 계약의 허용값과 필드 사이 조건까지 본다. 타입만 맞는 잘못된 값(예: desired_state="banana")도
    현재 의도로 쓰면 열린 episode 를 '대상 아님'으로 닫거나 생존 감시를 건너뛴다. 주기 목록은 REGISTERED 만, 단건 재확인은
    플랫폼 lifecycle 전부를 받는다."""
    if not isinstance(view, dict):
        return "not an object"
    try:
        vm_id = UUID(view.get("vm_id"))
    except (TypeError, ValueError, AttributeError):
        return f"vm_id {view.get('vm_id')!r}"
    if vm_id.int == 0:
        return "nil vm_id"
    if view.get("desired_state") not in DESIRED_STATES:
        return f"desired_state {view.get('desired_state')!r}"
    if view.get("lifecycle") not in lifecycles:
        return f"lifecycle {view.get('lifecycle')!r}"
    if view.get("guest_kind") not in GUEST_KINDS:
        return f"guest_kind {view.get('guest_kind')!r}"
    if not isinstance(view.get("intent_applicable"), bool):
        return f"intent_applicable {view.get('intent_applicable')!r}"
    revision = view.get("intent_revision")
    if revision is not None and (type(revision) is not int or revision < 1):
        return f"intent_revision {revision!r}"
    if view["intent_applicable"] and (view["desired_state"] == "unknown" or revision is None or view["lifecycle"] != "REGISTERED"):
        return (f"applicable intent without a state, revision or registration (desired_state={view['desired_state']!r}, "
                f"intent_revision={revision!r}, lifecycle={view['lifecycle']!r})")
    if not isinstance(view.get("name"), str) or not view["name"]:
        return f"name {view.get('name')!r}"
    # 템플릿은 Zephyr 의 app_heartbeat capability 를 찾는 열쇠라 Zephyr 에는 필수다. Linux 는 판정에 쓰지 않으므로 템플릿이 없는
    # 등록(null)도 받는다. 그런 VM 하나 때문에 전체 의도 조회가 실패해 모든 복구가 보류되지 않게 한다
    for name in ("template_id", "template_version"):
        value = view.get(name)
        if view["guest_kind"] == "zephyr" and (not isinstance(value, str) or not value):
            return f"zephyr {name} {value!r}"
        if value is not None and not isinstance(value, str):
            return f"{name} {value!r}"
    return None


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
    def __init__(self, manager: ManagerLike, xl: Xl, *, intent_refresh_s: float = 60.0, clock: Callable[[], float] = time.monotonic,
                 drive_bindings=None,drive_vm_ids=()) -> None:
        self._manager = manager
        self._xen = XenDriver(xl)
        self._refresh_s = intent_refresh_s
        self._clock = clock
        self._drive_bindings=drive_bindings;self._drive_vm_ids=set(drive_vm_ids)
        self._intents: dict[UUID, dict[str, Any]] | None = None
        self._capabilities: dict[tuple[str, str], dict[str, Any]] = {}
        self._capability_errors: set[tuple[str, str]] = set()  # 잘못된 값을 이미 로그한 템플릿
        self._refreshed_at: float | None = None
        self._templates_at: float | None = None
        self._action = "intents"  # 옛 Manager 면 list 로 바뀐다
        self._intent_known = True  # 마지막 의도 조회가 성공했는가. 실패하면 캐시는 표시용이고 판정 근거가 아니다

    @property
    def intent_known(self) -> bool:
        """마지막 의도 조회가 성공했는가(자가진단용)."""
        return self._intent_known

    # ---- 주기 관측 ----

    def __call__(self) -> list[VmObservation]:
        now = self._clock()
        due = self._action == "intents" or self._refreshed_at is None or now - self._refreshed_at >= self._refresh_s
        if self._intents is None or due:
            try:
                self._refresh_intents(now)
            except Dom0ManagerError as exc:
                if self._intents is None:
                    raise
                if self._intent_known:
                    log.warning("intent %s failed; cached registrations are kept for health reporting only and every judgment is held "
                                "until intents are read again: %s", self._action, exc.message)
                self._intent_known = False
        domains = {d.vm_id: d for d in self._xen.list_domains()}
        return [self._merge(vm_id, intent, domains.get(vm_id), intent_known=self._intent_known) for vm_id, intent in self._intents.items()]

    def _refresh_intents(self, now: float) -> None:
        envelope = self._manager.call(self._action)
        if self._action == "intents" and not envelope.get("ok") and (envelope.get("error") or {}).get("code") == "INVALID_SPEC":
            log.warning("platform manager does not support intents; falling back to list (platform lock + Xen observation) every %.0fs",
                        self._refresh_s)
            self._action = "list"
            envelope = self._manager.call("list")
        intents = _registrations(raise_for_envelope(envelope, context=f"recovery {self._action}"), self._action)
        if self._templates_at is None or now - self._templates_at >= self._refresh_s:
            try:
                templates = raise_for_envelope(self._manager.call("templates"), context="recovery templates").get("templates") or []
                self._capabilities = {
                    (str(t.get("template_id") or t.get("id") or ""), str(t.get("template_version") or t.get("version") or "")): dict(t.get("capabilities") or {})
                    for t in templates
                }
                self._templates_at = now
            except Dom0ManagerError as exc:  # capability 는 보조 정보다. 없으면 armed 만으로 간다. 다음 주기에 다시 읽는다
                log.warning("template capabilities unavailable, keeping previous: %s", exc.message)
        if not self._intent_known:
            log.info("intents read again; judgments resume")
        self._intents, self._refreshed_at, self._intent_known = intents, now, True

    def _merge(self, vm_id: UUID, intent: dict[str, Any], domain, *, intent_known: bool = True) -> VmObservation:
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
        expected = True if str(vm_id) in self._drive_vm_ids else declared
        binding=None;verified=str(vm_id) in self._drive_vm_ids
        if verified:binding=self._drive_bindings.current(str(vm_id),self._clock())
        if binding and (domain is None or binding['domid']!=domain.domid):binding=None
        return VmObservation(
            vm_id=vm_id,
            present=domain is not None,
            xen_state=domain.state if domain else None,
            domid=domain.domid if domain else None,
            app_heartbeat_expected=expected,
            capability_error=error,
            execution_binding_available=bool(binding) if verified else None,
            execution_id=binding['execution_id'] if binding else None,
            expected_app_boot_id=binding['boot_id'] if binding else None,
            expected_app_instance_id=binding['app_instance_id'] if binding else None,
            expected_app_instances=binding.get('app_instances') if binding else None,
            intent_known=intent_known,
            **fields,
        )

    # ---- 판정 직전 재확인 ----

    def confirm(self, vm_id: UUID) -> VmObservation | None:
        """Manager.get 한 번(잠금 + 이 VM 만 Xen 관측). 등록이 없으면 None. 캐시도 이 결과로 갱신한다.
        판정 직전 마지막 확인이라 주기 목록과 같은 검증을 하고 요청한 VM 의 응답인지 본다. 잘못된 응답은 PlatformUnavailable 이고
        캐시를 바꾸지 않는다(감시기는 이번 주기 판정을 보류한다)."""
        envelope = self._manager.call("get", {"vm_id": str(vm_id)})
        try:
            view = raise_for_envelope(envelope, context=f"recovery confirm {vm_id}").get("vm")
        except VMNotFound:
            if self._intents is not None:
                self._intents.pop(vm_id, None)
            return None
        problem = _view_problem(view, lifecycles=LIFECYCLES)
        if problem is None and UUID(view["vm_id"]) != vm_id:
            problem = f"response is for vm {view['vm_id']}"
        if problem is not None:
            raise PlatformUnavailable(f"recovery confirm {vm_id}: invalid get response ({problem})")
        intent = _intent(view)
        if self._intents is not None:
            self._intents[vm_id] = intent
        return self._merge(vm_id, intent, view_to_domain(view))
