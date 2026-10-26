"""XenovaClient 의 구독 승인(SUBACK) 추적. 브로커 없이 가짜 paho 로 콜백을 직접 부른다."""

import json
from types import SimpleNamespace

import pytest

from xenova_mqtt import Identity, PublishError, XenovaClient
from xenova_mqtt import client as client_module
from xenova_mqtt import envelope as env

HEALTH_TOPIC = "xenova/v1/recovery/health/4be41d5f-b8ef-408b-9709-7fe5ac11231f"


class FakePaho:
    def __init__(self):
        self.subscribed = []
        self.next_mid = 10
        self.published = []
        self.infos = []
        self.publish_rc = 0  # paho 는 실패를 반환값 rc 로 알린다
        self.ack = True  # PUBACK 이 바로 온 것으로 본다

    def username_pw_set(self, *a, **k):
        pass

    def reconnect_delay_set(self, **k):
        pass

    def subscribe(self, topic_filter, qos):
        self.next_mid += 1
        self.subscribed.append((topic_filter, qos, self.next_mid))
        return (0, self.next_mid)

    def connect_async(self, *a, **k):
        pass

    def loop_start(self):
        pass

    def loop_stop(self):
        pass

    def disconnect(self):
        pass

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, qos, retain))
        info = SimpleNamespace(rc=self.publish_rc, done=self.ack)
        info.is_published = lambda: info.done
        info.wait_for_publish = lambda timeout=None: None
        self.infos.append(info)
        return info

    def want_write(self):
        return False


@pytest.fixture
def client(monkeypatch):
    fake = FakePaho()
    monkeypatch.setattr(client_module, "_new_paho", lambda client_id: fake)
    c = XenovaClient(Identity(source_id="dom0-server"))
    c._fake = fake
    return c


def test_subscribed_requires_connack_and_suback(client):
    client.subscribe("xenova/v1/vms/+/heartbeat", qos=1)
    assert client.subscription_state("xenova/v1/vms/+/heartbeat") == "pending" and client.subscribed("xenova/v1/vms/+/heartbeat") is False
    client._on_connect(client._fake, None, {}, 0)  # CONNACK: 구독 요청만 나간다
    assert client.connected and client.subscribed("xenova/v1/vms/+/heartbeat") is False
    (topic_filter, qos, mid), = client._fake.subscribed
    client._on_subscribe(client._fake, None, mid, (1,))  # paho 1.6 granted_qos
    assert client.subscribed("xenova/v1/vms/+/heartbeat") is True


def test_denied_subscription_is_reported_not_granted(client):
    client.subscribe("xenova/v1/vms/+/heartbeat", qos=1)
    client.subscribe("xenova/v1/apps/+/events", qos=0)
    client._on_connect(client._fake, None, {}, 0)
    (_, _, mid_hb), (_, _, mid_app) = client._fake.subscribed
    client._on_subscribe(client._fake, None, mid_hb, (0x80,))  # ACL 거부 (1.6)
    client._on_subscribe(client._fake, None, mid_app, [SimpleNamespace(is_failure=False)], None)  # 2.x ReasonCode 승인
    assert client.subscription_state("xenova/v1/vms/+/heartbeat") == "denied" and client.subscribed("xenova/v1/vms/+/heartbeat") is False
    assert client.subscribed("xenova/v1/apps/+/events") is True
    client._on_subscribe(client._fake, None, 999, (0,))  # 모르는 mid 는 무시
    assert client.subscription_state("xenova/v1/apps/+/events") == "granted"


def test_reconnect_resets_grants_until_new_suback(client):
    client.subscribe("xenova/v1/vms/+/heartbeat", qos=1)
    client._on_connect(client._fake, None, {}, 0)
    client._on_subscribe(client._fake, None, client._fake.subscribed[-1][2], (1,))
    assert client.subscribed("xenova/v1/vms/+/heartbeat") is True
    client._on_disconnect(client._fake, None, 0)
    assert client.connected is False and client.subscription_state("xenova/v1/vms/+/heartbeat") == "pending"
    client._on_connect(client._fake, None, {}, 0)  # 재연결: 다시 요청, 승인 전까지 False
    assert client.subscribed("xenova/v1/vms/+/heartbeat") is False
    client._on_subscribe(client._fake, None, client._fake.subscribed[-1][2], [SimpleNamespace(is_failure=False)], None)
    assert client.subscribed("xenova/v1/vms/+/heartbeat") is True


def test_subscribe_while_connected_requests_immediately(client):
    client._on_connect(client._fake, None, {}, 0)
    client.subscribe("xenova/v1/apps/+/events", qos=0)
    assert client._fake.subscribed[-1][:2] == ("xenova/v1/apps/+/events", 0)
    assert client.subscription_state("xenova/v1/apps/+/events") == "pending"


class EagerSubackPaho(FakePaho):
    """네트워크 스레드가 subscribe() 가 mid 를 돌려주기 전에 SUBACK 을 처리한 경우."""

    def __init__(self):
        super().__init__()
        self.owner = None
        self.codes = (1,)

    def subscribe(self, topic_filter, qos):
        result = super().subscribe(topic_filter, qos)
        self.owner._on_subscribe(self, None, result[1], self.codes)
        return result


def test_suback_processed_before_mid_is_registered_is_not_lost(monkeypatch):
    fake = EagerSubackPaho()
    monkeypatch.setattr(client_module, "_new_paho", lambda client_id: fake)
    c = XenovaClient(Identity(source_id="dom0-server"))
    fake.owner = c
    c._on_connect(fake, None, {}, 0)
    c.subscribe("xenova/v1/apps/+/events", qos=0)  # 연결 중 동적 구독: SUBACK 이 먼저 처리됐어도 승인으로 기록된다
    assert c.subscribed("xenova/v1/apps/+/events") is True
    assert c._pending_mids == {} and c._early_subacks == {}
    fake.codes = (0x80,)
    c.subscribe("xenova/v1/vms/+/heartbeat", qos=1)
    assert c.subscription_state("xenova/v1/vms/+/heartbeat") == "denied"


def test_disconnect_during_subscribe_request_does_not_register_stale_mid(monkeypatch):
    class DropPaho(FakePaho):
        def subscribe(self, topic_filter, qos):
            result = super().subscribe(topic_filter, qos)
            owner._on_disconnect(self, None, 0)  # 요청이 나간 직후 끊김
            return result

    fake = DropPaho()
    monkeypatch.setattr(client_module, "_new_paho", lambda client_id: fake)
    owner = XenovaClient(Identity(source_id="dom0-server"))
    owner._on_connect(fake, None, {}, 0)
    owner.subscribe("xenova/v1/vms/+/heartbeat", qos=1)
    assert owner._pending_mids == {} and owner.subscription_state("xenova/v1/vms/+/heartbeat") == "pending"
    stale_mid = fake.subscribed[-1][2]
    fake.__class__ = FakePaho  # 재연결 뒤에는 보통대로
    owner._on_connect(fake, None, {}, 0)
    owner._on_subscribe(fake, None, stale_mid, (1,))  # 이전 연결의 mid 는 승인으로 치지 않는다
    assert owner.subscribed("xenova/v1/vms/+/heartbeat") is False
    owner._on_subscribe(fake, None, fake.subscribed[-1][2], (1,))
    assert owner.subscribed("xenova/v1/vms/+/heartbeat") is True


def test_unknown_suback_without_inflight_request_is_discarded_even_if_mid_is_reused(client):
    client._on_connect(client._fake, None, {}, 0)
    client._on_subscribe(client._fake, None, 999, (0,))  # 진행 중인 요청이 없다: 이전 연결의 늦은 응답 등. 버린다
    assert client._early_subacks == {}
    client._fake.next_mid = 998
    client.subscribe("xenova/v1/vms/+/heartbeat", qos=1)  # 같은 mid 999 를 받아도 옛 결과를 승인으로 쓰지 않는다
    assert client._fake.subscribed[-1][2] == 999 and client.subscription_state("xenova/v1/vms/+/heartbeat") == "pending"
    client._on_subscribe(client._fake, None, 999, (0x80,))
    assert client.subscription_state("xenova/v1/vms/+/heartbeat") == "denied"


def test_early_subacks_are_kept_only_while_a_request_is_in_flight(monkeypatch):
    class NoisyPaho(EagerSubackPaho):
        def subscribe(self, topic_filter, qos):
            result = FakePaho.subscribe(self, topic_filter, qos)
            self.owner._on_subscribe(self, None, 777, (0,))  # 요청 중에 온 모르는 mid 는 잠시 보관되지만
            self.owner._on_subscribe(self, None, result[1], self.codes)
            return result

    fake = NoisyPaho()
    monkeypatch.setattr(client_module, "_new_paho", lambda client_id: fake)
    c = XenovaClient(Identity(source_id="dom0-server"))
    fake.owner = c
    c._on_connect(fake, None, {}, 0)
    c.subscribe("xenova/v1/apps/+/events", qos=0)
    assert c.subscribed("xenova/v1/apps/+/events") is True
    assert c._early_subacks == {} and c._inflight == 0  # 요청이 끝나면 남은 보관분은 버린다


def test_subscribe_failure_discards_early_subacks_kept_during_the_request(monkeypatch):
    class FailingPaho(EagerSubackPaho):
        def subscribe(self, topic_filter, qos):
            self.next_mid += 1
            self.owner._on_subscribe(self, None, 555, (0,))  # 요청 중에 온 모르는 mid
            raise ValueError("broker refused the request")

    fake = FailingPaho()
    monkeypatch.setattr(client_module, "_new_paho", lambda client_id: fake)
    c = XenovaClient(Identity(source_id="dom0-server"))
    fake.owner = c
    c._on_connect(fake, None, {}, 0)
    with pytest.raises(ValueError):
        c.subscribe("xenova/v1/vms/+/heartbeat", qos=1)
    assert c._early_subacks == {} and c._inflight == 0  # 예외 경로도 정상 경로처럼 보관분을 버린다
    fake.__class__ = FakePaho
    fake.next_mid = 554
    c.subscribe("xenova/v1/apps/+/events", qos=0)  # 같은 mid 555 를 받아도 옛 SUBACK 을 쓰지 않는다
    assert fake.subscribed[-1][2] == 555 and c.subscription_state("xenova/v1/apps/+/events") == "pending"


def test_publish_reports_paho_rc_failure_and_missing_puback_only_when_asked(client):
    client._fake.publish_rc = 4  # MQTT_ERR_NO_CONN
    assert client.publish(HEALTH_TOPIC, "vm.health", {"ok": True}, qos=1, retain=True)["type"] == "vm.health"  # 호환: 경고만
    with pytest.raises(PublishError):
        client.publish(HEALTH_TOPIC, "vm.health", {"ok": True}, qos=1, retain=True, wait_s=0)
    with pytest.raises(PublishError):
        client.clear_retained(HEALTH_TOPIC, wait_s=0)
    client._fake.publish_rc = 0
    client._fake.ack = False
    with pytest.raises(PublishError):  # rc 는 성공이지만 PUBACK 이 오지 않음
        client.publish(HEALTH_TOPIC, "vm.health", {"ok": True}, qos=1, retain=True, wait_s=0.01)
    client.clear_retained(HEALTH_TOPIC, wait_s=0)  # rc 만 검사
    assert client._fake.published[-1][1:] == (b"", 1, True)


def test_stop_waits_for_unacknowledged_publishes_until_deadline(client, monkeypatch):
    sleeps = []
    monkeypatch.setattr(client_module.time, "sleep", lambda s: sleeps.append(s))
    client._on_connect(client._fake, None, {}, 0)
    client._fake.ack = False
    client.publish(HEALTH_TOPIC, "vm.health", {"ok": False}, qos=1, retain=True)
    assert len(client._pending_infos) == 1
    client.stop(flush_s=0.05)
    assert sleeps and client.connected is False  # PUBACK 을 기다리다 기한에 끊었다
    sleeps.clear()
    client._fake.infos[-1].done = True
    client._fake.ack = True
    client._on_connect(client._fake, None, {}, 0)
    client.publish(HEALTH_TOPIC, "vm.health", {"ok": True}, qos=1, retain=True)
    client.stop(flush_s=0.05)
    assert sleeps == []  # 전부 확인됐으면 바로 끊는다


def test_message_exposes_retain_flag(client):
    envelope = env.build(Identity(source_id="dom0-server"), "vm.health", {"ok": True}, 1)
    client._on_message(client._fake, None, SimpleNamespace(topic=HEALTH_TOPIC, payload=json.dumps(envelope).encode(), retain=True))
    assert client.latest(HEALTH_TOPIC).retained is True
    envelope = env.build(Identity(source_id="dom0-server"), "vm.health", {"ok": True}, 2)
    client._on_message(client._fake, None, SimpleNamespace(topic=HEALTH_TOPIC, payload=json.dumps(envelope).encode(), retain=False))
    assert client.latest(HEALTH_TOPIC).retained is False


def test_connections_counts_successful_connects_so_a_fast_reconnect_is_visible(client):
    assert client.connections == 0
    client._on_connect(client._fake, None, {}, 0)
    assert client.connections == 1 and client.connected
    client._on_disconnect(client._fake, None, 0)
    client._on_connect(client._fake, None, {}, 0)  # 두 확인 사이에 끊겼다 다시 붙음: connected 는 그대로 True
    assert client.connections == 2 and client.connected
    client._on_connect(client._fake, None, {}, 135)  # 거부된 CONNACK 은 세지 않는다
    assert client.connections == 2
