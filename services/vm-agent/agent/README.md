# xenova-agent — VM 에이전트 (heartbeat)

Linux 게스트 안에서 돌며 [MQTT 계약 v1](../../../docs/contracts/mqtt-contract-v1.md)의 heartbeat(MQTT-01)를 dom0 Mosquitto 로 보냅니다.
봉투·토픽·QoS·자동 재연결은 [mqtt-client](../mqtt-client/README.md)의 `perseus_mqtt`가 맡고, 이 패키지는 주기와 수명만 정합니다.

| 항목 | 값 |
| --- | --- |
| 토픽 | `perseus/v1/vms/{vm_id}/heartbeat`, `type: agent.heartbeat`, QoS 1 |
| payload | `{}`. 봉투의 `seq`·`boot_id`·`agent_instance_id`·`sent_at`만 의미가 있음 |
| 주기 | 5초. 시작 직후 1회, 이후 단조 시계 기준 `start + n*5s` (발행 지연이 누적되지 않음) |
| 끊김 | 브로커와 끊긴 틱은 보내지 않고 건너뜀. 재연결은 클라이언트가 하고 연결되면 다음 틱부터 재개 |
| 상태 | 에이전트는 상태를 선언하지 않음. 살아 있는지는 받는 쪽이 수신 시각으로 판정 |

앱·프로세스 상태는 heartbeat에 싣지 않고 metrics(MQTT-03)의 `processes[]`로 보냅니다(S15P31E110-122). Zephyr 는 에이전트·네트워크가 없어 대상이 아닙니다.

## 설정

| 환경변수 | 기본값 | 설명 |
| --- | --- | --- |
| `XENOVA_AGENT_VM_ID` | `/etc/xenova/instance-id` 내용 | 이 VM의 UUID. 플랫폼이 게스트 준비 때 파일로 기록한다 |
| `XENOVA_AGENT_BROKER_HOST` / `PORT` | `192.168.77.1` / `1883` | dom0 xenbr0 의 Mosquitto |
| `XENOVA_AGENT_BROKER_USERNAME` / `PASSWORD` | (없음) | 브로커 계정이 생기면 설정 (`deploy/`의 ACL) |
| `XENOVA_AGENT_HEARTBEAT_INTERVAL` | `5` | 초. 계약값이므로 시험 외에는 바꾸지 않는다 |
| `XENOVA_AGENT_LOG_LEVEL` | `INFO` | |

vm_id 가 UUID 가 아니거나, 주기가 유한한 양수가 아니거나, 포트가 범위 밖이면 시작하지 않고 종료 코드 2 로 끝납니다.

## 실행

```bash
# 개발 PC (브로커 없이도 시작되고 연결을 재시도한다)
cd services/vm-agent/agent
PYTHONPATH=.:../mqtt-client XENOVA_AGENT_VM_ID=11111111-1111-4111-8111-111111111111 XENOVA_AGENT_BROKER_HOST=127.0.0.1 python -m xenova_agent

# 게스트 (systemd)
# deploy/xenova-agent.service 를 /usr/lib/systemd/system/ 에 두고, perseus_mqtt 와 xenova_agent 를 PYTHONPATH 경로에 설치한다.
# 설치 경로와 템플릿 반영은 플랫폼 파트와 정한다.
```

SIGTERM·SIGINT 를 받으면 현재 대기를 끊고 클라이언트를 닫은 뒤 종료합니다.

## 테스트

```bash
cd services/vm-agent/agent
python -m pytest
```

가짜 시계와 가짜 클라이언트로 주기·지연 보정·끊김 건너뜀·발행 실패·정지를 확인합니다. 실제 브로커 시험은 mqtt-client 의 `PERSEUS_MQTT_TEST_BROKER` 방식을 따릅니다.
