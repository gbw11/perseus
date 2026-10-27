# xenova-mqtt — MQTT 계약 v1 클라이언트

VM 에이전트와 RC카 앱 VM(자율주행·수동주행·인터랙션)이 같이 쓰는 Python MQTT 클라이언트입니다.
[MQTT 메시지 계약 v1](../../../../docs/contracts/mqtt-contract-v1.md)의 봉투·토픽·QoS를 이 패키지가 맞춰 주므로, 쓰는 쪽은 `type`과 `payload`만 정하면 됩니다.

| 계약 | 패키지가 하는 일 |
| --- | --- |
| 2장 공통 봉투 | `schema_version`, `message_id`, `vm_id`/`source_id`, `app_id`, `boot_id`, `agent_instance_id`, `type`, `seq`, `sent_at`을 붙인다. `seq`는 토픽별로 1부터 센다 |
| 2장 수신 검사 | 필수 필드·`schema_version`·토픽과 `vm_id`/`app_id` 일치를 검사하고, 같은 발행자의 같거나 작은 `seq`는 버린다. `boot_id`·`agent_instance_id`가 바뀌면 다시 센다 |
| 3·5·6·7·8장 QoS | `type`별 QoS·retain 기본값 (`xenova_mqtt/policy.py`). 예: `drive.command` 0, `agent.inventory` 1 retained |
| 1장 크기 | 64 KB, 주행 토픽(`drive.command`, `vehicle.*`) 1 KB를 넘으면 발행 전에 `ValueError` |
| 1장 신선도 | 받은 메시지마다 수신 단조 시각을 남긴다. `sent_at`으로 신선도를 판정하지 않는다 |
| 세션 | clean session, 자동 재연결(1→10초), 재연결하면 구독을 다시 건다 |

검사에 걸린 메시지는 예외 없이 로그(`xenova_mqtt` 로거)만 남기고 버립니다. 백엔드 `AgentMessageHandler`와 같은 방식입니다.

## 설치

Python 3.11 이상, paho-mqtt 1.6 또는 2.x. 두 버전의 콜백 형식을 모두 지원합니다.

```bash
# Debian 13 (paho-mqtt 2.1) / Ubuntu 24.04 (paho-mqtt 1.6.1)
sudo apt install python3-paho-mqtt
# 패키지 경로를 PYTHONPATH에 두거나
export PYTHONPATH=/opt/xenova/mqtt-client
# pip를 쓸 수 있으면
pip install ./platform/services/vm-agent/mqtt-client
```

## 사용

### 에이전트 (VM)

```python
from xenova_mqtt import Identity, XenovaClient, topics

client = XenovaClient(Identity(vm_id=VM_ID), host="10.0.0.1", port=1883)
client.start()          # 브로커가 없어도 실패하지 않고 백그라운드에서 재시도

client.publish(topics.heartbeat(VM_ID), "agent.heartbeat", {})
client.publish(topics.inventory(VM_ID), "agent.inventory", {
    "os": {"id": "debian", "version_id": "13", "pretty_name": "Debian GNU/Linux 13"},
    "kernel_release": "6.12.0", "hostname": "vm-1", "interfaces": [], "agent_version": "0.1.0",
})
```

`boot_id`는 `/proc/sys/kernel/random/boot_id`에서 읽고, `agent_instance_id`는 프로세스마다 새로 만듭니다.

### 앱 VM — 주행 명령과 latest-only

```python
client = XenovaClient(Identity(vm_id=VM_ID, app_id="autonomous"), host=BROKER)
client.subscribe(topics.vehicle_control())                  # 핸들러 없이 최신값만 보관
client.subscribe(topics.app_events("perception"))
client.start()

control = client.latest(topics.vehicle_control())
perception = client.latest(topics.app_events("perception"))
if control and perception and perception.age_ms() < 200:
    client.publish(topics.app_events("autonomous"), "drive.command", {
        "generation": control.payload["generation"], "v_mps": 0.5, "steer_rad": 0.0,
    })
```

### Linux 앱 — 앱 생존 신호와 systemd watchdog

Linux VM 의 에이전트는 VM 자원과 에이전트 생존만 알립니다. 앱이 실제로 일하는지는 앱이 직접 `app.heartbeat`(계약 6.1)로 알립니다.
`AppHeartbeat.beat()` 는 **처리 루프 안에서** 매 반복 부릅니다. MQTT 는 계약 주기 1초로 고정이고, 브로커와 끊겨 있으면 쌓지 않습니다.
유닛에 `WatchdogSec=` 이 있으면 같은 호출이 그 절반마다 systemd 에 `WATCHDOG=1` 도 보냅니다(브로커 연결과 무관, 주기는 systemd 가
넘겨 주는 값에서 읽어 유닛과 어긋나지 않음). 루프가 멈추면 통지가 끊겨 systemd 가 앱을 재시작합니다. 루프 한 바퀴가 `WatchdogSec`
절반보다 길면 정상이어도 재시작되니 `WatchdogSec` 은 넉넉히 잡습니다. dom0 Recovery Worker 는 이 신호로 Linux VM 을 재시작하지 않습니다.
이 기능은 xenova_mqtt 0.2.0 부터 있습니다.

```python
from xenova_mqtt import AppHeartbeat, Identity, XenovaClient, systemd, topics

client = XenovaClient(Identity(vm_id=VM_ID, app_id="message-board"), host=BROKER)
client.start()
heartbeat = AppHeartbeat(client, "message-board")
systemd.ready()                                  # Type=notify 유닛이면 준비 완료
while not stopping:
    handle_one_batch()                           # 앱의 실제 일
    heartbeat.beat("idle")                       # active / standby / idle / error. 주행과 무관한 앱의 평상시는 idle
```

```ini
[Service]
Type=notify
ExecStart=/usr/bin/python3 -m xenova_message_board
Restart=on-failure
WatchdogSec=10
```

### VM이 아닌 발행자

```python
Identity(source_id="dom0-gateway", app_id="vehicle")   # dom0-gateway, jetson, esp32, dom0-server
```

### 이벤트 구독

```python
def on_mode(message):            # paho 네트워크 스레드에서 불린다 — 오래 막지 않는다
    print(message.type, message.payload, message.envelope["seq"])

client.subscribe(topics.app_events("vehicle"), on_mode)
```

## 하지 않는 것

- Zephyr 주행 VM, ESP32: 펌웨어라 이 패키지를 쓰지 않는다. 같은 봉투 규칙을 펌웨어에서 구현한다 — Zephyr 는 [C 판 설계](../../../../docs/design/mqtt-c-client.md).
- 브로커 계정·ACL: [브로커 구성](../../../deploy/mosquitto/README.md)에서 관리한다. 계정 이름은 토픽 안의 id(에이전트는 `vm_id`, 앱은 `app_id`)이고 `username`, `password` 인자로 넘긴다.
- heartbeat 주기, 메트릭 수집, 서비스 등록: 에이전트 본체가 정한다.

## 테스트

```bash
python -m pytest                        # 단위 테스트 (브로커 테스트는 건너뜀)

docker run --rm -d --name xenova-mq-test -p 18830:1883 eclipse-mosquitto:2 mosquitto -c /mosquitto-no-auth.conf
XENOVA_MQTT_TEST_BROKER=127.0.0.1:18830 XENOVA_MQTT_TEST_CONTAINER=xenova-mq-test python -m pytest
```

Git Bash에서는 `/mosquitto-no-auth.conf`가 Windows 경로로 바뀌므로 `MSYS_NO_PATHCONV=1`을 앞에 붙입니다.
`XENOVA_MQTT_TEST_CONTAINER`를 주면 브로커를 재시작해 재연결·재구독까지 확인합니다.

계정·ACL 이 걸린 브로커로 개발할 때는 [로컬 브로커](../../../deploy/mosquitto/README.md)(`docker compose`, 1883·9001)를 씁니다. 위 브로커 테스트는 계정 없이 도는 무인증 브로커 기준입니다.
