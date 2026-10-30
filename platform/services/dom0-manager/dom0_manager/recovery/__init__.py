"""dom0 Recovery Worker (S15P31E110-124 감시, S15P31E110-125 정책).

자동 복구 주체는 제어 계층이다(VM API 계약 1.2절). Worker 는 dom0-manager HTTP 와 별도 systemd 유닛으로 돌며
heartbeat(MQTT-01)·Xen 도메인 상태·플랫폼 desired_state 를 함께 보고 "VM 재시작이 필요한가"만 판정한다.
차량 제어권 전환(활성→대기 승격)은 게이트웨이의 일이고 여기서 다루지 않는다(주행 명세 3.3).
"""
