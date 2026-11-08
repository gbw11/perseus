"""paho-mqtt 래퍼. 봉투 부착·검사, type 별 QoS, 재연결 후 재구독, latest-only 보관을 맡는다.

Ubuntu 24.04 apt 패키지는 paho-mqtt 1.6, Debian 13 은 2.x 라 두 콜백 형식을 모두 받는다.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import paho.mqtt.client as mqtt

from . import envelope as env
from . import policy, topics

log = logging.getLogger("xenova_mqtt")


@dataclass(frozen=True)
class Message:
    """검사를 통과한 수신 메시지."""

    topic: str
    envelope: dict[str, Any]
    # 받는 쪽 단조 시계. 신선도는 보드 간 시각(sent_at)이 아니라 이 값으로 잰다 (계약 1장)
    received_monotonic: float = field(default_factory=time.monotonic)
    # 브로커가 구독 직후 준 마지막 값(MQTT retain 플래그). 실시간 수신이 아니고 저장된 지 얼마나 됐는지도 알 수 없으므로
    # 신선도·생존의 근거로 쓰지 않는다. 받는 쪽은 실시간 값(retained=False)을 받은 뒤 확정한다 (계약 11.1)
    retained: bool = False

    @property
    def type(self) -> str:
        return self.envelope["type"]

    @property
    def payload(self) -> dict[str, Any]:
        return self.envelope["payload"]

    def age_ms(self) -> float:
        return (time.monotonic() - self.received_monotonic) * 1000.0


@dataclass(frozen=True)
class PublishTicket:
    """Local submission/PUBACK observation; never an app/device acknowledgement."""
    envelope: dict[str, Any]
    info: Any
    connections: int
    disconnect_generation: int

    def status(self,client: "XenovaClient") -> str:
        if not client.connected or client.connections!=self.connections or client._generation!=self.disconnect_generation:
            return "disconnected"
        if self.info.rc!=mqtt.MQTT_ERR_SUCCESS:return "failed"
        try:return "acknowledged" if self.info.is_published() else "pending"
        except (ValueError,RuntimeError):return "failed"


Handler = Callable[[Message], None]


def _new_paho(client_id: str) -> mqtt.Client:
    kwargs: dict[str, Any] = {"client_id": client_id, "clean_session": True, "protocol": mqtt.MQTTv311}
    if hasattr(mqtt, "CallbackAPIVersion"):  # paho 2.x
        kwargs["callback_api_version"] = mqtt.CallbackAPIVersion.VERSION2
    return mqtt.Client(**kwargs)


def _failed(reason_code: Any) -> bool:
    # paho 2.x 는 ReasonCode, 1.6 은 int
    return bool(getattr(reason_code, "is_failure", reason_code != 0))


def _suback_failed(code: Any) -> bool:
    """SUBACK 한 항목. paho 1.6 은 granted_qos 정수(0x80 이면 거부), 2.x 는 ReasonCode."""
    failed = getattr(code, "is_failure", None)
    if failed is not None:
        return bool(failed)
    try:
        return int(code) >= 0x80
    except (TypeError, ValueError):
        return True


SUB_PENDING = "pending"  # 요청했고 SUBACK 대기
SUB_GRANTED = "granted"
SUB_DENIED = "denied"  # 브로커(ACL)가 거부
EARLY_SUBACK_LIMIT = 32  # mid 를 등록하기 전에 도착한 SUBACK 을 잠시 들고 있는 한도
PENDING_PUBLISH_LIMIT = 256  # PUBACK 을 기다리는 발행을 기억하는 한도. stop() 이 끊기 전에 기다린다


class PublishError(RuntimeError):
    """발행이 브로커에 전달되지 않았다(paho rc 실패 또는 PUBACK 대기 초과). wait_s 를 준 호출에서만 난다."""


class XenovaClient:
    """계약 v1 MQTT 클라이언트.

    >>> client = XenovaClient(Identity(vm_id="..."), host="10.0.0.1")
    >>> client.start()
    >>> client.publish(topics.heartbeat(vm_id), "agent.heartbeat", {})
    """

    def __init__(
        self,
        identity: env.Identity,
        host: str = "127.0.0.1",
        port: int = 1883,
        *,
        username: str | None = None,
        password: str | None = None,
        client_id: str | None = None,
        keepalive: int = 30,
        reconnect_min_s: int = 1,
        reconnect_max_s: int = 10,
    ) -> None:
        self.identity = identity
        self._host = host
        self._port = port
        self._keepalive = keepalive
        self._seq: dict[str, int] = {}
        self._subscriptions: dict[str, tuple[int, list[Handler]]] = {}
        # 구독 승인 상태. 연결(CONNACK)과 구독 승인(SUBACK)은 다르다: 계정은 붙었는데 ACL 이 필터를 거부하면 메시지가 하나도 안 온다.
        # 받는 쪽이 "안 온다"를 판정하려면 connected 가 아니라 subscribed(filter) 를 봐야 한다
        self._sub_state: dict[str, str] = {}
        self._pending_mids: dict[int, str] = {}
        # subscribe() 가 mid 를 돌려주기 전에 네트워크 스레드가 SUBACK 을 먼저 처리할 수 있다. 그 SUBACK 은 mid 로 잠시 보관했다가
        # 요청한 쪽이 mid 를 알게 되면 가져간다. paho 의 subscribe() 를 우리 잠금 안에서 부르면 SUBACK 콜백(paho 콜백 잠금을 쥔 채
        # 우리 잠금을 기다림)과 맞물릴 수 있어 잠금을 걸쳐 두지 않는다
        self._early_subacks: dict[int, Any] = {}
        self._generation = 0  # 연결 세대. 끊길 때마다 +1. 끊기기 전에 보낸 요청의 mid 는 등록하지 않는다 (재연결 때 다시 요청)
        self._connections = 0  # 연결에 성공한 횟수. 두 번 확인하는 사이에 끊겼다 다시 붙어도 값이 바뀐다
        self._connection_attempts = 0  # CONNACK 를 받은 연결 시도 수(거부 포함). TCP 가 닿지 않은 시도는 paho 안에서 끝나 세지 못한다
        self._inflight = 0  # 이 세대에서 subscribe() 가 진행 중인 요청 수. 0 이면 모르는 mid 의 SUBACK 은 보관하지 않고 버린다
        self._pending_infos: list[Any] = []  # PUBACK 을 아직 받지 못한 QoS>0 발행. stop() 이 끊기 전에 기다린다
        self._latest: dict[str, Message] = {}
        self._tracker = env.SeqTracker()
        self._receive_guard = None
        self._lock = threading.Lock()
        self._connected = threading.Event()
        self._stopping = False

        self._paho = _new_paho(client_id or f"{identity.publisher}-{identity.agent_instance_id[:8]}")
        if username:
            self._paho.username_pw_set(username, password)
        self._paho.reconnect_delay_set(min_delay=reconnect_min_s, max_delay=reconnect_max_s)
        self._paho.on_connect = self._on_connect
        self._paho.on_disconnect = self._on_disconnect
        self._paho.on_subscribe = self._on_subscribe
        self._paho.on_message = self._on_message

    # --- 연결 ---------------------------------------------------------------

    def start(self, wait_s: float | None = None) -> bool:
        """백그라운드 루프를 시작한다. 브로커가 없어도 실패하지 않고 계속 재시도한다.

        wait_s 를 주면 그 시간 안에 연결됐는지 돌려준다.
        """
        self._stopping = False
        self._paho.connect_async(self._host, self._port, self._keepalive)
        self._paho.loop_start()
        return self.wait_connected(wait_s) if wait_s is not None else self.connected

    def stop(self, flush_s: float = 1.0) -> None:
        """끊고 루프를 멈춘다. PUBACK 을 받지 못한 QoS 1 발행이나 소켓에 쓰지 못한 패킷이 있으면 flush_s 까지 기다린다."""
        deadline = time.monotonic() + flush_s
        while self._connected.is_set() and time.monotonic() < deadline:
            with self._lock:
                self._pending_infos = [info for info in self._pending_infos if not info.is_published()]
                pending = len(self._pending_infos)
            if pending == 0 and not self._paho.want_write():
                break
            time.sleep(0.02)
        self._stopping = True
        self._paho.disconnect()
        self._paho.loop_stop()
        self._connected.clear()

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    @property
    def connections(self) -> int:
        """브로커 연결에 성공한 횟수. 값이 바뀌었으면 그 사이 (재)연결이 있었다: clean session 이라 브로커가 이 연결의 상태를 모르고,
        브로커가 재시작했다면 retained 도 잃었을 수 있다. connected 만 보면 짧은 끊김을 놓친다."""
        return self._connections

    @property
    def connection_attempts(self) -> int:
        """CONNACK 를 받은 연결 시도 수(거부 포함). v2 Telemetry heartbeat 의 connection_attempts_total."""
        return self._connection_attempts

    def wait_connected(self, timeout_s: float) -> bool:
        return self._connected.wait(timeout_s)

    def _on_connect(self, client: mqtt.Client, userdata: Any, flags: Any, reason_code: Any, *_: Any) -> None:
        with self._lock:
            self._connection_attempts += 1
        if _failed(reason_code):
            log.warning("MQTT 연결 거부: %s", reason_code)
            return
        # clean session 이라 브로커가 구독을 잊는다. 연결될 때마다 다시 건다.
        # 먼저 연결 표시를 해야 그 사이 subscribe() 로 들어온 필터도 빠지지 않는다.
        self._connected.set()
        with self._lock:
            self._connections += 1
            subscriptions = [(topic_filter, qos) for topic_filter, (qos, _) in self._subscriptions.items()]
        for topic_filter, qos in subscriptions:
            self._request_subscribe(client, topic_filter, qos)
        log.info("MQTT 연결 %s:%s (구독 %d개 복구)", self._host, self._port, len(subscriptions))

    def _on_disconnect(self, client: mqtt.Client, userdata: Any, *args: Any) -> None:
        self._connected.clear()
        with self._lock:  # 재연결하면 구독을 다시 요청하고 SUBACK 을 다시 받아야 한다
            self._sub_state = {topic_filter: SUB_PENDING for topic_filter in self._subscriptions}
            self._pending_mids.clear()
            self._early_subacks.clear()
            self._inflight = 0
            self._generation += 1
        if not self._stopping:
            log.warning("MQTT 연결 끊김, 재연결 대기")

    def _request_subscribe(self, client: mqtt.Client, topic_filter: str, qos: int) -> None:
        with self._lock:
            self._sub_state[topic_filter] = SUB_PENDING
            generation = self._generation
            self._inflight += 1
        try:
            result = client.subscribe(topic_filter, qos)
        except Exception:
            with self._lock:
                if generation == self._generation:
                    self._inflight -= 1
                    if self._inflight == 0:
                        self._early_subacks.clear()  # 정상 경로와 같다. 실패한 요청 중에 보관한 SUBACK 을 다음 요청이 쓰면 안 된다
            raise
        mid = result[1] if isinstance(result, tuple) and len(result) > 1 else getattr(result, "mid", None)
        with self._lock:
            if generation != self._generation:
                return  # 그 사이 끊겼다(inflight 는 _on_disconnect 가 비웠다). 재연결하면 _on_connect 가 다시 요청한다
            self._inflight -= 1
            codes = None if mid is None else self._early_subacks.pop(int(mid), None)
            if self._inflight == 0:
                self._early_subacks.clear()  # 남은 것은 어느 요청의 응답도 아니다. 다음 요청이 같은 mid 를 받아도 쓰지 않는다
            if mid is None:
                return
            if codes is None:
                self._pending_mids[int(mid)] = topic_filter
                return
            denied, items = self._apply_suback(topic_filter, codes)  # SUBACK 이 먼저 와 있었다
        self._log_suback(topic_filter, denied, items)

    def _on_subscribe(self, client: mqtt.Client, userdata: Any, mid: int, codes: Any, *_: Any) -> None:
        """SUBACK. paho 1.6: (mid, granted_qos), 2.x: (mid, reason_codes, properties)."""
        with self._lock:
            topic_filter = self._pending_mids.pop(int(mid), None)
            if topic_filter is None:
                if self._inflight > 0:  # 요청한 쪽이 아직 mid 를 등록하지 못했을 수 있다. 등록할 때 가져가도록 보관한다
                    self._early_subacks[int(mid)] = codes
                    while len(self._early_subacks) > EARLY_SUBACK_LIMIT:
                        del self._early_subacks[next(iter(self._early_subacks))]
                return  # 진행 중인 요청이 없으면 모르는 mid 다(이전 연결의 늦은 응답 등). 버린다
            denied, items = self._apply_suback(topic_filter, codes)
        self._log_suback(topic_filter, denied, items)

    def _apply_suback(self, topic_filter: str, codes: Any) -> tuple[bool, list[Any]]:
        """잠금 안에서 부른다. 승인·거부를 기록하고 (거부 여부, SUBACK 항목) 을 돌려준다."""
        items = list(codes) if isinstance(codes, (list, tuple)) else [codes]
        denied = any(_suback_failed(code) for code in items) or not items
        self._sub_state[topic_filter] = SUB_DENIED if denied else SUB_GRANTED
        return denied, items

    @staticmethod
    def _log_suback(topic_filter: str, denied: bool, items: list[Any]) -> None:
        if denied:
            log.error("MQTT 구독 거부 %s: %s (계정·ACL 확인)", topic_filter, items)
        else:
            log.info("MQTT 구독 승인 %s", topic_filter)

    def subscription_state(self, topic_filter: str) -> str | None:
        """pending / granted / denied, 구독한 적 없으면 None."""
        with self._lock:
            return self._sub_state.get(topic_filter)

    def subscribed(self, topic_filter: str) -> bool:
        """연결돼 있고 브로커가 이 필터의 구독을 승인했다. 생존 신호가 "안 온다"를 판정하는 쪽은 connected 가 아니라 이것을 본다."""
        return self.connected and self.subscription_state(topic_filter) == SUB_GRANTED

    # --- 발행 ---------------------------------------------------------------

    def publish(
        self,
        topic: str,
        message_type: str,
        payload: dict[str, Any],
        *,
        qos: int | None = None,
        retain: bool | None = None,
        app_id: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        wait_s: float | None = None,
    ) -> dict[str, Any]:
        """봉투를 씌워 발행하고 보낸 봉투를 돌려준다. QoS·retain 은 생략하면 type 별 계약값.

        연결이 끊긴 동안 QoS 0 메시지는 버려지고, QoS 1 은 paho 가 들고 있다가 재연결 후 보낸다.
        paho 는 실패를 예외가 아니라 반환값(rc)으로 알린다. wait_s 가 None 이면 그 실패는 경고만 남긴다(기존 호출자 호환).
        wait_s 를 주면 rc 실패를 PublishError 로 올리고, 0 보다 크면 그 시간까지 PUBACK(QoS 1)을 기다려 못 받아도 PublishError 다.
        전달이 중요한 발행(retained 삭제, 종료 전 상태)에 쓴다.
        """
        rule = policy.for_message(message_type,payload)
        app = app_id or topics.parse(topic).app_id
        with self._lock:
            seq = self._seq.get(topic, 0) + 1
            self._seq[topic] = seq
        envelope = env.build(
            self.identity, message_type, payload, seq,
            app_id=app, correlation_id=correlation_id, causation_id=causation_id,
        )
        data = json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(data) > rule.max_bytes:
            raise ValueError(f"{message_type} 메시지 {len(data)} bytes > 한도 {rule.max_bytes}")
        info = self._paho.publish(
            topic, data,
            qos=rule.qos if qos is None else qos,
            retain=rule.retain if retain is None else retain,
        )
        self._track_publish(info, topic, wait_s)
        return envelope

    def publish_raw(self, topic: str, data: bytes, *, qos: int, retain: bool, wait_s: float | None = None) -> None:
        """봉투를 씌우지 않고 그대로 발행한다. v1 봉투가 아닌 자기 계약을 가진 메시지용(v2 Telemetry, xenova_agent.telemetry).

        v1 type 정책·seq·크기 한도를 적용하지 않으므로 호출자가 자기 계약의 QoS·retain·크기를 지킨다.
        끊긴 동안의 동작과 wait_s 의 뜻은 publish() 와 같다.
        """
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("publish_raw takes encoded bytes")
        info = self._paho.publish(topic, bytes(data), qos=qos, retain=retain)
        self._track_publish(info, topic, wait_s)

    def clear_retained(self, topic: str, *, wait_s: float | None = None) -> None:
        """retained 메시지를 지운다 (빈 payload + retain). 예: VM 삭제 시 inventory. wait_s 는 publish() 와 같다."""
        info = self._paho.publish(topic, b"", qos=1, retain=True)
        self._track_publish(info, topic, wait_s)

    def publish_tracked(self,topic: str,message_type: str,payload: dict[str,Any]) -> PublishTicket:
        """Nonblocking verified-profile submission. Offline calls are refused.

        A disconnect after submission is uncertain: Paho may replay a QoS1
        frame. Consumers must validate authority/generation/application; this
        method does not claim queued bytes were cancelled.
        """
        if not self.connected:raise PublishError('publisher disconnected before submission')
        rule=policy.for_message(message_type,payload)
        with self._lock:
            seq=self._seq.get(topic,0)+1;self._seq[topic]=seq
            generation,connections=self._generation,self._connections
        envelope=env.build(self.identity,message_type,payload,seq,app_id=topics.parse(topic).app_id)
        data=json.dumps(envelope,ensure_ascii=False,separators=(',',':')).encode()
        if len(data)>rule.max_bytes:raise ValueError('message exceeds policy limit')
        info=self._paho.publish(topic,data,qos=rule.qos,retain=rule.retain)
        self._track_publish(info,topic,0)
        return PublishTicket(envelope,info,connections,generation)

    def _track_publish(self, info: Any, topic: str, wait_s: float | None) -> None:
        """paho 의 MQTTMessageInfo 를 본다. 끊긴 동안 QoS 1 은 rc=NO_CONN 이지만 큐에 남아 재연결 뒤 나간다."""
        rc = getattr(info, "rc", mqtt.MQTT_ERR_SUCCESS)
        if rc != mqtt.MQTT_ERR_SUCCESS:
            if wait_s is not None:
                raise PublishError(f"{topic}: rc={rc} ({mqtt.error_string(rc)})")
            log.warning("발행 %s: rc=%s (%s)", topic, rc, mqtt.error_string(rc))
            return
        if not hasattr(info, "is_published"):
            return
        if wait_s is not None and wait_s > 0:
            try:
                info.wait_for_publish(timeout=wait_s)
            except Exception as exc:  # noqa: BLE001 - paho 는 큐 초과·연결 종료를 여기서 예외로 올린다
                raise PublishError(f"{topic}: {exc}") from exc
            if not info.is_published():
                raise PublishError(f"{topic}: no PUBACK within {wait_s:g}s")
            return
        with self._lock:
            self._pending_infos = [i for i in self._pending_infos if not i.is_published()][-PENDING_PUBLISH_LIMIT:]
            if not info.is_published():
                self._pending_infos.append(info)

    # --- 구독 ---------------------------------------------------------------

    def subscribe(self, topic_filter: str, handler: Handler | None = None, *, qos: int = 1) -> None:
        """토픽 필터를 구독한다. handler 는 paho 네트워크 스레드에서 불리므로 오래 막지 않는다.

        handler 없이 구독하면 latest() 로만 읽는다 (주행 토픽 latest-only).
        """
        with self._lock:
            _, handlers = self._subscriptions.get(topic_filter, (qos, []))
            if handler is not None:
                handlers.append(handler)
            self._subscriptions[topic_filter] = (qos, handlers)
            self._sub_state.setdefault(topic_filter, SUB_PENDING)
        if self.connected:
            self._request_subscribe(self._paho, topic_filter, qos)

    def latest(self, topic: str) -> Message | None:
        """토픽별 마지막으로 받은 메시지. 신선도는 Message.age_ms() 로 판정한다."""
        with self._lock:
            return self._latest.get(topic)

    def set_receive_guard(self,guard) -> None:
        """Install once before start. Memory-only guard runs before seq/latest mutation.

        This does not authenticate credentials; trusted consumers use it to bind
        an authenticated VM topic to the platform's current allowed execution.
        """
        with self._lock:
            if self._connected.is_set() or self._receive_guard is not None:raise ValueError('receive guard already active')
            self._receive_guard=guard

    def _on_message(self, client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        # Paho callback 진입 시각을 보존해 검증·guard 대기를 새 신선도로 빌리지 않는다.
        received_monotonic = time.monotonic()
        if not msg.payload:
            return  # retained 삭제 알림
        if len(msg.payload) > policy.MAX_BYTES:
            log.warning("버림 %s: PAYLOAD_TOO_LARGE %d bytes", msg.topic, len(msg.payload))
            return
        try:
            envelope = json.loads(msg.payload.decode("utf-8"))
            env.validate(msg.topic, envelope)
            if envelope['type'] in ('app.state.snapshot','app.state.applied'):
                from .handoff import decode_complete,snapshot,applied
                try:
                    envelope=decode_complete(msg.payload)
                    (snapshot if envelope['type']=='app.state.snapshot' else applied)(envelope['payload'])
                except ValueError:
                    log.warning('state message rejected: invalid complete state')
                    return
        except (UnicodeDecodeError, json.JSONDecodeError):
            log.warning("버림 %s: PAYLOAD_INVALID JSON 아님", msg.topic)
            return
        except RecursionError:
            log.warning('버림 %s: PAYLOAD_INVALID JSON nesting limit',msg.topic)
            return
        except env.EnvelopeError as error:
            log.warning("버림 %s: %s", msg.topic, error)
            return
        if len(msg.payload) > policy.for_message(envelope["type"],envelope.get('payload')).max_bytes:
            log.warning("버림 %s: PAYLOAD_TOO_LARGE %s %d bytes", msg.topic, envelope["type"], len(msg.payload))
            return
        guard=self._receive_guard
        if guard is not None:
            try:
                if not guard(msg.topic,envelope,bool(getattr(msg,'retain',False))):return
            except Exception:
                log.warning('receive binding guard unavailable; message rejected');return
        if not self._tracker.accept(msg.topic, envelope):
            log.debug("버림 %s: seq %s 중복·역전", msg.topic, envelope.get("seq"))
            return

        message = Message(msg.topic, envelope, received_monotonic=received_monotonic,
                          retained=bool(getattr(msg, "retain", False)))
        with self._lock:
            self._latest[msg.topic] = message
            handlers = [
                handler
                for topic_filter, (_, registered) in self._subscriptions.items()
                if topics.matches(topic_filter, msg.topic)
                for handler in registered
            ]
        for handler in handlers:
            try:
                handler(message)
            except Exception:  # 핸들러 오류로 수신 루프가 멈추면 안 된다
                log.exception("핸들러 오류 %s", msg.topic)
