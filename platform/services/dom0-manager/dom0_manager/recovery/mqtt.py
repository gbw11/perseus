"""MQTT 연결: 생존 신호 구독과 건강 스냅샷 발행. 공용 클라이언트(xenova_mqtt)는 여기서만 import 한다."""

from __future__ import annotations

import logging
import math
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from typing import Any, Protocol
from uuid import UUID

from .config import HEALTH_LEASE_PERIODS, RecoverySettings
from .heartbeats import AppHeartbeatTable, HeartbeatTable
from .model import HealthState, VmHealth

log = logging.getLogger(__name__)

HEARTBEAT_FILTER = "xenova/v1/vms/+/heartbeat"  # MQTT-01
HEARTBEAT_TYPE = "agent.heartbeat"
APP_EVENTS_FILTER = "xenova/v1/apps/+/events"  # MQTT-06. 그중 type app.heartbeat 만 쓴다
VERIFIED_APP_EVENTS_FILTER = "xenova/v1/vms/+/apps/+/events"
APP_HEARTBEAT_TYPE = "app.heartbeat"
APP_STATES = ("active", "standby", "idle", "error")
# 건강 스냅샷(MQTT-13, 계약 11.1). vms/{vm_id}/ 아래 토픽은 봉투 vm_id 와 토픽이 같아야 해서(계약 2장) VM 이 아닌 발행자는 쓸 수 없다.
# 복구 이벤트(MQTT-12, xenova/v1/recovery/events)와 같은 가지에 둔다
HEALTH_TOPIC_PREFIX = "xenova/v1/recovery/health"
RECOVERY_EVENTS_TOPIC = "xenova/v1/recovery/events"  # MQTT-12. 복구 정책 엔진(125)의 recovery.started/completed/failed
HEALTH_TYPE = "vm.health"
CLEAR_WAIT_S = 2.0  # retained 삭제는 PUBACK 까지 확인한다. 못 받으면 다음 주기에 다시
SHUTDOWN_BUDGET_S = 5.0  # 종료 전 관측 불가 발행 전체의 PUBACK 대기 한도

_HEARTBEAT_TOPIC = re.compile(r"^xenova/v1/vms/([^/]+)/heartbeat$")
_APP_TOPIC = re.compile(r"^xenova/v1/apps/([^/]+)/events$")


class MessageLike(Protocol):
    topic: str
    envelope: dict[str, Any]
    received_monotonic: float


class ClientLike(Protocol):
    connected: bool

    def subscribe(self, topic_filter: str, handler: Any = None, *, qos: int = 1) -> None: ...

    def subscribed(self, topic_filter: str) -> bool: ...

    def publish(self, topic: str, message_type: str, payload: dict[str, Any], *, qos: int | None = None, retain: bool | None = None, wait_s: float | None = None, **kwargs: Any) -> Any: ...

    def clear_retained(self, topic: str, *, wait_s: float | None = None) -> None: ...


@dataclass(frozen=True)
class LinkState:
    """관측 경로가 열려 있는가. 연결(CONNACK)이 아니라 필터별 구독 승인(SUBACK)이다.
    계정은 붙었는데 ACL 이 heartbeat 구독을 거부하면 메시지가 하나도 안 오므로, 그 상태에서 "안 온다"를 판정하면 안 된다.
    승인만으로는 모자란다: Mosquitto 는 읽기 ACL 에 막힌 구독도 SUBACK 성공으로 답하고 전달만 거른다(실보드 10/23 확인).
    그래서 감시기는 승인 뒤 그 채널에서 메시지를 실제로 받은 뒤에야 손실을 판정한다(Watcher._link_ready)."""

    agent: bool  # xenova/v1/vms/+/heartbeat 구독 승인
    app: bool  # xenova/v1/apps/+/events 구독 승인

    @classmethod
    def up(cls) -> "LinkState":
        return cls(agent=True, app=True)

    @classmethod
    def down(cls) -> "LinkState":
        return cls(agent=False, app=False)


def link_state(client: Any,*,verified=False) -> LinkState:
    subscribed = getattr(client, "subscribed", None)
    if not callable(subscribed):
        return LinkState.down()  # 승인 여부를 알 수 없는 클라이언트는 관측 경로를 믿지 않는다
    return LinkState(agent=bool(subscribed(HEARTBEAT_FILTER)), app=bool(subscribed(VERIFIED_APP_EVENTS_FILTER if verified else APP_EVENTS_FILTER)))


def _identity(envelope: dict[str, Any]) -> tuple[UUID, str, int] | None:
    try:
        return UUID(str(envelope["vm_id"])), str(envelope["boot_id"]), int(envelope["seq"])
    except (KeyError, TypeError, ValueError):
        return None


def heartbeat_handler(table: HeartbeatTable):
    """agent.heartbeat 만 생존 신호로 기록한다. 봉투 검사·seq 중복 제거는 공용 클라이언트가 했지만 type·payload·토픽 모양은 여기서 본다.
    heartbeat 토픽에 잘못 올라온 다른 type(agent.metrics 등)이 실제 중단을 가리지 않게 한다. paho 네트워크 스레드에서 불린다."""

    def handle(message: MessageLike) -> None:
        if getattr(message, "retained", False):
            return  # 브로커가 구독 직후 준 옛 값. 실시간 생존 신호가 아니다
        envelope = message.envelope
        match = _HEARTBEAT_TOPIC.match(message.topic)
        identity = _identity(envelope)
        if match is None or identity is None or envelope.get("type") != HEARTBEAT_TYPE or envelope.get("payload") != {}:
            log.warning("heartbeat dropped: topic=%s type=%s payload=%r", message.topic, envelope.get("type"), envelope.get("payload"))
            return
        vm_id, boot_id, seq = identity
        if str(vm_id) != match.group(1):
            log.warning("heartbeat dropped: topic vm %s != envelope vm %s", match.group(1), vm_id)
            return
        table.record(vm_id, boot_id, seq, message.received_monotonic)

    return handle


def _valid_app_payload(payload: Any) -> str | None:
    """계약 6.1: state 허용값, uptime_s 는 0 이상의 유한한 수. 맞으면 state 를 돌려준다."""
    if not isinstance(payload, dict):
        return None
    state, uptime = payload.get("state"), payload.get("uptime_s")
    if state not in APP_STATES or isinstance(uptime, bool) or not isinstance(uptime, (int, float)):
        return None
    if not math.isfinite(uptime) or uptime < 0:
        return None
    return str(state)


def app_heartbeat_handler(table: AppHeartbeatTable,*,bindings=None,vm_ids=()):
    """apps/{app_id}/events 중 app.heartbeat 만. 봉투의 vm_id 로 VM 에 묶는다(VM 이 발행하므로 vm_id 와 app_id 가 함께 있다)."""

    def handle(message: MessageLike) -> None:
        if getattr(message, "retained", False):
            return  # 브로커가 구독 직후 준 옛 값. 실시간 생존 신호가 아니다
        envelope = message.envelope
        if envelope.get("type") != APP_HEARTBEAT_TYPE:
            return  # 같은 토픽의 drive.command·perception.event 등은 여기 관심사가 아니다
        match = _APP_TOPIC.match(message.topic)
        identity = _identity(envelope)
        state = _valid_app_payload(envelope.get("payload"))
        if match is None or identity is None or state is None or envelope.get("app_id") != match.group(1):
            log.warning("app.heartbeat dropped: topic=%s app_id=%s payload=%r", message.topic, envelope.get("app_id"), envelope.get("payload"))
            return
        vm_id, boot_id, seq = identity
        if str(vm_id) in vm_ids:return  # configured verified VMs cannot bypass through legacy topics
        table.record(vm_id, match.group(1), state, boot_id, seq, message.received_monotonic)

    return handle


def subscribe_liveness(client: ClientLike, heartbeats: HeartbeatTable, app_heartbeats: AppHeartbeatTable,*,rejoin=None) -> None:
    client.subscribe(HEARTBEAT_FILTER, heartbeat_handler(heartbeats), qos=1)
    client.subscribe(APP_EVENTS_FILTER, app_heartbeat_handler(app_heartbeats,vm_ids=rejoin.vm_ids if rejoin else ()), qos=0)
    if rejoin is not None:
        if hasattr(client,'set_receive_guard'):client.set_receive_guard(rejoin.cache.guard(rejoin.vm_ids))
        elif getattr(rejoin,'config',{}).get('readiness_profile')=='warm-drive-1':raise ValueError('warm profile requires pre-sequence binding guard')
        def verified(message):
            from xenova_mqtt import topics
            info=topics.parse(message.topic);env=message.envelope
            if (getattr(message,'retained',False) or info.vm_id not in rejoin.vm_ids or
                info.app_id!=env.get('app_id') or info.vm_id!=env.get('vm_id') or
                not rejoin.cache.accepts(info.vm_id,env.get('boot_id'),env.get('agent_instance_id'),message.received_monotonic,app=info.app_id)):return
            warm=getattr(rejoin,'config',{}).get('readiness_profile')=='warm-drive-1'
            if message.type=='app.lifecycle' and warm:
                from xenova_mqtt.lifecycle import parse
                try:value=parse(message.payload)
                except (ValueError,TypeError):return
                rejoin.on_readiness(message)
                app_heartbeats.record_lifecycle(UUID(info.vm_id),info.app_id,env['boot_id'],env['agent_instance_id'],value,message.received_monotonic)
            elif message.type=='app.readiness' and not warm:rejoin.on_readiness(message)
            elif message.type=='app.heartbeat':
                state=_valid_app_payload(message.payload)
                if state:app_heartbeats.record(UUID(info.vm_id),info.app_id,state,env['boot_id'],env['seq'],
                    message.received_monotonic,instance_id=env['agent_instance_id'])
        client.subscribe(VERIFIED_APP_EVENTS_FILTER,verified,qos=0)


def health_topic(vm_id: UUID) -> str:
    return f"{HEALTH_TOPIC_PREFIX}/{vm_id}"


class HealthPublisher:
    """retained 건강 스냅샷(계약 11.1).

    내용이 바뀌면(manager_seq) 바로 내고, 같아도 refresh_s 마다 다시 낸다. payload 의 lease_s(= 3 × refresh_s)는 받는 쪽이 이 값을
    믿어도 되는 시간이다: 그 안에 다시 받지 못하면 Worker 가 죽었거나 브로커와 끊긴 것이므로 관측 불가로 본다. retained 만 믿으면
    며칠 전 HEALTHY 가 현재로 읽힌다. 등록에서 사라진 VM 은 빈 payload 로 지우고, Worker 가 멈출 때는 전부 관측 불가로 바꾼다.
    """

    def __init__(self, client: ClientLike, *, refresh_s: float = 15.0, clock: Callable[[], float] = time.monotonic) -> None:
        self._client = client
        self._refresh_s = refresh_s
        self._lease_s = HEALTH_LEASE_PERIODS * refresh_s
        self._clock = clock
        self._sent: dict[UUID, tuple[int, float]] = {}  # vm_id → (manager_seq, 보낸 단조 시각)
        self._to_clear: set[UUID] = set()  # 지우지 못한 retained. 다음 주기에 다시 시도

    @property
    def lease_s(self) -> float:
        return self._lease_s

    def publish(self, health: VmHealth, now: float | None = None, *, wait_s: float = 0.0) -> bool:
        """wait_s=0 은 paho 가 알린 실패(rc)만 검사한다. 0 보다 크면 PUBACK 까지 기다린다."""
        now = self._clock() if now is None else now
        sent = self._sent.get(health.vm_id)
        if sent is not None and sent[0] == health.manager_seq and now - sent[1] < self._refresh_s:
            return False
        if not self._client.connected:
            return False  # 끊긴 동안 큐에 쌓지 않는다. 받는 쪽은 lease 만료로 관측 불가를 안다
        try:
            self._client.publish(health_topic(health.vm_id), HEALTH_TYPE, health.payload(lease_s=self._lease_s), qos=1, retain=True, wait_s=wait_s)
        except Exception as exc:  # noqa: BLE001 - rc 실패·PUBACK 미수신 포함. 판정을 막지 않고 다음 주기에 다시 낸다
            log.warning("health publish failed for %s: %s", health.vm_id, exc)
            return False
        self._sent[health.vm_id] = (health.manager_seq, now)
        self._to_clear.discard(health.vm_id)
        return True

    def clear(self, vm_id: UUID) -> bool:
        """등록에서 사라진 VM: retained 를 지운다(빈 payload, QoS 1, PUBACK 확인). 못 지우면 기억해 두고 retry_clears() 가 다시 시도한다."""
        self._sent.pop(vm_id, None)
        self._to_clear.add(vm_id)
        if not self._client.connected:
            return False
        try:
            self._client.clear_retained(health_topic(vm_id), wait_s=CLEAR_WAIT_S)
        except Exception as exc:  # noqa: BLE001 - rc 실패·PUBACK 미수신 포함
            log.warning("health clear failed for %s: %s", vm_id, exc)
            return False
        self._to_clear.discard(vm_id)
        return True

    def retry_clears(self) -> None:
        for vm_id in list(self._to_clear):
            self.clear(vm_id)

    def publish_unavailable(self, healths: Iterable[VmHealth], now: float | None = None, *, budget_s: float = SHUTDOWN_BUDGET_S) -> int:
        """Worker 가 멈출 때. 남은 retained 가 HEALTHY 로 읽히지 않도록 관측 불가를 내고 PUBACK 까지 확인한다(전부 합쳐 budget_s 안에).
        받는 쪽은 lease 만료로도 같은 결론에 이르지만, 확인된 것만 센다."""
        deadline = self._clock() + budget_s
        count = 0
        for health in healths:
            gone = replace(health, state=HealthState.OBSERVATION_UNAVAILABLE, heartbeat_age_s=None, reason=None, manager_seq=health.manager_seq + 1)
            if self.publish(gone, now, wait_s=max(0.0, deadline - self._clock())):
                count += 1
        return count


def build_client(settings: RecoverySettings) -> Any:
    """xenova_mqtt.XenovaClient 를 source_id 신원으로 만든다. paho·xenova_mqtt 는 여기서만 필요하다."""
    from xenova_mqtt import Identity, XenovaClient

    return XenovaClient(
        Identity(source_id=settings.source_id),
        host=settings.broker_host,
        port=settings.broker_port,
        username=settings.broker_username,
        password=settings.broker_password,
    )
