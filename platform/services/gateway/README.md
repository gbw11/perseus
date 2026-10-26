# xenova-gateway — dom0 게이트웨이 (제어권 전환 판정·ESP32 전달)

dom0에서 도는 별도 프로세스입니다. 주행 앱의 `drive.command` 신선도로 제어권(AUTO·MANUAL·AUTO_READY·SAFE_STOP)을 판정하고,
**활성 앱의 현재 세대 명령만** ESP32로 보냅니다 (S15P31E110-98).
규칙은 [MQTT 메시지 계약 v1](../../../docs/contracts/mqtt-contract-v1.md) v1.2 5·6장과 [RC카 시나리오](../../../docs/scenario-rc-car.md) 4장·§8 결정(D1·D5·D8)을 따릅니다.

두 VM이 같은 펌웨어에서 AUTO/MANUAL을 모두 수행하는 구성에는 `--vm-a <UUID> --vm-b <UUID>`를 지정합니다.
이때 앱과 활성 VM을 별개로 선택하고, UUID별 실행·
heartbeat·현재 세대 명령을 대조합니다. 복구한 VM은 준비를 다시 확인한 뒤 대기로
재합류하며 자동으로 활성 권한을 되찾지 않습니다. 아래 기존 모드 표는 UUID 쌍을
생략한 앱별 구성의 규칙입니다. 공통 구성의 준비·양방향 복귀·대칭 승계·ACL 조건은
[공통 주행 펌웨어 안내](../../apps/drive/README.md)에 정리했습니다.

| 하는 것 | 하지 않는 것 |
| --- | --- |
| 활성 앱 명령 100 ms 끊김 → 전환, `generation` 지정 | VM 재시작 (복구 서비스) |
| `vehicle/control`(retained), `drive.mode.changed`, `drive.fault.detected`, `drive.resume.accepted`·`cancelled` 발행 | 인지 끊김 시 정지 (자율주행 앱, D5) |
| 계약 5.5 수용 규칙 적용 후 시리얼 프레임 전달, `SAFE_STOP` 중 정지 프레임 | 최종 정지 (ESP32 와치독 300 ms) |

## 토픽

| 방향 | 토픽 | `type` | 쓰는 곳 |
| --- | --- | --- | --- |
| 구독 | `xenova/v1/apps/{autonomous,manual}/events` | `drive.command` | 신선도 판정, 활성 앱이면 전달 |
| 구독 | `xenova/v1/apps/{autonomous,manual}/events` | `app.heartbeat` | `AUTO_READY` 조건 (D8). 전환 판정에는 쓰지 않는다 |
| 구독 | `xenova/v1/apps/message-board/events` | `drive.resume.requested` | 재개 요청 (메시지 보드 VM, MQTT-06). QoS 1 |
| 구독 | `xenova/v1/console/resume` | `drive.resume.requested` | 재개 요청 (운영자 콘솔 → 백엔드 발행, MQTT-13). QoS 1. 위와 똑같이 처리 |
| 구독 | `xenova/v1/vehicle/control` | `vehicle.control` | 시작할 때 이전 세대 읽기만 (재시작해도 세대를 되돌리지 않는다) |
| 발행 | `xenova/v1/vehicle/control` | `vehicle.control` | QoS 1 retained. 시작·전환마다 |
| 발행 | `xenova/v1/apps/vehicle/events` | `drive.mode.changed`, `drive.fault.detected`, `drive.resume.accepted`, `drive.resume.cancelled` | QoS 1 |

봉투·QoS·재연결은 [xenova_mqtt](../vm-agent/mqtt-client/README.md)가 맡습니다. 발행자는 `source_id: dom0-gateway`, 브로커 계정은 `gateway`입니다.

## 모드

| 지금 | 조건 | 다음 | `reason` |
| --- | --- | --- | --- |
| (시작) | — | `AUTO` (기본) 또는 `--start-mode SAFE_STOP` | `start` |
| `AUTO` | 자율 명령 100 ms 끊김 + 수동 명령 신선 | `MANUAL` | `command_timeout` |
| `AUTO` | 자율 명령 100 ms 끊김 + 수동도 신선하지 않음 | `SAFE_STOP` | `no_fresh_standby` |
| `MANUAL` | 자율 명령 3 s 연속 신선 + 자율 `app.heartbeat` 3 s 안 + `state != error` | `AUTO_READY` | `autonomous_ready` * |
| `MANUAL`·`AUTO_READY` | 수동 명령 100 ms 끊김 | `SAFE_STOP` | `no_fresh_standby` |
| `AUTO_READY` | 위 `AUTO_READY` 조건이 깨짐 | `MANUAL` | `autonomous_lost` * |
| `AUTO_READY` | `drive.resume.requested` 수락 후 3초, 그때도 `AUTO_READY` 조건 충족 | `AUTO` | `resume_requested` |
| `SAFE_STOP` | 수동 명령 신선 | `MANUAL` | `manual_fresh` * |

\* 계약 5.4의 `reason` 목록에 없어 새로 둔 값입니다 (계약 v1.1 반영 요청).

- 전환마다 `generation`이 1 오릅니다. 시작 세대는 retained `vehicle/control`의 세대 + 1 (없으면 1).
- **신선** = 마지막 유효 `drive.command`를 받은 지 100 ms 이하. 받은 시각(게이트웨이 단조 시계)으로만 잽니다.
- 앱 생존은 세대와 상관없이 유효한 명령이면 인정합니다. 전환 직후 새 활성 앱은 몇 ms 동안 옛 세대로 보내기 때문입니다.
- **전달**은 활성 앱 토픽의, 현재 세대와 같은 명령뿐입니다 (계약 5.5-1·2). 같은 판정 주기에 여러 개 오면 마지막 것만 (latest-only). `seq` 중복·역전은 `xenova_mqtt`가 버립니다 (5.5-3).
- `SAFE_STOP`에서는 들어갈 때 정지 프레임을 control·이벤트보다 먼저 보내고, 이후 20 ms마다 다시 보냅니다. 종료할 때도 정지 프레임을 한 번 보냅니다.
- `AUTO`는 시작이나 재개로만 들어갑니다. `MANUAL`이 끊기면 자율이 신선해도 `SAFE_STOP`입니다.
- `drive.fault.detected`는 한 번이라도 명령을 낸 주행 앱이 끊길 때(활성·대기 모두) 한 번 냅니다. 명령이 다시 오면 다시 낼 수 있습니다.
- `drive.resume.requested`는 `AUTO_READY`에서만 받고, 다른 모드에서는 이벤트 없이 무시합니다.

### 재개 3초 카운트다운 (계약 v1.2 6장)

```text
AUTO_READY ──재개 요청──▶ drive.resume.accepted {switch_in_ms: 3000, generation, requested_by}
              │            (모드 AUTO_READY 유지, 수동이 계속 제어. 화면이 3·2·1 을 센다)
              ├─ 3초 뒤 조건 여전히 충족 ─▶ AUTO, 세대 +1, drive.mode.changed reason resume_requested
              └─ 그 사이 AUTO_READY 를 벗어남 ─▶ drive.resume.cancelled {reason, generation} → MANUAL 또는 SAFE_STOP
```

- 받는 조건: 모드 `AUTO_READY` 이고 카운트다운 중이 아님. 카운트다운 중 재요청, 같은 판정 주기의 두 번째 요청은 무시합니다 (이벤트 없음, `MEASURE event=resume_ignored`).
- 3초는 수락한 판정 시각(게이트웨이 단조 시계)부터 셉니다. 3초째 판정 주기에서 전환하므로 늦어도 판정 주기(5 ms) 안팎입니다.
- `cancelled.reason`은 그 전환의 `reason` 그대로입니다. 자율 명령·heartbeat 가 끊기면 `autonomous_lost` (→ `MANUAL`), 수동 명령이 끊기면 `no_fresh_standby` (→ `SAFE_STOP`).
- `accepted`·`cancelled`의 `generation`은 수락 때의 세대(`AUTO_READY` 세대)입니다. `cancelled`는 그 전환의 `vehicle/control`·`drive.mode.changed`보다 먼저 냅니다 (`SAFE_STOP`이면 정지 프레임이 맨 앞).
- `requested_by`는 요청 payload 의 문자열 그대로, 없으면 `null`.

## 실행

Python 3.11 이상, paho-mqtt 1.6 또는 2.x. 시리얼은 pyserial(`python3-serial`)이 있어야 합니다.

```bash
export PYTHONPATH=/opt/xenova/gateway:/opt/xenova/mqtt-client   # 개발 PC: platform/services/gateway 와 vm-agent/mqtt-client
export XENOVA_GATEWAY_BROKER_PASSWORD=<비밀번호>                  # 저장소에 두지 않는다
python3 -m xenova_gateway --sink serial --serial-port /dev/ttyUSB0
```

| 인자 | 환경변수 | 기본값 | 설명 |
| --- | --- | --- | --- |
| `--host` / `--port` | `XENOVA_GATEWAY_BROKER_HOST` / `_PORT` | `127.0.0.1` / `1883` | dom0 Mosquitto |
| `--username` / `--password` | `XENOVA_GATEWAY_BROKER_USERNAME` / `_PASSWORD` | `gateway` / 없음 | 비밀번호는 환경변수로 (ps 에 보이지 않게) |
| `--sink` | `XENOVA_GATEWAY_SINK` | `mock` | `mock`(기록만) / `serial` |
| `--serial-port` / `--serial-baud` | `XENOVA_GATEWAY_SERIAL_PORT` / `_BAUD` | `/dev/ttyUSB0` / `115200` | |
| `--frame-log` | `XENOVA_GATEWAY_FRAME_LOG` | 없음 | 목 싱크가 프레임을 한 줄씩 기록할 파일, `-` 는 표준출력 |
| `--start-mode` | `XENOVA_GATEWAY_START_MODE` | `AUTO` | `SAFE_STOP` 이면 수동 명령이 올 때까지 정지 (시나리오 F5 "자동 출발 없음") |
| `--retained-wait-s` | `XENOVA_GATEWAY_RETAINED_WAIT_S` | `1.0` | 시작 시 이전 `vehicle/control` 을 기다리는 시간 |
| `--log-level` | `XENOVA_GATEWAY_LOG_LEVEL` | `INFO` | |
| `--vm-a` / `--vm-b` | `XENOVA_GATEWAY_VM_A` / `_VM_B` | 없음 | 같은 공통 펌웨어를 실행하는 서로 다른 UUID 쌍. 둘을 함께 지정 |
| `--initial-active-vm` | `XENOVA_GATEWAY_INITIAL_ACTIVE_VM` | `--vm-a` | retained 활성 문맥이 없는 최초 선택. 고정 Primary 역할이 아님 |

판정 시간(100 ms·3 s·3 s·20 ms, 재개 카운트다운 3 s)은 계약값이라 인자로 바꾸지 않습니다 (`xenova_gateway/machine.py` `Timing`).

### 측정 로그

전환·첫 출력마다 `MEASURE` 줄을 남깁니다. 시각 `t`는 게이트웨이 단조 시계(초)입니다 (-87·-94).

```text
MEASURE event=transition from=AUTO to=MANUAL gen=12 reason=command_timeout last_active_t=… detected_t=… gap_ms=105.2
MEASURE event=first_output gen=12 mode=MANUAL reason=command_timeout t=… since_detect_ms=23.6 since_last_active_ms=129.1
```

`gap_ms` = 마지막 활성 명령 수신 → 판정, `since_last_active_ms` = 마지막 활성 명령 수신 → 새 세대 첫 프레임(수동 명령 또는 정지) 전달.

## ESP32 시리얼 프레임 (초안)

> **초안입니다.** S15P31E110-241 에서 계약 v1.1 로 옮겨 확정합니다. 옛 [차량 제어 명세](../../../docs/design/vehicle-control-spec.md) 5.2 USB 시리얼 형식을 따랐습니다.

```text
본문   = [id:u16 LE][len:u8][payload][crc:u16 LE]      CRC-16/CCITT-FALSE (0x1021, 초기 0xFFFF), 범위 id·len·payload
프레임 = COBS(본문) + 0x00                              최대 16 바이트, 받는 쪽은 CRC 실패 시 다음 0x00 까지 버림
```

| id | 이름 | payload (리틀엔디언) | len |
| --- | --- | --- | --- |
| `0x0100` | drive | `[generation:u32][counter:u8][v:i16 mm/s][steer:i16 mrad]` | 9 |
| `0x0010` | stop | `[generation:u32][counter:u8][reason:u8]` — reason 1 `SAFE_STOP`, 2 게이트웨이 종료 | 6 |

- `counter`는 drive·stop이 같이 쓰는 8비트 순환 값입니다. 매체 위 중복·역전 확인용이고, MQTT 봉투 `seq`를 대신합니다.
- 속도·조향은 1/1000 단위 정수로 바꾸고 i16 범위로 자릅니다 (±32.767 m/s, ±32.767 rad).
- ESP32는 현재 세대(`vehicle/control` 대신 프레임의 `generation`)가 바뀌면 counter 기준을 새로 잡고, 300 ms 동안 프레임이 없으면 정지합니다.
- 검증 벡터: `crc16(b"123456789") == 0x29B1`. 인코딩·디코딩 기준 구현은 `xenova_gateway/frame.py`.
- 정할 것: `generation` u32 때문에 drive payload가 9 바이트라 CAN 한 프레임(8 바이트)에 안 들어갑니다. CAN으로 갈 때 u16으로 줄일지, ESP32 → 게이트웨이 상태 프레임(`vehicle.status` 중계), hello/ack 세션.

## 시험

```bash
cd platform/services/gateway
python -m pytest            # 상태 기계·코덱·루프 단위 시험 (시각 주입, sleep 없음)
```

로컬 브로커 통합 측정 (게이트웨이 + `platform/apps/manual` 참조 앱 + 자율주행 목, 자율 목을 강제 종료해 잰다):

```bash
MSYS_NO_PATHCONV=1 docker run -d --name gw-broker -p 18832:1883 \
  -v "$PWD/nodelay.conf:/mosquitto/config/mosquitto.conf:ro" eclipse-mosquitto:2.0.21
# nodelay.conf: listener 1883 / allow_anonymous true / set_tcp_nodelay true
python tools/measure_failover.py --port 18832 --runs 10
```

| 도구 | 하는 일 |
| --- | --- |
| `tools/mock_drive_app.py` | 주행 앱 목. `vehicle/control` 세대로 `drive.command` 50 Hz + `app.heartbeat` 1초 |
| `tools/send_resume.py` | 재개 요청 목. `drive.resume.requested` 한 번. 기본은 메시지 보드 경로, `--console` 이면 `xenova/v1/console/resume` (계정 `backend`, `source_id: dom0-server`) |
| `tools/measure_failover.py` | 위 둘과 게이트웨이·수동 앱을 띄워 AUTO→MANUAL→AUTO_READY→(재개 3초)→AUTO 를 N회, 끝에 SAFE_STOP 을 잰다 |

## 검증 현황 (2026-10-21, 개발 PC Windows + Docker mosquitto 2.0.21, 목 싱크)

| 항목 | 결과 |
| --- | --- |
| 단위 시험 | 53개 통과 (v1.2 재개 카운트다운 포함) |
| AUTO→MANUAL 판정 (마지막 자율 명령 → 판정), 10회 | p50 105.2 ms, max 111.0 ms |
| 마지막 자율 명령 → 첫 수동 프레임 전달, 10회 | p50 129.1 ms, max 135.2 ms (브로커 `set_tcp_nodelay true`) |
| 같은 측정, 브로커 기본 설정 | p50 150.8 ms, max 161.4 ms — 아래 참고 |
| 자율 첫 명령 → AUTO_READY, 10회 | 3.001~3.002 s, 재개 후 AUTO 세대 +1 (v1.2 카운트다운 이전 측정) |
| 두 앱 종료 → SAFE_STOP | 판정 109.4 ms, 정지 프레임 49 Hz |

- **브로커 `set_tcp_nodelay true`가 필요합니다.** 기본값(Nagle 켜짐)이면 브로커 → 게이트웨이 소켓에서 작은 메시지가 ACK를 기다리며 묶여, 전환 뒤 첫 수동 명령이 약 40 ms 늦게 옵니다 (브로커 단방향 지연 자체는 1.3 ms). dom0 브로커 설정(`platform/deploy/mosquitto/etc/mosquitto.conf`)에 넣어야 합니다.
- Windows 수치입니다. 실보드(Linux, 코어 고정)에서 다시 잽니다.

## 아직 안 한 것

- vchan 중계 (S15P31E110-240): Zephyr 주행 VM 의 명령은 dom0 중계를 거쳐 브로커로 온다. 게이트웨이는 브로커만 본다
- 실제 ESP32 연결, 프레임 확정·계약 v1.1 (S15P31E110-241), `vehicle.status` 중계
- 코어 고정·실시간 우선순위, systemd unit, Yocto 레시피 (플랫폼·deploy)
- 시리얼 쓰기 연속 실패 시 처리 (지금은 로그·횟수만, 최종 정지는 ESP32 와치독)
