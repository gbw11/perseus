# xenova-agent — VM 에이전트 (heartbeat)

Linux 게스트 안에서 돌며 [MQTT 계약 v1](../../../docs/contracts/mqtt-contract-v1.md)의 heartbeat(MQTT-01)를 dom0 Mosquitto 로 보냅니다.
봉투·토픽·QoS·자동 재연결은 [mqtt-client](../mqtt-client/README.md)의 `perseus_mqtt`가 맡고, 이 패키지는 주기와 수명만 정합니다.

| 항목 | 값 |
| --- | --- |
| 토픽 | `perseus/v1/vms/{vm_id}/heartbeat`, `type: agent.heartbeat`, QoS 1 |
| payload | `{}`. 봉투의 `seq`·`boot_id`·`agent_instance_id`·`sent_at`만 의미가 있음 |
| 주기 | 5초. 시작 직후 1회, 이후 단조 시계 기준 `start + n*5s` (발행 지연이 누적되지 않음) |
| 끊김 | 브로커와 끊긴 틱은 보내지 않고 건너뜀. 재연결은 클라이언트가 하고 연결되면 다음 틱부터 재개 |
| 발행 예외 | 봉투·토픽·라이브러리 사용 오류처럼 고정된 문제라 보고, 연속 3번이면 종료 코드 1로 끝남. systemd 가 로그에 남기고 2초 뒤 다시 띄움 |
| 연결 거부 | 잘못된 계정처럼 브로커가 연결을 거부하면 클라이언트가 경고만 남기고 재시도하므로 에이전트는 "연결 없음"으로만 본다. 영구 거부를 구분하려면 공용 mqtt-client 가 거부 사유를 드러내야 하며 별도 후속 작업이다 |
| 상태 | 에이전트는 상태를 선언하지 않음. 살아 있는지는 받는 쪽이 수신 시각으로 판정 |
| 권한 | root uid 지만 capability 전부 제거, `ProtectSystem=strict`·`PrivateTmp` 등으로 쓰기·권한 상승 차단. 전용 사용자는 instance-id(root 0600) 소유권을 플랫폼과 정한 뒤 |

앱·프로세스 상태는 heartbeat에 싣지 않고 metrics(MQTT-03)의 `processes[]`로 보냅니다(S15P31E110-122). Zephyr 는 에이전트·네트워크가 없어 대상이 아닙니다.

## 설정

| 환경변수 | 기본값 | 설명 |
| --- | --- | --- |
| `XENOVA_AGENT_VM_ID` | `/etc/xenova/instance-id` 내용 | 이 VM의 UUID. 플랫폼이 게스트 준비 때 파일로 기록한다 |
| `XENOVA_AGENT_BROKER_HOST` / `PORT` | `192.168.77.1` / `1883` | dom0 xenbr0 의 Mosquitto |
| `XENOVA_AGENT_BROKER_USERNAME` / `PASSWORD` | (없음) | 브로커 계정이 생기면 설정 (`deploy/`의 ACL) |
| `XENOVA_AGENT_LOG_LEVEL` | `INFO` | DEBUG/INFO/WARNING/ERROR/CRITICAL |

heartbeat 주기는 계약값 5초로 고정이며 환경변수로 바꿀 수 없습니다(`XENOVA_AGENT_HEARTBEAT_INTERVAL`이 설정돼 있으면 시작을 거부). 시험용 짧은 주기는 `AgentSettings(heartbeat_interval_s=…)` 생성자로만 줍니다. vm_id 는 소문자·하이픈 표준형으로 정규화해 토픽과 봉투에 씁니다(Spring·registry 문자열과 글자까지 같아야 함). UUID 가 아니거나 nil UUID(Domain-0)이거나, 포트·로그 레벨이 범위 밖이면 시작하지 않고 종료 코드 2 로 끝납니다.

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

1. `apt install python3-paho-mqtt` (Ubuntu 24.04 1.6.1, Debian 13 2.1.0). 스크립트는 설치 전에 rootfs 의 `paho/mqtt/client.py`를 확인하고, 없으면 아무것도 설치하지 않고 종료 코드 1 을 냅니다
2. 위 스크립트 호출. 종료 코드가 0 이 아니면 템플릿 생성을 중단합니다
3. 브로커 계정이 생기면 `/etc/xenova/agent.env`에 `XENOVA_AGENT_BROKER_USERNAME/PASSWORD` 기록 (없으면 생략)

최종 이미지 검사는 chroot 안에서 `python3 -c "import paho.mqtt.client"`와 `systemctl is-enabled xenova-agent.service`로 합니다. 스크립트는 rootfs 를 신뢰하지 않아, 쓰려는 자리의 마지막 요소가 심볼릭 링크면(호스트 파일로 이어질 수 있음) 아무것도 쓰지 않고 거부합니다.

VM 생성 경로가 둘이라 신원 유닛 의존은 drop-in 으로 나눕니다. 일반 생성 VM(`prepare_instance`)에는 `/etc/xenova/instance-id`만 있고 `xenova-guest-identity.service`가 없으므로 기본 유닛은 순서(`After=`)만 둡니다. deployment seed 경로(`prepare_deployment_seed`)처럼 신원 유닛이 있는 rootfs 에는 설치 스크립트가 `xenova-agent.service.d/10-xenova-identity.conf`로 `Requires=`를 붙여, 신원 확립에 실패한 VM 은 heartbeat 를 보내지 않습니다. seed 준비가 템플릿 설치 뒤에 신원 유닛을 넣는 순서라면 그 시점에 `install_identity_dropin(rootfs)`를 한 번 더 부릅니다. `/etc/xenova/instance-id`가 없으면 어느 경로든 시작하지 않습니다. Zephyr 템플릿에는 설치하지 않습니다.

## 테스트

```bash
cd services/vm-agent/agent
python -m pytest
```

가짜 시계와 가짜 클라이언트로 주기·지연 보정·끊김 건너뜀·발행 실패·정지를 확인합니다. 실제 브로커 시험은 mqtt-client 의 `PERSEUS_MQTT_TEST_BROKER` 방식을 따릅니다.
