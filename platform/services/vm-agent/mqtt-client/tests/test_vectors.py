"""C판(platform/apps/common/xenova-mqtt-c)과 같은 시험 벡터를 Python 구현에 돌린다. 두 구현이 같은 규칙인지의 기준이다."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from xenova_mqtt import envelope as env
from xenova_mqtt import policy, topics

VECTORS = Path(__file__).resolve().parents[4] / "apps/common/xenova-mqtt-c/tests/vectors"


def _load(name: str):
    return json.loads((VECTORS / f"{name}.json").read_text(encoding="utf-8"))


TOPICS = _load("topics")


@pytest.mark.parametrize("case", TOPICS["build"], ids=lambda c: c["out"])
def test_topic_build(case):
    assert getattr(topics, case["fn"])(*case["args"]) == case["out"]


@pytest.mark.parametrize("case", TOPICS["invalid"], ids=lambda c: f"{c['fn']}{c['args']}")
def test_topic_invalid(case):
    with pytest.raises(ValueError):
        getattr(topics, case["fn"])(*case["args"])


@pytest.mark.parametrize("case", TOPICS["parse"], ids=lambda c: c["topic"])
def test_topic_parse(case):
    info = topics.parse(case["topic"])
    assert (info.vm_id, info.app_id) == (case["vm_id"], case["app_id"])


@pytest.mark.parametrize("case", TOPICS["matches"], ids=lambda c: f"{c[0]}~{c[1]}")
def test_topic_matches(case):
    assert topics.matches(case[0], case[1]) is case[2]


@pytest.mark.parametrize("case", _load("policy"), ids=lambda c: c["type"])
def test_policy(case):
    rule = policy.for_type(case["type"])
    assert (rule.qos, rule.retain, rule.max_bytes) == (case["qos"], case["retain"], case["max_bytes"])


@pytest.mark.parametrize("case", _load("validate"), ids=lambda c: c["name"])
def test_validate(case):
    try:
        envelope = json.loads(case["raw"]) if "raw" in case else case["envelope"]
        env.validate(case["topic"], envelope)
        code = None
    except json.JSONDecodeError:
        code = "PAYLOAD_INVALID"  # 클라이언트가 JSON 이 아니면 PAYLOAD_INVALID 로 버린다
    except env.EnvelopeError as error:
        code = error.code
    assert code == case["error"]


@pytest.mark.parametrize("case", _load("seq"), ids=lambda c: c["name"])
def test_seq(case):
    tracker = env.SeqTracker()
    for step in case["steps"]:
        envelope = {"vm_id": step["publisher"], "boot_id": step["boot_id"], "agent_instance_id": step["instance_id"]}
        if step["seq"] is not None:
            envelope["seq"] = step["seq"]
        assert tracker.accept(step["topic"], envelope) is step["accept"], step
