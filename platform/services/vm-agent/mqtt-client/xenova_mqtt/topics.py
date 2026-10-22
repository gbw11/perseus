"""MQTT 계약 v1 토픽 빌더와 파서 (docs/contracts/mqtt-contract-v1.md 3·5·6·7·8·11장)."""

from __future__ import annotations

import re
from dataclasses import dataclass

PREFIX = "xenova/v1"

VM_KINDS = ("heartbeat", "inventory", "metrics", "command", "command-result")

# 토픽 한 단계에 들어갈 수 없는 문자. 와일드카드·구분자가 섞이면 다른 VM 토픽을 건드린다.
_SEGMENT = re.compile(r"^[^/+#\s]+$")


def _segment(name: str, value: str) -> str:
    if not isinstance(value, str) or not _SEGMENT.match(value):
        raise ValueError(f"{name} 는 '/', '+', '#', 공백 없는 문자열이어야 한다: {value!r}")
    return value


def vm(vm_id: str, kind: str) -> str:
    """에이전트 토픽 `xenova/v1/vms/{vm_id}/{kind}` (MQTT-01~05)."""
    if kind not in VM_KINDS:
        raise ValueError(f"알 수 없는 에이전트 토픽 종류: {kind}")
    return f"{PREFIX}/vms/{_segment('vm_id', vm_id)}/{kind}"


def heartbeat(vm_id: str) -> str:
    return vm(vm_id, "heartbeat")


def inventory(vm_id: str) -> str:
    return vm(vm_id, "inventory")


def metrics(vm_id: str) -> str:
    return vm(vm_id, "metrics")


def command(vm_id: str) -> str:
    return vm(vm_id, "command")


def command_result(vm_id: str) -> str:
    return vm(vm_id, "command-result")


def app_events(app_id: str) -> str:
    """앱 이벤트 `xenova/v1/apps/{app_id}/events` (MQTT-06)."""
    return f"{PREFIX}/apps/{_segment('app_id', app_id)}/events"


def manual_input() -> str:
    """백엔드 → 수동 VM 키 입력 (MQTT-11)."""
    return f"{PREFIX}/apps/manual/input"


def vehicle_control() -> str:
    return f"{PREFIX}/vehicle/control"


def vehicle_status() -> str:
    return f"{PREFIX}/vehicle/status"


def sim_pose() -> str:
    """시뮬레이터 차 위치 (MQTT-15, 실차에는 없음)."""
    return f"{PREFIX}/sim/pose"


def sim_world() -> str:
    """시뮬레이터 트랙·장애물·E-stop, retained (MQTT-16)."""
    return f"{PREFIX}/sim/world"


def sim_command() -> str:
    """백엔드 → 시뮬레이터 조작 명령 (MQTT-17)."""
    return f"{PREFIX}/sim/command"


def probe_request(target_vm_id: str) -> str:
    return f"{PREFIX}/probes/{_segment('target_vm_id', target_vm_id)}/request"


def probe_reply(source_vm_id: str) -> str:
    return f"{PREFIX}/probes/{_segment('source_vm_id', source_vm_id)}/reply"


@dataclass(frozen=True)
class TopicInfo:
    """토픽에서 읽어낸 식별자. 봉투의 같은 필드와 대조할 때 쓴다."""

    vm_id: str | None = None
    app_id: str | None = None


_VM = re.compile(rf"^{PREFIX}/vms/([^/]+)/[^/]+$")
# apps/manual/input 은 백엔드가 보내는 입력이라 app_id 대조 대상이 아니다 (events 만)
_APP = re.compile(rf"^{PREFIX}/apps/([^/]+)/events$")


def parse(topic: str) -> TopicInfo:
    """토픽에서 vm_id·app_id 를 꺼낸다. 계약 밖 토픽이면 빈 TopicInfo."""
    if match := _VM.match(topic):
        return TopicInfo(vm_id=match.group(1))
    if match := _APP.match(topic):
        return TopicInfo(app_id=match.group(1))
    return TopicInfo()


def matches(subscription: str, topic: str) -> bool:
    """MQTT 와일드카드(`+`, `#`) 구독이 토픽에 맞는지."""
    sub_parts = subscription.split("/")
    topic_parts = topic.split("/")
    for index, part in enumerate(sub_parts):
        if part == "#":
            return True
        if index >= len(topic_parts):
            return False
        if part != "+" and part != topic_parts[index]:
            return False
    return len(sub_parts) == len(topic_parts)
