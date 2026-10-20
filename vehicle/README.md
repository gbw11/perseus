# vehicle — 젯슨(차량 쪽) 작업

RC카에 실린 **Jetson Nano(ADAS ECU)** 에서 도는 코드를 둔다. RPi4(dom0·domU) 위에서 도는 것은 `platform/` 에 둔다.

- 역할: D435f 카메라로 장애물·차선·경로를 계산해 RPi4 로 보낸다. 주행 명령은 내지 않는다 ([architecture](../docs/architecture.md)).
- 메시지: 인지 결과 `perception.result` 등은 [MQTT 메시지 계약 v1](../docs/contracts/mqtt-contract-v1.md) 을 따른다.
- 브랜치: part `app` → 기능별 develop 브랜치 `vehicle` ([CONTRIBUTING](../CONTRIBUTING.md) §1·§3).
