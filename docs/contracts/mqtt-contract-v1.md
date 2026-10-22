# MQTT 메시지 계약 v1

**최종 갱신** 2026-10-22 v1.5 초안 (v1.4 위에 자율주행 자동 복귀 3·2·1 + 취소 — 5.2·6장) · v1.4 초안 (v1.3 위에 11장 실제 서킷 트랙·출발 신호·장애물 종류·배치 검사 결과 MQTT-18·REST-36~37 — 변경 이력)
**대상 시스템** Raspberry Pi 4 + Xen 멀티 OS 관리 플랫폼, RC카 시나리오
**브로커** dom0 Mosquitto

VM 에이전트, RC카 앱 VM, dom0 서버·게이트웨이, 젯슨이 주고받는 MQTT 메시지의 기준 문서입니다. ESP32는 MQTT에 붙지 않고 게이트웨이를 거친다(4장). 에이전트 수신부는 이미 백엔드에 구현돼 있고(`backend/.../agent/`), 이 문서의 2장·3장은 그 구현과 같습니다. 4장 이후 RC카 메시지는 새로 정하는 내용입니다.

`MQTT-NN`은 노션 인터페이스 계약의 MQTT 계약 ID입니다. 기준은 이 문서이고 노션 행은 요약입니다. 어긋나면 **구현 코드 → 이 문서 → 노션** 순으로 따릅니다 (2026-10-21, S15P31E110-249).

토픽·필드·QoS를 바꾸면 발행하는 쪽과 구독하는 쪽 담당이 함께 리뷰합니다. 한쪽만 바꾸면 메시지가 조용히 버려집니다.

---

## 1. 공통 규칙

| 항목 | 규칙 |
| --- | --- |
| 토픽 접두사 | `xenova/v1/`. 10/20 이름 통일로 접두사도 바꿨다. 이전 `perseus/v1/`는 어디에도 배포된 적이 없어 호환 처리는 없다 |
| 인코딩 | UTF-8 JSON, 필드 이름은 snake_case |
| 시각 | `sent_at`은 ISO-8601 UTC. **보드 간 시각 비교로 판정하지 않는다.** 신선도는 받는 쪽이 수신 시각으로 잰다 |
| 단위 | 속도 `m/s`, 조향 `rad`, 시간 `ms`, 메트릭 `percent`·`bytes` |
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
- metrics의 `unit`은 `percent`, `bytes`만 받는다. 그 외는 `METRIC_UNIT_MISMATCH`.
- heartbeat 주기는 5초. 에이전트 상태는 **백엔드가 heartbeat 나이로 판정**한다 (`ONLINE` → 15초 `STALE` → 30초 `OFFLINE`). 에이전트가 스스로 상태를 선언하지 않는다.
- heartbeat는 **VM 재시작 판정에만** 쓴다. 주행 제어권 전환 판정에는 쓰지 않는다 (5장).

## 4. RC카 구성과 app_id

| app_id | 발행자 | `guest_kind` | 역할 |
| --- | --- | --- | --- |
| `autonomous` | 자율주행 VM | `zephyr` | 젯슨 인식 결과로 판단·경로 생성, 평소 주행 명령 |
| `manual` | 수동주행 VM | `zephyr` | 대기, 인계 시 콘솔 키 입력을 주행 명령으로 변환 |
| `message-board` | 메시지 보드 VM | `linux` | 관제 메시지, 모드·장애 알림 표시, 자율주행 재개 요청, 카메라 AI 감지(MQTT-14, 주행 판단에 안 씀) |
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
| `AUTO` | autonomous | 시작, `AUTO_READY`에서 자동 복귀 카운트다운이 끝났을 때 (v1.5), 또는 자율주행 재개 요청 |
| `MANUAL` | manual | `AUTO`에서 자율 명령이 100 ms 끊기고 수동 명령이 신선할 때, `SAFE_STOP`에서 수동 명령이 신선해졌을 때, 또는 `AUTO_READY` 조건이 깨졌을 때 |
| `SAFE_STOP` | 없음 | 활성 앱 명령이 끊겼는데 넘길 앱이 없을 때 |
| `AUTO_READY` | manual | `MANUAL` 중 복구된 자율 VM이 3초 연속 아래를 모두 만족할 때: Xen 도메인 실행 중, `app.heartbeat` 신선(`state`가 `error` 아님), `drive.command` 신선 |

전환 표 (게이트웨이 구현 `platform/services/gateway` 와 같다):

| 지금 | 조건 | 다음 | `reason` |
| --- | --- | --- | --- |
| (시작) | — | `AUTO` | `start` (`drive.mode.changed`의 `from`은 null) |
| `AUTO` | 자율 명령 100 ms 끊김 + 수동 명령 신선 | `MANUAL` | `command_timeout` |
| `AUTO` | 자율 명령 100 ms 끊김 + 수동도 신선하지 않음 | `SAFE_STOP` | `no_fresh_standby` |
| `MANUAL` | 위 `AUTO_READY` 조건 3초 충족 | `AUTO_READY` | `autonomous_ready` |
| `MANUAL`·`AUTO_READY` | 수동 명령 100 ms 끊김 (자율이 신선해도) | `SAFE_STOP` | `no_fresh_standby` |
| `AUTO_READY` | `AUTO_READY` 조건이 깨짐 | `MANUAL` | `autonomous_lost` |
| `AUTO_READY` | 들어간 뒤 자동 복귀 3초(`drive.resume.accepted`, `requested_by: auto`)가 끝나고 조건이 그대로 (v1.5) | `AUTO` | `auto_resumed` |
| `AUTO_READY` | `drive.resume.requested` (자동 복귀를 취소했거나 카운트다운 밖) | `AUTO` | `resume_requested` |
| `SAFE_STOP` | 수동 명령 신선 | `MANUAL` | `manual_fresh` |

- `AUTO`에는 시작, 자동 복귀, 재개로만 들어간다. `SAFE_STOP`에서 자율 명령이 신선해져도 `AUTO`로 가지 않는다 (사람이 확인한 뒤 수동 → 재개).
- **자동 복귀 (v1.5, 2026-10-22 결정)**: `AUTO_READY`에 들어가면 게이트웨이가 운영자 조작 없이 바로 `drive.resume.accepted`(`requested_by: auto`, `switch_in_ms: 3000`)를 내고 3·2·1 을 센다. 끝날 때 조건이 그대로면 `AUTO`(`auto_resumed`). 카운트다운 중 **취소 요청**(6장 `drive.resume.cancel_requested`)이 오면 바꾸지 않고 `drive.resume.cancelled`(`reason: operator_cancelled`)를 낸 뒤 수동이 계속 몬다. 취소한 뒤에는 이번 `AUTO_READY` 동안 다시 자동 복귀하지 않는다 — 운영자 재개 요청으로만 `AUTO`. `AUTO_READY`를 벗어났다가 다시 들어오면 자동 복귀를 다시 한다.

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
| `reason` | string | 전환 사유 (`start`, `command_timeout`, `no_fresh_standby`, `autonomous_ready`, `autonomous_lost`, `resume_requested`, `manual_fresh` — 5.2 전환 표) |

**`vehicle.status`** (게이트웨이, `source_id: dom0-gateway`)

ESP32가 시리얼로 보낸 상태를 게이트웨이가 바꿔 발행한다. 값은 ESP32가 잰 것이다.

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `applied_generation` | uint32 | ESP32가 마지막으로 적용한 명령의 세대 (게이트웨이가 시리얼 쪽 값을 세대로 바꿔 적는다) |
| `v_mps`, `steer_rad` | float | 실제 값 |
| `flags` | object | `estop`, `watchdog`, `obstacle` (bool) |
| `batt_v` | float | 선택. 배터리 전압 — ESP32 에 전압 측정 회로가 있을 때만 넣고, 없으면 필드를 생략한다. 받는 쪽은 없을 수 있다고 보고 화면에 띄우지 않아도 된다 |

**`perception.result`** (젯슨, `source_id: jetson`)

좌표는 차량 기준 지면 좌표다. 원점 = 카메라 바로 아래 지면, x = 전방(m), y = 왼쪽이 +(m). (PC 대역 구현 `platform/apps/perception` 과 같다)

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `lane_offset_m` | float | 차선 중심 대비 차의 위치. + 이면 차가 중심보다 왼쪽. 차선을 못 찾으면 0 |
| `heading_err_rad` | float | 진행 방향 오차. + 이면 차선이 왼쪽으로 돈다 (`steer_rad` + 와 같은 쪽). 차선을 못 찾으면 0 |
| `obstacles` | array | `[{x, y, w}]` — 장애물 아랫변 가운데의 위치(m)와 아랫변 폭(m). 가까운 순, 최대 8개 |
| `stop` | bool | 정지 필요 (전방 정지 영역 안에 장애물) |

- 새 인식 결과가 0.5초 넘게 없으면 발행을 멈춘다 (오래된 결과를 반복하지 않는다 — 젯슨 끊김이 받는 쪽에서 보이게).

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
| MQTT-06 | `xenova/v1/apps/autonomous/events` | `perception.event` | 자율 VM → 메시지 보드 | 1 | `kind`(`obstacle`·`obstacle_cleared`·`perception_lost`·`perception_restored`), `detail`(예 `stop 80 cm`) — 활성일 때 판단이 바뀌면 |
| MQTT-06 | `xenova/v1/apps/message-board/events` | `message.posted` | 메시지 보드 → 주행 화면 | 1 | `text`, `level` |
| MQTT-06 | `xenova/v1/apps/message-board/events` | `drive.resume.requested` | 메시지 보드 → 게이트웨이 | 1 | `requested_by` |
| MQTT-06 | `xenova/v1/apps/message-board/events` | `drive.resume.cancel_requested` | 메시지 보드 → 게이트웨이 | 1 | `requested_by` (v1.5) |
| MQTT-06 | `xenova/v1/apps/vehicle/events` | `drive.mode.changed` | 게이트웨이 → 메시지 보드, 주행 화면, 백엔드 | 1 | `from`, `to`, `generation`, `reason` |
| MQTT-06 | `xenova/v1/apps/vehicle/events` | `drive.fault.detected` | 게이트웨이 → 메시지 보드, 백엔드 | 1 | `app_id`, `vm_id`, `reason`(`command_timeout`), `detected_at` |
| MQTT-06 | `xenova/v1/apps/vehicle/events` | `drive.resume.accepted` | 게이트웨이 → 메시지 보드, 주행 화면, 백엔드 | 1 | `switch_in_ms`(3000), `generation`(지금 세대), `requested_by` (자동 복귀면 `auto`, v1.5) |
| MQTT-06 | `xenova/v1/apps/vehicle/events` | `drive.resume.cancelled` | 게이트웨이 → 메시지 보드, 주행 화면, 백엔드 | 1 | `reason`(`autonomous_lost`, `operator_cancelled`(v1.5) 등), `generation` |
| MQTT-13 | `xenova/v1/console/resume` | `drive.resume.requested` | 백엔드(운영자 콘솔) → 게이트웨이 | 1 | `requested_by` |
| MQTT-13 | `xenova/v1/console/resume` | `drive.resume.cancel_requested` | 백엔드(운영자 콘솔) → 게이트웨이 | 1 | `requested_by` (v1.5, 백엔드가 `DELETE /api/v1/drive/resume`을 받아 발행) |
| MQTT-14 | `xenova/v1/apps/message-board/vision` | `vision.result` | 메시지 보드 → 백엔드 (화면·기록) | 0 | `frame_id`, `fps`, `objects[{label, score, bbox[x,y,w,h]}]` (bbox 는 0~1 정규화) |

- `drive.resume.requested`는 `AUTO_READY`일 때만 받아들인다. 다른 모드에서는 무시하고 `drive.mode.changed`를 내지 않는다. 메시지 보드(MQTT-06)와 운영자 콘솔(MQTT-13, 백엔드가 `POST /api/v1/drive/resume`을 받아 발행) 어느 쪽에서 와도 같다.
- 재개 요청을 내는 쪽은 메시지 보드 VM(MQTT-06)과 운영자 콘솔(MQTT-13, 백엔드) 둘이다 (9장 ACL).
- `drive.fault.detected`는 한 번이라도 명령을 낸 주행 앱(활성·대기 모두)의 `drive.command`가 100 ms 끊기면 한 번 낸다. 명령이 다시 오면 다시 낼 수 있다.
- **재개는 바로 바꾸지 않는다 (3·2·1).** 받아들이면 게이트웨이가 먼저 `drive.resume.accepted`(`switch_in_ms: 3000`)를 내고, 3초 동안 수동주행이 계속 제어한다. 화면은 이 이벤트로 "3, 2, 1" 을 센다. 3초 뒤 `AUTO_READY` 조건(5.2)이 여전히 맞으면 `AUTO`로 바꾸고 `drive.mode.changed`(`reason: resume_requested`)를 낸다. 그 사이 조건이 깨지면 바꾸지 않고 `drive.resume.cancelled`를 낸다. 카운트다운 중 들어온 재개 요청은 무시한다. 자동 복귀(v1.5)도 같은 3·2·1 이고, `requested_by`만 `auto`다.
- **재개 취소 (v1.5)**: `drive.resume.cancel_requested`는 카운트다운 중일 때만 받아들이고 `drive.resume.cancelled`(`operator_cancelled`)로 답한다. 카운트다운이 없으면 무시한다. 콘솔은 `DELETE /api/v1/drive/resume`(202) → 백엔드가 MQTT-13 에 발행.
- **`vision.result`(MQTT-14)는 주행 판단에 쓰지 않는다.** 메시지 보드 VM 이 카메라 영상을 AI 로 본 결과로, 화면·기록용이다. 게이트웨이·자율주행·수동주행 VM 은 구독하지 않는다 — 주행 판단은 `perception.result`(5.4) 하나. 그래서 메시지 보드 VM 이 죽어도 주행은 그대로다 (격리). 10 Hz 이하, QoS 0, latest-only, 새 결과가 없으면 내지 않는다. 필드·영상 경로는 [카메라·AI 감지 계약](camera-stream-v1.md).
- 백엔드는 `drive.mode.changed`, `drive.fault.detected`, `drive.resume.accepted`, `drive.resume.cancelled`를 콘솔 이벤트 로그(REST-18, `/ws/v1/events`)에 남긴다. 심각도는 모드 전환 `INFO`, `SAFE_STOP` 진입·장애 `WARNING`.

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
| 메시지 보드 VM (`message-board`) | 위에 더해 `xenova/v1/apps/message-board/vision` (MQTT-14) |
| 젯슨 | `xenova/v1/apps/perception/events` |
| 게이트웨이 | `xenova/v1/vehicle/control`, `xenova/v1/vehicle/status`, `xenova/v1/apps/vehicle/events` |
| dom0 vchan 중계 | 계정이 따로 없다. Zephyr VM마다 그 VM의 `app_id` 계정으로 접속한다 (4.1) |
| 백엔드 | `xenova/v1/apps/manual/input`, `xenova/v1/console/resume`, `xenova/v1/vms/{vm_id}/command`, `xenova/v1/sim/command` (11장) |
| 시뮬레이터 (`simulator`, 시뮬레이터 구성) | `xenova/v1/sim/pose`, `xenova/v1/sim/world`, `xenova/v1/sim/result` (11장) |
| 각 VM 측정 클라이언트·응답기 | `xenova/v1/probes/#` (자기 `vm_id`가 `source_vm_id`·`target_vm_id`와 맞는 것만) |

브로커 계정·ACL 파일은 [브로커 구성](../../platform/deploy/mosquitto/README.md)(`platform/deploy/mosquitto/etc/acl`)에서 관리한다.

- 계정 이름 = 토픽 안의 id: 에이전트는 `vm_id`, 앱 VM 은 `app_id`, 젯슨은 `perception`, 그 외 `gateway`·`backend`. ACL 이 `%u` 패턴으로 "자기 토픽에만 발행" 을 건다. ESP32는 MQTT에 붙지 않으므로 계정이 없다 (v1.1).
- MQTT-14 `vision` 은 backend 가 읽는다. 구독은 아래처럼 모두 열려 있으므로 "주행 쪽은 구독하지 않는다"(6장)는 각 앱이 지킨다.
- 구독(read)은 개발 단계에서 모든 계정에 `xenova/v1/#` 를 연다. 계정별 구독 제한은 다음 개정(v1.2)에서 정한다.
- `probes/#` 의 "자기 vm_id 만" 조건은 payload 기준이라 브로커가 거르지 못한다. 받는 쪽이 검사한다.

## 10. 미확정

- 명령 만료 판정에 쓰는 시각 오차 허용 범위와 동기화 방식 (MQTT-04 `CLOCK_UNTRUSTED`)
- 와치독 300 ms, 전환 100 ms, `AUTO_READY` 3초는 실보드 측정 후 확정

## 11. 시뮬레이터 전용 (실차에는 없음)

차·카메라를 시뮬레이터로 둔 구성(데모 기본, [시뮬레이터 시연 시나리오](../scenario-simulator.md))에서만 쓰는 메시지·API 다. 실차 구성에는 이 토픽이 없고, 받는 쪽은 없어도 동작해야 한다. 시뮬레이터는 ESP32 자리(게이트웨이 시리얼 뒤)에 있으므로 `vehicle.status` 는 그대로 게이트웨이가 낸다 — 아래는 **실차에 없는 값**(위치·트랙)과 **시연 조작**만이다.

### 11.1 토픽

| ID | 토픽 | `type` | 발행 → 구독 | 주기 | QoS | retain |
| --- | --- | --- | --- | --- | --- | --- |
| MQTT-15 | `xenova/v1/sim/pose` | `sim.pose` | 시뮬레이터 → 백엔드, 인지 대역 | 20 Hz | 0 | X |
| MQTT-16 | `xenova/v1/sim/world` | `sim.world` | 시뮬레이터 → 백엔드, 인지 대역 | 바뀔 때 + 시작 | 1 | O |
| MQTT-17 | `xenova/v1/sim/command` | `sim.command` | 백엔드(운영자 콘솔) → 시뮬레이터 | 조작할 때 | 1 | X |
| MQTT-18 | `xenova/v1/sim/result` | `sim.command.result` | 시뮬레이터 → 백엔드 | 명령마다 1번 | 1 | X |

- 발행자 `source_id`: 시뮬레이터 `simulator`, 백엔드 `backend`. 봉투·seq 규칙은 2장과 같다.
- 좌표는 **트랙 고정 좌표**다 (5.4 `perception.result` 의 차량 기준 좌표와 다르다). 원점 = 트랙 중심, x = 오른쪽, y = 위쪽(m), `yaw` = +x 에서 반시계 rad. 인지 대역이 이 값으로 차량 기준 `perception.result` 를 계산한다 (S15P31E110-268).
- 값은 모두 **모의 단위**(m, m/s)다. 화면은 트랙의 `real_m` ÷ 중심선 길이(약 51)를 곱해 **실제 서킷 단위**(km, km/h, m)로 보인다 (v1.4). 계약 값은 바꾸지 않는다 — 실차 확장에서는 같은 값이 실제 RC카 단위다.

**`sim.pose`**

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `x`, `y` | float | 차 뒷바퀴 축 가운데 위치 (m) |
| `yaw` | float | 진행 방향 (rad, −π ~ π) |
| `v_mps`, `steer_rad` | float | 지금 속도·조향 (모델 값). 후진이면 `v_mps` < 0 |
| `world_revision` | uint32 | 이 위치를 계산할 때의 `sim.world.revision` |
| `surface` | string | 차 중심이 있는 노면 `road` · `grass` · `dirt` (v1.4). 잔디·흙이면 최고 속도가 줄어든다 |
| `race` | object | 출발 신호·랩 (v1.4). 아래 표 |

`race` (v1.4)

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `phase` | string | `lights`(출발 신호 중, 차는 출발선) · `go`(출발 뒤) |
| `lights_on` | uint8 | 켜진 빨간 불 수 0~5. F1 방식: 1초마다 하나씩 켜지고, 다섯 개가 켜진 뒤 0.2~3초(무작위) 지나 **모두 꺼지면** `go` |
| `lap` | uint32 | 끝낸 바퀴 수 (출발 뒤 0) |
| `lap_ms` | uint32 | 지금 바퀴 경과 시간 (`go` 전에는 0) |
| `last_lap_ms`, `best_lap_ms` | uint32 \| null | 지난 바퀴·가장 빠른 바퀴 |
| `jump_start` | bool | 불이 꺼지기 전에 차가 움직였다. 첫 바퀴에 5000 ms 를 더한다 |

**`sim.world`**

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `revision` | uint32 | 트랙·장애물·E-stop 이 바뀔 때마다 1 오른다. 시작 1. 시뮬레이터가 다시 시작하면 1 부터 다시 세므로, 받는 쪽은 revision 이 줄어도 새 실행으로 보고 받는다. 시뮬레이터는 정상 종료할 때 retained 를 지운다 |
| `track` | object | 지금 트랙. 아래 표 (v1.4 에서 타원을 실제 서킷 중심선으로 바꿈) |
| `tracks` | array | 고를 수 있는 트랙 `[{id, name, real_m}]` (v1.4). 10개: `spielberg`(레드불링 4315 m)·`monza`(몬차 5790 m)·`silverstone`·`spa`·`melbourne`·`hungaroring`·`catalunya`·`mexico`·`bahrain`·`interlagos`. 시작은 `spielberg` |
| `car` | object | `{length_m, width_m}` 판정에 쓰는 차 몸 크기. 기본 `0.42`·`0.20` (v1.4) |
| `time_scale` | float | 시뮬레이터 시간 배율 (1 = 실제 시간, 기본 1). 화면이 10 Hz 위치 사이를 이어 그릴 때 곱한다 (v1.4). 시연에서 보기 좋은 빠르기로 3 을 쓸 수 있다 — 와치독·명령 주기는 실제 시간 그대로 |
| `obstacles` | array | `[{id, kind, x, y, w}]` — 종류·가운데 위치(m)·폭(m). 최대 8개. `kind` 는 v1.4 |
| `estop` | bool | 시연용 E-stop 이 걸려 있음 (`vehicle.status.flags.estop` 도 같이 true) |

`track` (v1.4)

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `id`, `name` | string | 트랙 id·화면 이름 |
| `real_m` | uint32 | 실제 서킷 한 바퀴 길이 (m). 화면 단위 변환에 쓴다 |
| `lane_width_m`, `curb_m` | float | 차선 폭 `0.6`, 연석 폭 `0.06`. 연석 끝까지 아스팔트 |
| `centerline` | array | `[[x, y], …]` 차선 중심선, 약 0.2 m 간격, 닫힌 고리 (진행 방향 순서). 트랙마다 약 410 ~ 680점 |
| `wall_left_m`, `wall_right_m` | array | 점마다 중심선에서 왼쪽·오른쪽 벽까지 거리 (m). 연석 밖 ~ 벽 = 잔디·흙 (지나갈 수 있지만 느리다). 출발 직선은 잔디 없이 바로 벽, 코너 근처는 보통의 두 배 |
| `start_index` | uint32 | 출발선이 있는 `centerline` 점 |

- 트랙 모양 출처: TUM racetrack-database (LGPL-3.0, © OpenStreetMap contributors) 중심선을 1/50 로 줄이고 반경 0.8 m 미만 코너만 다듬었다. 벽 거리는 시뮬레이터가 계산해 넣는다 — 받는 쪽은 그대로 쓴다.
- `sim.world` 는 가장 긴 스파(681점) 기준 약 30 KB (1장 64 KB 이하).
- 10개를 고른 기준: 1/50 로 줄였을 때 원본과 차이 1 m 이하, 다른 구간과 1.2 m 넘게 떨어짐. 스즈카처럼 구간이 겹쳐 지나가는 트랙은 2차원 트랙 좌표로 나타낼 수 없어 뺐다.

**`sim.command`**

| `op` | 인자 | 동작 |
| --- | --- | --- |
| `obstacle.add` | `obstacle_id`, `kind`, `x`, `y` | 장애물 놓기. `kind` = `cone`(폭 0.18) · `box`(0.30) · `tires`(0.26) — 폭은 종류로 정한다 (v1.4, v1.3 의 `w` 는 받기만 하고 무시). `obstacle_id` 는 백엔드가 만든다. 이미 있으면 무시 |
| `obstacle.move` | `obstacle_id`, `x`, `y` | 옮기기 |
| `obstacle.remove` | `obstacle_id` | 치우기 |
| `estop` | `engaged` (bool) | E-stop 걸기·풀기 |
| `push` | `dy_m` | 차를 진행 방향 기준 옆으로 옮긴다 (+ 왼쪽, \|dy_m\| ≤ 0.5). 자율주행이 차선으로 돌아오는지 보이는 시연용 |
| `track.select` | `track_id` | 트랙 바꾸기 (v1.4). 장애물을 모두 치우고 차를 출발선에 세운 뒤 출발 신호를 처음부터 |
| `race.start` | — | 다시 출발 (v1.4). 차를 출발선에 세우고 출발 신호를 처음부터 |

- 모든 명령에 `request_id`(string)를 넣는다. 시뮬레이터는 명령마다 MQTT-18 `sim.command.result` 를 한 번 낸다 (v1.4). 적용되면 `sim.world`(revision +1)도 다시 낸다 — `push`·`race.start` 는 `sim.pose` 에만 보인다.
- 모르는 `op`·범위 밖 값은 시뮬레이터가 버리고 로그를 남기며 `ok: false`, `code: INVALID` 로 답한다 (9번째 장애물, \|dy_m\| > 0.5, 없는 `track_id`).

**배치 검사 (v1.4)** — `obstacle.add`·`obstacle.move` 는 **이 차가 실제로 지나갈 수 있을 때만** 적용한다. 못 지나가는 자리는 놓지 않고 거부한다.

1. 틈: 그 장애물 앞뒤로 차 길이 안에서, 장애물·벽 사이 가장 넓은 틈이 차 폭 + 양옆 여유(0.03 m) = **0.26 m** 보다 좁으면 `NO_GAP`
2. 실제 주행: 그 장애물과 이어진 장애물 묶음을 자율주행 규칙으로 미리 돌려 보아, 서거나 닿으면 `TOO_DENSE`
3. 차가 바로 앞(4.8 m 안)에서 달리는 중이면 지금 위치·속도로도 돌려 보아, 못 피하면 `TOO_CLOSE`
4. 차 몸과 겹치면 `ON_CAR`, 벽 밖이면 `OUT_OF_TRACK`

**`sim.command.result`** (MQTT-18, v1.4)

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `request_id` | string | 받은 명령의 `request_id` |
| `op` | string | 받은 명령의 `op` |
| `ok` | bool | 적용했나 |
| `code` | string \| null | 거부 사유 `NO_GAP` · `TOO_DENSE` · `TOO_CLOSE` · `ON_CAR` · `OUT_OF_TRACK` · `INVALID` (`ok` 이면 null) |
| `message` | string \| null | 화면에 그대로 띄울 한 줄. 거리는 실제 단위 (예: "차가 지나갈 틈이 없어요 — 가장 넓은 틈 11.2 m < 필요 13.3 m") |
| `gap_m`, `need_m` | float \| null | `NO_GAP` 일 때 모의 단위 틈·필요 폭 |

### 11.2 콘솔 API (백엔드)

인증은 다른 콘솔 API 와 같은 사용자 JWT, 오류 본문은 [VM 관리 API 계약](vm-api-contract-v1.md) 8.2 `{code, message}`.

| ID | 메서드 | 경로 | 요청 | 성공 | 오류 |
| --- | --- | --- | --- | --- | --- |
| REST-30 | GET | `/api/v1/sim/state` | — | 200 `{available, pose, world, pose_age_ms}` | 401 |
| REST-31 | POST | `/api/v1/sim/obstacles` | `{kind, x, y}` (`kind` 기본 `cone`, v1.4. v1.3 의 `w` 는 무시) | 202 `{obstacle_id, request_id}` | 401, 409, 422 |
| REST-32 | PUT | `/api/v1/sim/obstacles/{obstacle_id}` | `{x, y}` | 202 `{request_id}` | 401, 404, 409, 422 |
| REST-33 | DELETE | `/api/v1/sim/obstacles/{obstacle_id}` | — | 202 `{request_id}` | 401, 404, 409 |
| REST-34 | POST | `/api/v1/sim/estop` | `{engaged}` | 202 `{request_id}` | 401, 409, 422 |
| REST-35 | POST | `/api/v1/sim/push` | `{dy_m}` | 202 `{request_id}` | 401, 409, 422 |
| REST-36 | POST | `/api/v1/sim/track` | `{track_id}` (v1.4) | 202 `{request_id}` | 401, 409, 422 |
| REST-37 | POST | `/api/v1/sim/race/start` | — (v1.4) | 202 `{request_id}` | 401, 409 |
| WS-06 | WS | `/ws/v1/sim` | 첫 프레임 `{type:"auth", access_token}` | `sim.snapshot` 푸시 | 인증 실패 시 닫음 |

- `available` = 마지막 `sim.pose` 가 1초 안. `pose`·`world` 는 마지막으로 받은 payload 그대로 (없으면 null).
- 409 `SIM_UNAVAILABLE`: 시뮬레이터가 없음(`available` false). 404 `OBSTACLE_NOT_FOUND`: 백엔드가 아는 마지막 `sim.world` 에 그 id 가 없음. 422 `VALIDATION_ERROR`: 필드 타입·범위 (\|dy_m\| > 0.5, w ≤ 0, 장애물 8개 초과).
- 202 는 "명령을 보냈다" 이다. 적용 여부는 WS-06 의 `sim.command.result`(같은 `request_id`)로 본다. 콘솔은 `ok: false` 면 `message` 를 띄운다 (v1.4). 3초 안에 결과가 없으면 "시뮬레이터 응답 없음" 으로 본다.
- 422 `VALIDATION_ERROR` 에 v1.4 추가: 모르는 `kind`, 비어 있는 `track_id`. 없는 트랙 id 는 시뮬레이터가 `INVALID` 로 거부한다.
- WS-06 은 서버 → 콘솔 단방향. `{type:"sim.snapshot", available, pose, world}` 를 10 Hz(위치는 최신 것만)로, `sim.world` 가 바뀌면 바로 한 번 더 보낸다. MQTT-18 을 받으면 `{type:"sim.command.result", …payload}` 를 바로 보낸다 (v1.4).
- 모니터 화면의 출발 신호·랩 시간·km/h 는 `pose.race`·`world.track.real_m` 으로 그린다. 화면이 따로 계산하지 않는다 (v1.4).
- 백엔드는 REST-31 ~ 35 성공을 콘솔 이벤트 로그(REST-18, `/ws/v1/events`)에 `sim.command`(`INFO`, payload = 보낸 명령)로 남긴다.

### 11.3 게이트웨이 ↔ 시뮬레이터

MQTT 가 아니다. 게이트웨이가 시리얼 대신 TCP 로 같은 프레임(COBS + CRC, S15P31E110-241)을 보낸다 (`--sink tcp`, 시뮬레이터가 서버, 기본 포트 7600). 끊기면 게이트웨이는 1초마다 다시 붙고, 그동안 프레임이 없으므로 시뮬레이터 와치독(300 ms)이 차를 세운다 — 실차와 같다 (S15P31E110-260, 게이트웨이 쪽 작업은 별도 이슈).

## 관련 문서

- [VM 관리 API 계약 v1](vm-api-contract-v1.md) — `vm_id`, `guest_kind`
- [아키텍처 설계서](../architecture.md)
- [차량 제어 상세 명세](../design/vehicle-control-spec.md)
- [RC카 시나리오](../scenario-rc-car.md) §8 — D1·D3·D8 결정
- [카메라·AI 감지 계약 v1](camera-stream-v1.md) — MQTT-14 `vision.result` 와 영상 경로
- [시뮬레이터 시연 시나리오 (기본 시연)](../scenario-simulator.md) — 11장을 쓰는 구성·화면

## 변경 이력

| 버전 | 날짜 | 내용 |
| --- | --- | --- |
| v1 | 2026-10-19 | 최초. 에이전트 토픽(구현과 같음), RC카 주행 제어·앱 이벤트·지연 측정·ACL |
| v1 (보완) | 2026-10-20 | 접두사 `xenova/v1/`, 주행 VM Zephyr(4장), `app.heartbeat`(6.1), 수동주행 입력(7장) |
| v1.1 | 2026-10-21 | 시나리오 D1·D3·D8 반영 (S15P31E110-242). ESP32를 MQTT 참여자에서 빼고 게이트웨이 시리얼 뒤로(4장, 5.3, 9장 계정 삭제), `vehicle.status` 발행 = 게이트웨이 `source_id: dom0-gateway`(5.4), 5.5 ESP32 수용 규칙 → 게이트웨이 수용 규칙, Zephyr vchan + dom0 중계(4.1, 10장에서 삭제), `AUTO_READY`·VM 재시작 근거에서 에이전트 대신 도메인 상태 + `app.heartbeat`(5.1·5.2·6.1). `vehicle.status.batt_v` 를 선택 필드로(5.4, 측정 회로 없음). 토픽 이름은 그대로 |
| v1.1.1 | 2026-10-21 | 구현 기준 정합 (S15P31E110-249). 5.2 전환 표와 사유 `autonomous_ready`·`autonomous_lost`·`manual_fresh` (게이트웨이 -98 구현), `MANUAL` 중 수동 끊김은 자율이 신선해도 `SAFE_STOP`, `perception.result` 좌표계·부호·최대 개수·발행 중단 (젯슨 대역 -156 구현), 6장 재개 응답 이벤트 없음·`drive.fault.detected` 조건. 토픽·필드 이름 그대로 |
| v1.2 | 2026-10-21 | 재개를 바로 바꾸지 않고 `drive.resume.accepted`(3초 뒤 전환) → `AUTO` 또는 `drive.resume.cancelled`(6장). 운영자 콘솔 재개 경로 MQTT-13 `xenova/v1/console/resume`(백엔드 발행, 9장 ACL) (S15P31E110-98). 카메라 감지 결과 MQTT-14 `xenova/v1/apps/message-board/vision` `vision.result`(6장, 주행 판단 미사용·≤10 Hz·QoS 0, 9장 ACL message-board 발행) (S15P31E110-128) |
| v1.3 (초안) | 2026-10-22 | 시뮬레이터 전용 11장 (데모 기본 구성): MQTT-15 `sim.pose`·MQTT-16 `sim.world`(retained)·MQTT-17 `sim.command`, 콘솔 REST-30~35·WS-06 `/ws/v1/sim`, 게이트웨이 TCP 출력. 9장 ACL `simulator` 계정·백엔드 `sim/command`. 6장 `perception.event` `kind` 값 (자율주행 앱 -259 구현) (S15P31E110-261·-265) |
| v1.5 (초안) | 2026-10-22 | 자율주행 자동 복귀: `AUTO_READY`에 들어가면 게이트웨이가 바로 3·2·1(`drive.resume.accepted`, `requested_by: auto`) 뒤 `AUTO`(`auto_resumed`). 카운트다운 중 취소 `drive.resume.cancel_requested`(MQTT-06 메시지 보드·MQTT-13 콘솔, `DELETE /api/v1/drive/resume`) → `drive.resume.cancelled`(`operator_cancelled`), 그 `AUTO_READY` 동안은 운영자 재개로만 (5.2·6장, 게이트웨이 -98) |
| v1.4 (초안) | 2026-10-22 | 11장 트랙을 타원 → 실제 서킷 중심선 (`track.centerline`·구간별 벽 `wall_left_m`·`wall_right_m`·`real_m`·`start_index`, 트랙 목록 `tracks`), `sim.pose` `surface`·`race`(F1 출발 신호·랩), 장애물 `kind`, `sim.world.car`·`time_scale`, `sim.command` `track.select`·`race.start`, 배치 검사와 결과 MQTT-18 `xenova/v1/sim/result` `sim.command.result`, REST-31 `kind`·REST-36 트랙 바꾸기·REST-37 다시 출발, WS-06 결과 전달. 9장 ACL 시뮬레이터 `sim/result`. 화면 표시 단위 = 실제 서킷 비율 (S15P31E110-262·-265) |
