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
| `XENOVA_AGENT_HEARTBEAT_INTERVAL` | `5` | 초. 계약값은 5이고 환경변수로는 1~10만 받는다 (받는 쪽이 15초에 STALE, 30초에 OFFLINE 판정) |
| `XENOVA_AGENT_LOG_LEVEL` | `INFO` | DEBUG/INFO/WARNING/ERROR/CRITICAL |

vm_id 는 소문자·하이픈 표준형으로 정규화해 토픽과 봉투에 씁니다(Spring·registry 문자열과 글자까지 같아야 함). UUID 가 아니거나 nil UUID(Domain-0)이거나, 주기·포트·로그 레벨이 범위 밖이면 시작하지 않고 종료 코드 2 로 끝납니다.

## 실행

```bash
# 개발 PC (브로커 없이도 시작되고 연결을 재시도한다)
cd services/vm-agent/agent
PYTHONPATH=.:../mqtt-client XENOVA_AGENT_VM_ID=11111111-1111-4111-8111-111111111111 XENOVA_AGENT_BROKER_HOST=127.0.0.1 python -m xenova_agent

# 게스트 rootfs 에 설치 (템플릿 준비 단계에서 플랫폼 파트가 부른다)
python3 services/vm-agent/agent/deploy/install_into_rootfs.py <rootfs>
```

SIGTERM·SIGINT 를 받으면 현재 대기를 끊고 클라이언트를 닫은 뒤 종료합니다.

## 게스트 배포

`deploy/install_into_rootfs.py <rootfs>`가 두 패키지를 `/opt/xenova/mqtt-client`·`/opt/xenova/agent`에 복사하고 `xenova-agent.service`를 설치해 enable 합니다. 템플릿 준비(플랫폼 파트, `prepare_rootfs.py`)에서 해야 할 일은 세 가지입니다.

1. `apt install python3-paho-mqtt` (Ubuntu 24.04 1.6.1, Debian 13 2.1.0). 스크립트는 rootfs 에 paho 가 없으면 경고하고 종료 코드 1 을 냅니다
2. 위 스크립트 호출
3. 브로커 계정이 생기면 `/etc/xenova/agent.env`에 `XENOVA_AGENT_BROKER_USERNAME/PASSWORD` 기록 (없으면 생략)

유닛은 `Requires=xenova-guest-identity.service`라 신원 확립에 실패한 VM 은 heartbeat 를 보내지 않고, `/etc/xenova/instance-id`가 없으면 시작하지 않습니다. Zephyr 템플릿에는 설치하지 않습니다.

## 테스트

```bash
cd services/vm-agent/agent
python -m pytest
```

가짜 시계와 가짜 클라이언트로 주기·지연 보정·끊김 건너뜀·발행 실패·정지를 확인합니다. 실제 브로커 시험은 mqtt-client 의 `PERSEUS_MQTT_TEST_BROKER` 방식을 따릅니다.
