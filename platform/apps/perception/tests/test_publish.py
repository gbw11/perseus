from __future__ import annotations

import json

import pytest

from xenova_mqtt import envelope as env
from xenova_mqtt import policy, topics
from xenova_perception.__main__ import parse_args
from xenova_perception.config import PerceptionSettings
from xenova_perception.geometry import Lane, Result
from xenova_perception.publisher import TOPIC, LatestResult, Publisher
from xenova_perception.synthetic import SyntheticSource


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeClient:
    """XenovaClient.publish 와 같은 봉투를 만들어 보관한다."""

    def __init__(self) -> None:
        self.identity = env.Identity(source_id="jetson", app_id="perception")
        self.sent: list[tuple[str, dict, int | None, bool | None]] = []
        self.seq = 0

    def publish(self, topic, message_type, payload, *, qos=None, retain=None, **_):
        self.seq += 1
        envelope = env.build(self.identity, message_type, payload, self.seq, app_id=topics.parse(topic).app_id)
        self.sent.append((topic, envelope, qos, retain))
        return envelope


def run_for(publisher, clock, seconds, step=0.001):
    for _ in range(round(seconds / step)):
        clock.now += step
        publisher.tick()


def test_topic_is_contract_topic():
    assert TOPIC == "xenova/v1/apps/perception/events"


def test_publishes_15_hz_with_fake_clock():
    clock, client = Clock(), FakeClient()
    pub = Publisher(client, SyntheticSource(PerceptionSettings(), clock=clock), 15.0, clock=clock)
    run_for(pub, clock, 10.0)
    assert len(client.sent) == pytest.approx(150, abs=1)
    assert pub.skipped == 0


def test_rate_holds_when_ticks_are_jittery():
    # 4 ms 와 9 ms 간격이 섞여도 마감 시각 누적이라 평균 15 Hz
    clock, client = Clock(), FakeClient()
    pub = Publisher(client, SyntheticSource(PerceptionSettings(), clock=clock), 15.0, clock=clock)
    for i in range(1540):
        clock.now += 0.004 if i % 2 else 0.009
        pub.tick()
    assert len(client.sent) == pytest.approx(150, abs=1)


def test_envelope_and_qos_match_contract():
    clock, client = Clock(), FakeClient()
    pub = Publisher(client, SyntheticSource(PerceptionSettings(), clock=clock), 15.0, clock=clock)
    run_for(pub, clock, 0.2)
    topic, envelope, qos, retain = client.sent[0]
    assert topic == TOPIC
    assert qos == 0 and retain is False
    assert policy.for_type("perception.result").qos == 0
    env.validate(topic, envelope)  # 받는 쪽 검사 통과
    assert envelope["source_id"] == "jetson" and "vm_id" not in envelope
    assert envelope["app_id"] == "perception"
    assert envelope["type"] == "perception.result"
    assert set(envelope["payload"]) == {"lane_offset_m", "heading_err_rad", "obstacles", "stop"}
    assert len(json.dumps(envelope).encode()) < 1024


def test_latest_result_republishes_until_stale_then_skips():
    clock, client = Clock(), FakeClient()
    latest = LatestResult(stale_s=0.5)
    pub = Publisher(client, latest, 15.0, clock=clock)
    run_for(pub, clock, 0.3)
    assert client.sent == [] and pub.skipped > 0  # 아직 프레임 없음
    latest.put(Result(lane=Lane(0.1, 0.0), at=clock.now))
    run_for(pub, clock, 0.4)
    sent_fresh = len(client.sent)
    assert sent_fresh == pytest.approx(6, abs=1)  # 새 프레임 없이도 같은 결과를 15 Hz 로 다시 낸다
    run_for(pub, clock, 1.0)
    assert len(client.sent) <= sent_fresh + 2  # 0.5 s 넘으면 멈춘다
    latest.put(Result(at=clock.now))
    run_for(pub, clock, 0.2)
    assert len(client.sent) > sent_fresh + 2  # 프레임이 돌아오면 다시 낸다


def test_parse_args_reads_env_and_cli_overrides():
    env_vars = {
        "XENOVA_PERCEPTION_BROKER_HOST": "10.0.0.5",
        "XENOVA_PERCEPTION_BROKER_PORT": "1884",
        "XENOVA_PERCEPTION_BROKER_PASSWORD": "from-env",
    }
    s = parse_args([], env_vars)
    assert (s.broker_host, s.broker_port, s.username, s.password) == ("10.0.0.5", 1884, "perception", "from-env")
    s = parse_args(["--host", "127.0.0.1", "--username", ""], env_vars)
    assert s.broker_host == "127.0.0.1" and s.username is None
    assert parse_args([], {}).password is None


def test_video_source_needs_path():
    with pytest.raises(ValueError):
        parse_args(["--source", "video"], {})
