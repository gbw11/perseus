"""Xenova VM 에이전트.

Linux 게스트 안에서 돌며 MQTT 계약 v1(docs/contracts/mqtt-contract-v1.md)의 heartbeat(MQTT-01)·inventory(MQTT-02)·
metrics(MQTT-03)를 dom0 Mosquitto 로 보낸다. 봉투·토픽·QoS·재연결은 platform/services/vm-agent/mqtt-client 의 xenova_mqtt 가 맡고,
이 패키지는 수집과 주기·수명을 정한다.
"""

__version__ = "0.3.0"
