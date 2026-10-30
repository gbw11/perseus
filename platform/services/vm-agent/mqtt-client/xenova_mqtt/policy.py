"""메시지 type 별 QoS·retain·크기 기본값 (계약 1·3·5·6·7·8장 표).

같은 토픽에 QoS 가 다른 type 이 섞이므로(예: apps/autonomous/events 의 drive.command 0, perception.event 1)
토픽이 아니라 type 으로 정한다.
"""

from __future__ import annotations

from dataclasses import dataclass

MAX_BYTES = 64 * 1024
MAX_DRIVING_BYTES = 1024


@dataclass(frozen=True)
class Policy:
    qos: int
    retain: bool = False
    # 주행 토픽: 1 KB 제한, 받는 쪽은 최신 1건만 쓴다 (latest-only)
    driving: bool = False

    @property
    def max_bytes(self) -> int:
        return MAX_DRIVING_BYTES if self.driving else MAX_BYTES


POLICIES: dict[str, Policy] = {
    # 3장 에이전트
    "agent.heartbeat": Policy(qos=1),
    "agent.inventory": Policy(qos=1, retain=True),
    "agent.metrics": Policy(qos=0),
    "agent.command": Policy(qos=1),
    "agent.command_result": Policy(qos=1),
    # 5장 주행 제어
    "drive.command": Policy(qos=0, driving=True),
    "perception.result": Policy(qos=0),
    "vehicle.control": Policy(qos=1, retain=True, driving=True),
    "vehicle.status": Policy(qos=0, driving=True),
    # 6장 앱 이벤트
    "perception.event": Policy(qos=1),
    "message.posted": Policy(qos=1),
    "drive.resume.requested": Policy(qos=1),
    "drive.mode.changed": Policy(qos=1),
    "drive.fault.detected": Policy(qos=1),
    # 6.1 앱 생존 신호: 1초마다 다시 오므로 유실돼도 다음 것으로 충분하다
    "app.heartbeat": Policy(qos=0),
    "app.readiness": Policy(qos=0, driving=True),
    # 7장 수동 입력
    "drive.input": Policy(qos=0),
    # 8장 지연 측정
    "probe.ping": Policy(qos=1),
    "probe.pong": Policy(qos=1),
    # 12장 시뮬레이터 전용 (실차에는 없음)
    "sim.pose": Policy(qos=0, driving=True),
    "sim.world": Policy(qos=1, retain=True),
    "sim.command": Policy(qos=1),
    "sim.command.result": Policy(qos=1),
}

# 계약 표에 없는 type. 유실보다 중복이 낫다고 보고 QoS 1 로 둔다.
DEFAULT = Policy(qos=1)


def for_type(message_type: str) -> Policy:
    return POLICIES.get(message_type, DEFAULT)
