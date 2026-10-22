# MQTT 메시지 계약 v1

**최종 갱신** 2026-10-21 v1.1 (ESP32는 게이트웨이 시리얼 뒤로, Zephyr는 vchan 중계, `AUTO_READY`는 `app.heartbeat` 기준 — 변경 이력)
**대상 시스템** Raspberry Pi 4 + Xen 멀티 OS 관리 플랫폼, RC카 시나리오
**브로커** dom0 Mosquitto

VM 에이전트, RC카 앱 VM, dom0 서버·게이트웨이, 젯슨이 주고받는 MQTT 메시지의 기준 문서입니다. ESP32는 MQTT에 붙지 않고 게이트웨이를 거친다(4장). 에이전트 수신부는 이미 백엔드에 구현돼 있고(`backend/.../agent/`), 이 문서의 2장·3장은 그 구현과 같습니다. 4장 이후 RC카 메시지는 새로 정하는 내용입니다.

`MQTT-NN`은 노션 인터페이스 계약의 MQTT 계약 ID입니다. 토픽별 상세 규칙(오류 코드, 중복 처리, 수락 기준)은 노션 행에 있고, 이 문서와 다르면 이 문서를 따릅니다.

토픽·필드·QoS를 바꾸면 발행하는 쪽과 구독하는 쪽 담당이 함께 리뷰합니다. 한쪽만 바꾸면 메시지가 조용히 버려집니다.

---

## 1. 공통 규칙

| 항목 | 규칙 |
| --- | --- |
| 토픽 접두사 | `xenova/v1/`. 10/20 이름 통일로 접두사도 바꿨다. 이전 `perseus/v1/`는 어디에도 배포된 적이 없어 호환 처리는 없다 |
| 인코딩 | UTF-8 JSON, 필드 이름은 snake_case |
| 시각 | `sent_at`은 ISO-8601 UTC. **보드 간 시각 비교로 판정하지 않는다.** 신선도는 받는 쪽이 수신 시각으로 잰다 |
| 단위 | 속도 `m/s`, 조향 `rad`, 시간 `ms`, 메트릭 `percent`·`bytes`. (에이전트 MQTT-03 기준. dom0의 xentop 원시값은 MQTT가 아니라 VM API 계약 4.6 `GET /api/v1/stats`로 전달한다) |
| 크기 | 메시지 64 KB 이하. 주행 토픽(`drive.command`, `vehicle/*`)은 1 KB 이하 |
| 세션 | clean session. 오프라인 큐에 기대지 않는다 |

## 2. 공통 봉투

모든 메시지가 같은 봉투를 쓴다. 백엔드 `AgentEnvelope`가 이 형식을 검사하고, 어긋나면 로그만 남기고 버린다.

```json
{
  "schema_version": "1.0",
  "message_id": "6f1c0e0a-6a1e-4d43-9d2b-8f7c2f0f9a11",
  "vm_id": "1b4e28ba-2fa1-11d2-883f-0016d3cca427",
  "boot_id": "c5b0b0b2-...",
  "agent_instance_id": "agent-1",
  "type": "agent.heartbeat",
  "seq": 42,
  "sent_at": "2026-10-19T09:00:00.000Z",
  "payload": {}
}
```

| 필드 | 필수 | 규칙 |
| --- | --- | --- |
| `schema_version` | O | `"1.0"` 고정. 다르면 `SCHEMA_VERSION_UNSUPPORTED` |
| `message_id` | O | 메시지마다 고유 (UUID 권장) |
| `vm_id` | VM이 발행할 때 | 토픽의 `{vm_id}`가 있으면 같아야 한다. 다르면 `VM_ID_MISMATCH` |
| `source_id` | VM이 아닐 때 | `vm_id` 대신 쓴다. `dom0-server`, `dom0-gateway`, `jetson` |
| `app_id` | 앱 토픽일 때 | 토픽의 `{app_id}`와 같아야 한다 |
| `boot_id` | O | 발행 측 부팅마다 바뀌는 값 |
| `agent_instance_id` | O | 발행 프로세스 실행마다 바뀌는 값. 에이전트가 아니어도 같은 필드를 쓴다 |
| `type` | O | 메시지 종류. 점으로 구분한 소문자 (`agent.heartbeat`, `drive.command`) |
| `seq` | O | 1 이상. 같은 `agent_instance_id`·토픽 안에서 1씩 증가. 받는 쪽은 마지막 값 이하를 버린다 (중복·지연) |
| `sent_at` | O | ISO-8601 UTC |
| `payload` | O | JSON 객체 |
| `correlation_id` | 선택 | 앱 간 한 흐름을 묶는 ID |
| `causation_id` | 선택 | 직접 원인이 된 메시지의 `message_id` |

- 노션 MQTT-06의 AppEventEnvelope는 이 봉투로 합친다: `event_id` → `message_id`, `source_vm_id` → `vm_id`, `source_app_id` → `app_id`, `event_type` → `type`.
- 백엔드가 에이전트에 보내는 명령(MQTT-04)만 예외다. 공통 필드(`schema_version`, `message_id`, `vm_id`, `type`, `sent_at`)에 명령 전용 필드를 더한 CommandEnvelope를 쓰고, `boot_id`·`agent_instance_id`·`seq` 대신 `expected_boot_id`·`command_id`를 쓴다.
- `boot_id`나 `agent_instance_id`가 바뀌면 받는 쪽은 그 발행자의 `seq` 기준을 새로 잡는다.
- 펌웨어(Zephyr)는 `message_id`를 단조 증가 정수 문자열로 대신할 수 있다.

## 3. 에이전트 토픽 (구현됨)

각 VM의 에이전트와 백엔드가 주고받는다. heartbeat·inventory·metrics 수신은 백엔드에 구현돼 있고, 명령(04·05)은 설계 초안이다.

| ID | 토픽 | `type` | 방향 | QoS | retain | payload |
| --- | --- | --- | --- | --- | --- | --- |
| MQTT-01 | `xenova/v1/vms/{vm_id}/heartbeat` | `agent.heartbeat` | 에이전트 → 백엔드 | 1 | X | `{}` (봉투만) |
| MQTT-02 | `xenova/v1/vms/{vm_id}/inventory` | `agent.inventory` | 에이전트 → 백엔드 | 1 | **O** | `os{id, version_id, pretty_name}`, `kernel_release`, `hostname`, `interfaces[{name, mac, addresses[{address, family}]}]`, `agent_version`, `capabilities{commands[], metrics[]}` |
| MQTT-03 | `xenova/v1/vms/{vm_id}/metrics` | `agent.metrics` | 에이전트 → 백엔드 | 0 | X | `window_ms`, `values[{name, unit, ...}]`, `processes[]` |
| MQTT-04 | `xenova/v1/vms/{vm_id}/command` | `agent.command` | 백엔드 → 에이전트 | 1 | X | `command_id`, `operation_id`, `expected_boot_id`, `expires_at`, `action`(`stress`/`agent_exit`), `parameters` |
| MQTT-05 | `xenova/v1/vms/{vm_id}/command-result` | `agent.command_result` | 에이전트 → 백엔드 | 1 | X | `command_id`, `operation_id`, `status`(`ACCEPTED`/`RUNNING`/`SUCCEEDED`/`FAILED`/`REJECTED`), `started_at`, `finished_at`, `duration_ms`, `result`, `error` |

- inventory는 retained다. 최초 기동과 OS·네트워크 정보가 바뀔 때 전체를 다시 보낸다. retained 메시지는 마지막 보고일 뿐 지금 `ONLINE`이라는 증거가 아니다. VM을 삭제하면 백엔드가 그 VM의 retained inventory를 지운다.
- inventory 필수값은 `os.id`, `kernel_release`, `hostname`. os-release가 없는 Zephyr는 `os.id = "zephyr"`, `kernel_release = "<Zephyr 버전>"`으로 보낸다.
- metrics의 `unit`은 `percent`, `bytes`만 받는다. 그 외는 `METRIC_UNIT_MISMATCH`. 값마다 `value`가 `null`이면 `unavailable_reason`을 붙인다(측정 실패를 0으로 보내지 않는다). 에이전트는 `source`를 보내지 않으며 백엔드가 `guest_agent`로 저장한다. dom0가 xentop으로 재는 VM 외부 관측값은 `source = xen`으로 VM API 계약 2.4·4.6절을 따른다
- heartbeat 주기는 5초. 에이전트 상태는 **백엔드가 heartbeat 나이로 판정**한다 (`ONLINE` → 15초 `STALE` → 30초 `OFFLINE`). 에이전트가 스스로 상태를 선언하지 않는다.
- heartbeat는 **VM 재시작 판정에만** 쓴다. 주행 제어권 전환 판정에는 쓰지 않는다 (5장).

## 4. RC카 구성과 app_id

| app_id | 발행자 | `guest_kind` | 역할 |
| --- | --- | --- | --- |
| `autonomous` | 자율주행 VM | `zephyr` | 젯슨 인식 결과로 판단·경로 생성, 평소 주행 명령 |
| `manual` | 수동주행 VM | `zephyr` | 대기, 인계 시 콘솔 키 입력을 주행 명령으로 변환 |
| `message-board` | 메시지 보드 VM | `linux` | 관제 메시지, 모드·장애 알림 표시, 자율주행 재개 요청 |
| `perception` | 젯슨 (`source_id: jetson`) | — | 객체인식 결과. 외부 발행자이며 앱 3종에 포함하지 않는다 |
| `vehicle` | dom0 서버·게이트웨이 | — | 모드 전환·장애 알림 |

- `guest_kind` 값은 `linux`, `zephyr` 두 가지다 (VM 관리 API 계약과 같다).
- 자율·수동주행 VM은 Zephyr다 (2026-10-20 [RC카 시나리오](../scenario-rc-car.md) §2). Zephyr에는 VM 에이전트가 없어 `agent.*` 토픽을 내지 않는다. 앱 프로세스 생존은 `app.heartbeat`(6.1)로 알린다.
- ESP32는 MQTT에 붙지 않는다. dom0 USB 시리얼(이후 CAN)로 게이트웨이와만 통신한다 (시나리오 §8 D1). 이 문서에서 ESP32로 가는 명령·ESP32에서 오는 상태는 모두 게이트웨이를 거친다.

### 4.1 Zephyr VM 연결 (vchan + dom0 중계)

Zephyr 주행 VM은 PV 네트워크 대신 Xen vchan으로 dom0 중계에 붙고, 중계가 VM 대신 브로커에 접속한다 (시나리오 §8 D3).

| 항목 | 규칙 |
| --- | --- |
| 경로 | Zephyr 앱 ↔ vchan ↔ dom0 중계 ↔ 브로커 (`127.0.0.1:1883`) |
| 봉투 | 앱이 만든 봉투(2장)를 중계가 바꾸지 않고 그대로 보낸다. 토픽·QoS·retain도 앱이 정한다 |
| 신원 | 중계는 vchan 상대 도메인으로 VM을 식별한다. domid → `vm_id`·`app_id`는 XenStore에서 읽고 payload를 믿지 않는다 |
| 브로커 계정 | 중계는 VM마다 연결을 따로 열고 그 VM의 `app_id` 계정으로 접속한다. 브로커 ACL(9장)은 직접 접속한 앱과 똑같이 걸린다 |
| 검사 | 봉투의 `vm_id`·`app_id`가 그 도메인과 다르면 중계가 버린다. 발행 토픽이 그 `app_id`에 허용된 것이 아니면 보내지 않는다 |
| 구독 | 앱이 요청한 구독만 대신 걸고, 받은 메시지를 그 VM의 vchan으로만 내려보낸다 |
| 끊김 | vchan이 끊기면 중계는 그 VM의 브로커 연결을 닫는다. 브로커 쪽에서는 앱이 끊긴 것과 같다 |

- vchan 프레임 형식과 중계 구현은 [C MQTT 클라이언트 설계](../design/mqtt-c-client.md)와 -239·-240에서 정한다. 이 문서는 브로커에서 보이는 결과만 정한다.
- Linux VM(메시지 보드, 에이전트)은 지금처럼 PV 네트워크로 브로커에 직접 붙는다.

## 5. 주행 제어

### 5.1 역할 분리

| 판정 | 주체 | 근거 신호 |
| --- | --- | --- |
| 제어권 전환 (자율 ↔ 수동) | **dom0 게이트웨이** (관리 서버와 별도 프로세스) | 활성 앱의 `drive.command` 끊김 100 ms |
| VM 재시작 | 복구 서비스 | Xen 도메인 상태 + Linux VM은 에이전트 `OFFLINE`, Zephyr VM은 `app.heartbeat`(6.1) 3초 끊김 |
| 최종 정지 | ESP32 | 게이트웨이 시리얼 프레임 끊김 300 ms (와치독) |

- 게이트웨이는 VM을 재시작하지 않고, 복구 서비스는 제어권을 바꾸지 않는다.
- 잘못된 승격보다 정지가 낫다. 수동 VM의 명령이 신선하지 않으면 승격하지 않고 `SAFE_STOP`으로 간다.

### 5.2 주행 모드

| 모드 | 활성 앱 | 들어가는 조건 |
| --- | --- | --- |
| `AUTO` | autonomous | 시작, 또는 `AUTO_READY`에서 자율주행 재개 요청 |
| `MANUAL` | manual | `AUTO`에서 자율 명령이 100 ms 끊기고 수동 명령이 신선할 때, 또는 `SAFE_STOP`에서 수동 명령이 신선해졌을 때 |
| `SAFE_STOP` | 없음 | 활성 앱 명령이 끊겼는데 넘길 앱이 없을 때 |
| `AUTO_READY` | manual | `MANUAL` 중 복구된 자율 VM이 3초 연속 아래를 모두 만족할 때: Xen 도메인 실행 중, `app.heartbeat` 신선(`state`가 `error` 아님), `drive.command` 신선 |

- 대기 중인 앱도 `drive.command`를 계속 발행한다. 게이트웨이는 활성 앱 명령만 유효로 보고, 대기 앱 명령은 신선도 확인에만 쓴다. 수동 VM은 키 입력이 없으면 `v_mps = 0`을 보낸다.
- 모드가 바뀌거나 활성 앱이 바뀔 때마다 게이트웨이는 `generation`을 1 올린다.
- 자율 VM은 Zephyr라 에이전트가 없다. `AUTO_READY` 판정에 에이전트 상태를 쓰지 않는다 (시나리오 §8 D8). 도메인 상태는 게이트웨이가 dom0에서 직접 확인한다.

### 5.3 토픽

| ID | 토픽 | `type` | 발행 → 구독 | 주기 | QoS | retain |
| --- | --- | --- | --- | --- | --- | --- |
| MQTT-06 | `xenova/v1/apps/autonomous/events` | `drive.command` | 자율 VM → 게이트웨이 | 50 Hz | 0 | X |
| MQTT-06 | `xenova/v1/apps/manual/events` | `drive.command` | 수동 VM → 게이트웨이 | 50 Hz | 0 | X |
| MQTT-06 | `xenova/v1/apps/perception/events` | `perception.result` | 젯슨 → 자율 VM | 15 Hz | 0 | X |
| MQTT-09 | `xenova/v1/vehicle/control` | `vehicle.control` | 게이트웨이 → 앱 전체, 백엔드 | 변경 시 | 1 | **O** |
| MQTT-10 | `xenova/v1/vehicle/status` | `vehicle.status` | 게이트웨이(ESP32 상태 변환) → 앱 전체, 백엔드 | 20 Hz | 0 | X |

- 주행 토픽은 QoS 0, 받는 쪽은 토픽별 최신 1개만 유지한다 (latest-only). 50 Hz라 재전송보다 다음 명령이 빠르다.
- `vehicle/control`은 retained라 앱이 재시작해도 현재 활성 앱과 세대를 바로 받는다.
- 와치독 300 ms는 50 Hz 기준 명령 15개 분량이다. 게이트웨이 전환(100 ms)이 와치독보다 먼저 일어난다.

### 5.4 payload

**`drive.command`**

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `generation` | uint32 | 발행 시점에 구독한 `vehicle/control`의 세대 |
| `v_mps` | float | 목표 속도 |
| `steer_rad` | float | 목표 조향각 |

**`vehicle.control`** (게이트웨이, `source_id: dom0-gateway`)

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `generation` | uint32 | 현재 세대 |
| `mode` | string | `AUTO` / `MANUAL` / `AUTO_READY` / `SAFE_STOP` |
| `active_app` | string \| null | `autonomous` / `manual` / null (`SAFE_STOP`) |
| `active_vm_id` | string \| null | 활성 앱의 VM |
| `reason` | string | 전환 사유 (`start`, `command_timeout`, `resume_requested`, `no_fresh_standby`) |

**`vehicle.status`** (게이트웨이, `source_id: dom0-gateway`)

ESP32가 시리얼로 보낸 상태를 게이트웨이가 바꿔 발행한다. 값은 ESP32가 잰 것이다.

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `applied_generation` | uint32 | ESP32가 마지막으로 적용한 명령의 세대 (게이트웨이가 시리얼 쪽 값을 세대로 바꿔 적는다) |
| `v_mps`, `steer_rad` | float | 실제 값 |
| `flags` | object | `estop`, `watchdog`, `obstacle` (bool) |
| `batt_v` | float | 배터리 전압 |

**`perception.result`** (젯슨)

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `lane_offset_m` | float | 차선 중심 대비 |
| `heading_err_rad` | float | 진행 방향 오차 |
| `obstacles` | array | `[{x, y, w}]` |
| `stop` | bool | 정지 필요 |

### 5.5 게이트웨이 수용 규칙

게이트웨이가 `drive.command`를 ESP32 시리얼로 넘길지 정하는 규칙이다 (시나리오 §8 D1).

1. `vehicle/control`의 `active_app` 토픽에서 온 `drive.command`만 시리얼로 넘긴다. 대기 앱 명령은 신선도 확인(5.2)에만 쓰고 넘기지 않는다.
2. `generation`이 현재 세대와 다르면 버린다.
3. 같은 세대에서 `seq`가 마지막 수신값 이하이면 버린다. 세대가 바뀌면 `seq` 기준을 새로 잡는다.
4. 넘길 명령이 없으면(`SAFE_STOP`, 활성 앱 명령 끊김) 정지 프레임을 보낸다. 마지막 명령을 반복하지 않는다.

- ESP32 쪽 규칙(세션·epoch·counter 검사, 시리얼 프레임 300 ms 끊김 정지)은 [차량 제어 상세 명세](../design/vehicle-control-spec.md)와 시리얼 프레임 형식(-241)을 따른다. MQTT의 `generation`·`seq`는 시리얼로 그대로 가지 않는다.
- 게이트웨이가 죽으면 시리얼 프레임이 끊기므로 ESP32 와치독이 차를 세운다.

## 6. 앱 이벤트

| ID | 토픽 | `type` | 발행 → 구독 | QoS | 주요 payload |
| --- | --- | --- | --- | --- | --- |
| MQTT-06 | `xenova/v1/apps/autonomous/events` | `perception.event` | 자율 VM → 메시지 보드 | 1 | `kind`(obstacle 등), `detail` |
| MQTT-06 | `xenova/v1/apps/message-board/events` | `message.posted` | 메시지 보드 → 주행 화면 | 1 | `text`, `level` |
| MQTT-06 | `xenova/v1/apps/message-board/events` | `drive.resume.requested` | 메시지 보드 → 게이트웨이 | 1 | `requested_by` |
| MQTT-06 | `xenova/v1/apps/vehicle/events` | `drive.mode.changed` | 게이트웨이 → 메시지 보드, 주행 화면, 백엔드 | 1 | `from`, `to`, `generation`, `reason` |
| MQTT-06 | `xenova/v1/apps/vehicle/events` | `drive.fault.detected` | 게이트웨이 → 메시지 보드, 백엔드 | 1 | `app_id`, `vm_id`, `reason`, `detected_at` |

- `drive.resume.requested`는 `AUTO_READY`일 때만 받아들인다. 다른 모드에서는 무시하고 `drive.mode.changed`를 내지 않는다.
- 백엔드는 `drive.mode.changed`, `drive.fault.detected`를 콘솔 이벤트 로그(REST-18, `/ws/v1/events`)에 남긴다. 심각도는 모드 전환 `INFO`, `SAFE_STOP` 진입·장애 `WARNING`.

### 6.1 앱 생존 신호 `app.heartbeat`

| ID | 토픽 | `type` | 발행 → 구독 | 주기 | QoS | 주요 payload |
| --- | --- | --- | --- | --- | --- | --- |
| MQTT-06 | `xenova/v1/apps/{app_id}/events` | `app.heartbeat` | 앱 VM → 백엔드, 메시지 보드, 주행 화면 | 1초 | 0 | `state`, `uptime_s` |

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `state` | string | `active`(활성 앱) / `standby`(대기, 주행 명령은 계속 발행) / `idle`(`vehicle/control`을 아직 못 받음, 주행과 무관한 앱의 평상시) / `error` |
| `uptime_s` | float | 앱 프로세스 시작 후 경과 초 |

- 앱 **프로세스**가 살아 있는지와 상태를 알린다. VM 생존은 `agent.heartbeat`(3장), 제어권 전환은 `drive.command` 끊김 100 ms(5장)로 판정하며 이 신호를 쓰지 않는다.
- 주행 루프와 같은 스레드에서 보낸다. 루프가 멈추면 heartbeat도 멈춰야 "프로세스는 있는데 일을 안 한다"를 잡는다.
- 받는 쪽은 3초 동안 못 받으면 그 앱을 끊김으로 표시한다.
- 에이전트가 없는 Zephyr 주행 VM에서는 이 신호가 앱 쪽 생존 정보다. 복구 서비스의 VM 재시작 판정(5.1)과 게이트웨이의 `AUTO_READY` 판정(5.2)은 Xen 도메인 상태와 이 신호를 쓴다 (시나리오 §8 D8).

## 7. 수동주행 입력

콘솔 → WebSocket → 백엔드 → MQTT → 수동 VM.

| 구간 | 규칙 |
| --- | --- |
| 콘솔 → 백엔드 | 입력 전용 WebSocket `/ws/v1/drive`. 기존 로그인(JWT)을 쓴다. 기존 `/ws/v1/metrics`, `/ws/v1/events`는 서버 → 콘솔 단방향이라 따로 둔다. 첫 프레임 `{type:"auth", access_token}` 뒤 눌린 키가 바뀔 때마다 `{type:"input", keys:[...]}`, 응답 `{type:"input.ack", result: SENT·RELEASED·NOT_MANUAL·NOT_CONNECTED}`. 연결이 끊기면 키를 모두 뗀 것으로 본다 |
| 백엔드 → 수동 VM | MQTT-11 `xenova/v1/apps/manual/input`, `type: drive.input`, QoS 0, payload `keys`(눌린 키 목록) |
| 차단 | 백엔드는 모드가 `MANUAL`이 아니면 입력을 버린다 (모드는 백엔드가 구독한 `vehicle/control` 기준) |
| 반복 | 백엔드는 키가 하나라도 눌려 있는 동안 마지막 `keys`를 50 ms마다(20 Hz) 다시 보낸다. 모두 떼면 `keys: []`를 한 번 보낸다 |
| 끊김 | 수동 VM은 마지막 `drive.input`을 받은 지 200 ms가 지나면 키를 모두 뗀 것으로 보고 `v_mps = 0`을 보낸다 |

**`drive.input`** payload

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `keys` | string[] | 눌린 키. `up` `down` `left` `right` `brake`만 쓴다. 브라우저 키 이름(`ArrowUp`, `w` 등)은 콘솔이 바꿔서 보낸다. 모르는 키는 무시한다 |

- 수동 VM의 변환: `up` 전진, `down` 후진, `left` 왼쪽(`steer_rad` +), `right` 오른쪽(`steer_rad` −). 반대 키가 같이 눌리면 그 축은 0, `brake`가 있으면 속도 0. 최대 속도·조향각은 수동 VM 설정이다.

## 8. 메시지 지연 측정

VM 간 메시지 지연은 왕복 시간(RTT)으로 잰다. 일방향 지연이라고 표시하지 않는다.

| ID | 토픽 | `type` | 방향 | QoS | retain | payload |
| --- | --- | --- | --- | --- | --- | --- |
| MQTT-07 | `xenova/v1/probes/{target_vm_id}/request` | `probe.ping` | 송신 VM → 대상 VM | 1 | X | `trial_id`, `target_vm_id`, `nonce`, `payload`(64~4096 bytes 문자열) |
| MQTT-08 | `xenova/v1/probes/{source_vm_id}/reply` | `probe.pong` | 대상 VM → 송신 VM | 1 | X | `trial_id`, `request_message_id`, `nonce`, `payload` |

- 송신 측은 발행 직전의 단조 시계로 시작 시각을 잡고, 같은 시계로 응답 수신 시각을 잰다. 송신·대상은 서로 다른 VM이어야 하며 dom0 측정 코드로 대신하지 않는다.
- 시간 초과는 실패로 기록한다. 0 ms 성공으로 채우지 않는다.

## 9. 보안·ACL

| 계정 | publish 허용 |
| --- | --- |
| 각 VM 에이전트 | `xenova/v1/vms/{자기 vm_id}/heartbeat·inventory·metrics·command-result` |
| 앱 VM | `xenova/v1/apps/{자기 app_id}/events` |
| 젯슨 | `xenova/v1/apps/perception/events` |
| 게이트웨이 | `xenova/v1/vehicle/control`, `xenova/v1/vehicle/status`, `xenova/v1/apps/vehicle/events` |
| dom0 vchan 중계 | 계정이 따로 없다. Zephyr VM마다 그 VM의 `app_id` 계정으로 접속한다 (4.1) |
| 백엔드 | `xenova/v1/apps/manual/input`, `xenova/v1/vms/{vm_id}/command` |
| 각 VM 측정 클라이언트·응답기 | `xenova/v1/probes/#` (자기 `vm_id`가 `source_vm_id`·`target_vm_id`와 맞는 것만) |
| dom0 Recovery Worker (`dom0-server`) | `xenova/v1/recovery/events`·`xenova/v1/recovery/health/{vm_id}` (11장, ACL 은 `xenova/v1/recovery/#`). 구독(read)은 `xenova/v1/vms/+/heartbeat`와 `xenova/v1/apps/+/events`(Zephyr `app.heartbeat`, 6.1). 개발 단계의 전체 읽기 허용에 포함되며 계정별 구독 제한(다음 개정)에서도 유지한다. 계정 `dom0-server`와 이 행의 실제 반영(`platform/deploy/mosquitto/etc/accounts`·`acl`)은 heartbeat 감시기(S15P31E110-124)의 완료 조건이다. Worker 는 필터별 구독 승인(SUBACK)을 보고, 거부된 신호의 손실은 판정하지 않는다 |

브로커 계정·ACL 파일은 [브로커 구성](../../platform/deploy/mosquitto/README.md)(`platform/deploy/mosquitto/etc/acl`)에서 관리한다.

- 계정 이름 = 토픽 안의 id: 에이전트는 `vm_id`, 앱 VM 은 `app_id`, 젯슨은 `perception`, 그 외 `gateway`·`backend`. ACL 이 `%u` 패턴으로 "자기 토픽에만 발행" 을 건다. ESP32는 MQTT에 붙지 않으므로 계정이 없다 (v1.1).
- 구독(read)은 개발 단계에서 모든 계정에 `xenova/v1/#` 를 연다. 계정별 구독 제한은 다음 개정(v1.2)에서 정한다.
- `probes/#` 의 "자기 vm_id 만" 조건은 payload 기준이라 브로커가 거르지 못한다. 받는 쪽이 검사한다.

## 10. 미확정

- 명령 만료 판정에 쓰는 시각 오차 허용 범위와 동기화 방식 (MQTT-04 `CLOCK_UNTRUSTED`)
- 와치독 300 ms, 전환 100 ms, `AUTO_READY` 3초는 실보드 측정 후 확정

## 11. 복구 이벤트 (dom0 → 백엔드)

자동 복구는 dom0의 Recovery Worker가 수행한다(VM API 계약 1.2절). Worker는 `xenova/v1/vms/+/heartbeat`(Linux, `agent.heartbeat`)와 `xenova/v1/apps/+/events`의 `app.heartbeat`(Zephyr, 6.1·시나리오 D8)를 직접 구독해 Xen 도메인 상태와 함께 장애를 판정하고, 시도마다 아래 토픽으로 이벤트를 낸다. 객체 정의는 VM API 계약 2.5절이다. VM 마다의 현재 건강은 11.1 의 retained 스냅샷이다.

| ID | 토픽 | `type` | 방향 | QoS | retain | payload |
| --- | --- | --- | --- | --- | --- | --- |
| MQTT-12 | `xenova/v1/recovery/events` | `recovery.started` / `recovery.completed` / `recovery.failed` | dom0 Recovery Worker → 백엔드 | 1 | X | VM API 계약 2.5절 복구 이벤트 객체 |
| MQTT-13 | `xenova/v1/recovery/health/{vm_id}` | `vm.health` | dom0 Recovery Worker → 게이트웨이·백엔드 | 1 | O | 11.1 건강 스냅샷 |

- 두 토픽 모두 봉투의 발행자는 `source_id = "dom0-server"`다(2장). MQTT-12 복구 이벤트의 `vm_id`는 payload 안에 있다(이 토픽에는 `{vm_id}`가 없다). MQTT-13 은 토픽에 `{vm_id}`가 있고 payload 의 `vm_id` 도 같은 값이다(11.1).
- 백엔드는 `event_id`로 중복을 거르고 `EventType` `recovery.*`로 기록한다(VM API 계약 5장). 백엔드가 재시작해 빠진 구간은 `GET /api/v1/recovery/events?after_event_id=`(VM API 계약 4.7절)로 채운다.
- Worker가 Spring 연결이나 브로커 없이도 복구를 수행하는 것이 전제이므로, 이 토픽은 알림일 뿐 복구 실행의 조건이 아니다.
- 브로커 계정 `dom0-server`(publish `xenova/v1/recovery/#`, read `xenova/v1/vms/+/heartbeat`·`xenova/v1/apps/+/events`. 9장과 같다)는 `platform/deploy/mosquitto/etc/accounts`·`acl`에 포함한다. 배포 시 실제 계정 비밀번호를 발급하고 Worker의 root 전용 설정에 전달한다.
- 제어권 전환은 게이트웨이의 `drive.mode.changed`(6장, MQTT-06)로 알리며 이 토픽에 싣지 않는다.
- `vms/{vm_id}/` 아래 토픽은 봉투 `vm_id`와 토픽이 같아야 하므로(2장) VM 이 아닌 발행자는 쓸 수 없다. 그래서 VM 별 건강도 `recovery/` 가지에 둔다.

### 11.1 건강 스냅샷 `vm.health`

Worker 가 VM 마다 내는 현재 건강이다(주행 명세 3.3 의 게이트웨이 입력 `ok`·`desired_state`·`manager_seq`). retained 라 새 구독자는 마지막 값을 바로 받는다. 내용이 바뀌면 바로 내고, 같아도 `lease_s` 의 1/3(기본 15초)마다 다시 낸다. **받는 쪽은 마지막 수신(자기 단조 시계) 뒤 `lease_s` 가 지나면 그 VM 을 `OBSERVATION_UNAVAILABLE` 로 본다.** Worker 가 죽거나 브로커와 끊기면 retained 값이 오래된 채 남기 때문이다. 구독 직후 브로커가 주는 마지막 값(MQTT retain 플래그가 켜진 수신)은 **임시값**이다. 브로커는 그 값이 저장된 지 얼마나 됐는지 알려주지 않으므로, 받는 쪽은 그것으로 `ok` 를 믿지 않고 Worker 가 다시 낸 실시간 값(retain 플래그 없음)을 받은 뒤 확정하며 lease 도 실시간 값의 수신부터 센다. `OBSERVATION_UNAVAILABLE`·`STOPPED`·`UNHEALTHY` 같은 비정상 값은 임시값이라도 그대로 표시해도 된다. 공용 클라이언트는 이 플래그를 `Message.retained` 로 준다. 복구 판정 유예와 "건강하다"를 분리하기 위해 `ok` 는 `state` 가 `HEALTHY` 일 때만 `true` 다.

```json
{
  "vm_id": "550e8400-e29b-41d4-a716-446655440000",
  "ok": false,
  "state": "STARTING",
  "desired_state": "running",
  "xen_state": "running",
  "heartbeat_age_s": null,
  "reason": null,
  "manager_seq": 3,
  "lease_s": 45
}
```

| 필드 | 의미 |
| --- | --- |
| `vm_id` | 토픽의 `{vm_id}` 와 같다 |
| `ok` | `state == HEALTHY` 일 때만 `true` |
| `state` | `HEALTHY`(실행 중이고 생존 신호가 신선함. 신호가 없는 종류는 Xen 실행만 근거) / `STARTING`(실행 중이지만 이번 부팅의 생존 신호 전, 유예 중) / `UNHEALTHY`(판정 사유가 있거나 `paused`·`stopping`, 신호 끊김) / `STOPPED`(도메인이 없고 의도가 `running` 이 아님) / `OBSERVATION_UNAVAILABLE`(생존 신호를 관측할 수 없음: 구독 미승인, 또는 Worker 의 Zephyr 대상 기록 손상. **장애가 아니다**) |
| `desired_state` | 플랫폼 registry 의도 `running` / `stopped` / `unknown` |
| `xen_state` | VM API 계약 3.2 의 Xen 상태, 도메인이 없으면 `null` |
| `heartbeat_age_s` | 이번 부팅에서 마지막 생존 신호(Linux `agent.heartbeat`, Zephyr `app.heartbeat`)를 받은 뒤 지난 초. 없으면 `null` |
| `reason` | 복구 판정이 내려진 상태면 VM API 계약 2.5 의 `reason`, 아니면 `null` |
| `manager_seq` | 같은 VM 에서 내용이 바뀔 때만 1 증가(주기 재발행에서는 그대로, `heartbeat_age_s` 만 갱신). 받는 쪽이 재시작 뒤 retained 값과 새 값을 구별한다 |
| `lease_s` | 이 스냅샷의 유효 시간(초). Worker 는 이 시간의 1/3 마다 다시 낸다. 받는 쪽은 마지막 수신 뒤 이 시간이 지나면 `OBSERVATION_UNAVAILABLE` 로 본다 |

- 봉투의 발행자는 `source_id = "dom0-server"` 다. 받는 쪽은 토픽의 `{vm_id}` 와 payload `vm_id` 가 같은 것만 쓴다.
- Linux VM 의 `HEARTBEAT_LOST` 판정은 30초, Zephyr VM 의 `APP_HEARTBEAT_LOST` 는 15초 무수신이다(VM API 계약 2.5). 게이트웨이의 제어권 전환은 이 스냅샷이 아니라 `drive.command` 신선도(5장)로 한다.
- VM 이 등록에서 사라지면(삭제·보관) Worker 가 빈 payload(QoS 1, retain)로 retained 를 지우고 PUBACK 까지 확인한다. 지우지 못했으면 플랫폼 관측과 무관하게 다음 주기에 다시 시도한다.
- Worker 가 정상 종료할 때는 남은 VM 전부를 `OBSERVATION_UNAVAILABLE` 로 다시 내고 PUBACK 까지 확인한다(`manager_seq` 증가). 비정상 종료면 받는 쪽의 `lease_s` 만료가 같은 결론을 낸다.

## 관련 문서

- [VM 관리 API 계약 v1](vm-api-contract-v1.md) — `vm_id`, `guest_kind`
- [아키텍처 설계서](../architecture.md)
- [차량 제어 상세 명세](../design/vehicle-control-spec.md)
- [RC카 시나리오](../scenario-rc-car.md) §8 — D1·D3·D8 결정

## 변경 이력

| 버전 | 날짜 | 내용 |
| --- | --- | --- |
| v1 | 2026-10-19 | 최초. 에이전트 토픽(구현과 같음), RC카 주행 제어·앱 이벤트·지연 측정·ACL |
| v1 (보완) | 2026-10-20 | 접두사 `xenova/v1/`, 주행 VM Zephyr(4장), `app.heartbeat`(6.1), 수동주행 입력(7장) |
| v1.1 | 2026-10-21 | 시나리오 D1·D3·D8 반영 (S15P31E110-242). ESP32를 MQTT 참여자에서 빼고 게이트웨이 시리얼 뒤로(4장, 5.3, 9장 계정 삭제), `vehicle.status` 발행 = 게이트웨이 `source_id: dom0-gateway`(5.4), 5.5 ESP32 수용 규칙 → 게이트웨이 수용 규칙, Zephyr vchan + dom0 중계(4.1, 10장에서 삭제), `AUTO_READY`·VM 재시작 근거에서 에이전트 대신 도메인 상태 + `app.heartbeat`(5.1·5.2·6.1). 토픽 이름·payload 필드는 그대로 |
| v1.2 | 2026-10-22 | 복구 Worker 건강 스냅샷(S15P31E110-124): MQTT-13 `vm.health`(11.1, `lease_s` 수명과 주기 재발행·수신 측 만료 규칙, retain 플래그 수신은 임시값), 계정 `dom0-server` 의 구독에 `apps/+/events`·publish 에 `recovery/health/{vm_id}` 추가(9장), 11장 판정 입력에 Zephyr `app.heartbeat` 명시. 기존 토픽·payload 는 그대로 |
