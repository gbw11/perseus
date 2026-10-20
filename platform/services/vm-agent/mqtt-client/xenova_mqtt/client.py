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

    @property
    def type(self) -> str:
        return self.envelope["type"]

    @property
    def payload(self) -> dict[str, Any]:
        return self.envelope["payload"]

    def age_ms(self) -> float:
        return (time.monotonic() - self.received_monotonic) * 1000.0


Handler = Callable[[Message], None]


def _new_paho(client_id: str) -> mqtt.Client:
    kwargs: dict[str, Any] = {"client_id": client_id, "clean_session": True, "protocol": mqtt.MQTTv311}
    if hasattr(mqtt, "CallbackAPIVersion"):  # paho 2.x
        kwargs["callback_api_version"] = mqtt.CallbackAPIVersion.VERSION2
    return mqtt.Client(**kwargs)


def _failed(reason_code: Any) -> bool:
    # paho 2.x 는 ReasonCode, 1.6 은 int
    return bool(getattr(reason_code, "is_failure", reason_code != 0))


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
        self._latest: dict[str, Message] = {}
        self._tracker = env.SeqTracker()
        self._lock = threading.Lock()
        self._connected = threading.Event()
        self._stopping = False

        self._paho = _new_paho(client_id or f"{identity.publisher}-{identity.agent_instance_id[:8]}")
        if username:
            self._paho.username_pw_set(username, password)
        self._paho.reconnect_delay_set(min_delay=reconnect_min_s, max_delay=reconnect_max_s)
        self._paho.on_connect = self._on_connect
        self._paho.on_disconnect = self._on_disconnect
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

    def stop(self) -> None:
        self._stopping = True
        self._paho.disconnect()
        self._paho.loop_stop()
        self._connected.clear()

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    def wait_connected(self, timeout_s: float) -> bool:
        return self._connected.wait(timeout_s)

    def _on_connect(self, client: mqtt.Client, userdata: Any, flags: Any, reason_code: Any, *_: Any) -> None:
        if _failed(reason_code):
            log.warning("MQTT 연결 거부: %s", reason_code)
            return
        # clean session 이라 브로커가 구독을 잊는다. 연결될 때마다 다시 건다.
        # 먼저 연결 표시를 해야 그 사이 subscribe() 로 들어온 필터도 빠지지 않는다.
        self._connected.set()
        with self._lock:
            subscriptions = [(topic_filter, qos) for topic_filter, (qos, _) in self._subscriptions.items()]
        for topic_filter, qos in subscriptions:
            client.subscribe(topic_filter, qos)
        log.info("MQTT 연결 %s:%s (구독 %d개 복구)", self._host, self._port, len(subscriptions))

    def _on_disconnect(self, client: mqtt.Client, userdata: Any, *args: Any) -> None:
        self._connected.clear()
        if not self._stopping:
            log.warning("MQTT 연결 끊김, 재연결 대기")

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
    ) -> dict[str, Any]:
        """봉투를 씌워 발행하고 보낸 봉투를 돌려준다. QoS·retain 은 생략하면 type 별 계약값.

        연결이 끊긴 동안 QoS 0 메시지는 버려지고, QoS 1 은 paho 가 들고 있다가 재연결 후 보낸다.
        """
        rule = policy.for_type(message_type)
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
        self._paho.publish(
            topic, data,
            qos=rule.qos if qos is None else qos,
            retain=rule.retain if retain is None else retain,
        )
        return envelope

    def clear_retained(self, topic: str) -> None:
        """retained 메시지를 지운다 (빈 payload + retain). 예: VM 삭제 시 inventory."""
        self._paho.publish(topic, b"", qos=1, retain=True)

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
        if self.connected:
            self._paho.subscribe(topic_filter, qos)

    def latest(self, topic: str) -> Message | None:
        """토픽별 마지막으로 받은 메시지. 신선도는 Message.age_ms() 로 판정한다."""
        with self._lock:
            return self._latest.get(topic)

    def _on_message(self, client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        if not msg.payload:
            return  # retained 삭제 알림
        if len(msg.payload) > policy.MAX_BYTES:
            log.warning("버림 %s: PAYLOAD_TOO_LARGE %d bytes", msg.topic, len(msg.payload))
            return
        try:
            envelope = json.loads(msg.payload.decode("utf-8"))
            env.validate(msg.topic, envelope)
        except (UnicodeDecodeError, json.JSONDecodeError):
            log.warning("버림 %s: PAYLOAD_INVALID JSON 아님", msg.topic)
            return
        except env.EnvelopeError as error:
            log.warning("버림 %s: %s", msg.topic, error)
            return
        if len(msg.payload) > policy.for_type(envelope["type"]).max_bytes:
            log.warning("버림 %s: PAYLOAD_TOO_LARGE %s %d bytes", msg.topic, envelope["type"], len(msg.payload))
            return
        if not self._tracker.accept(msg.topic, envelope):
            log.debug("버림 %s: seq %s 중복·역전", msg.topic, envelope.get("seq"))
            return

        message = Message(msg.topic, envelope)
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
