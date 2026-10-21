# MQTT 메시지 계약 v1

**최종 갱신** 2026-10-20 (4장 주행 VM Zephyr, 6.1 `app.heartbeat`, 7장 `drive.input` 키·반복·끊김 규칙)
**대상 시스템** Raspberry Pi 4 + Xen 멀티 OS 관리 플랫폼, RC카 시나리오
**브로커** dom0 Mosquitto

VM 에이전트, RC카 앱 VM, dom0 서버, 젯슨, ESP32가 주고받는 MQTT 메시지의 기준 문서입니다. 에이전트 수신부는 이미 백엔드에 구현돼 있고(`backend/.../agent/`), 이 문서의 2장·3장은 그 구현과 같습니다. 4장 이후 RC카 메시지는 새로 정하는 내용입니다.

`MQTT-NN`은 노션 인터페이스 계약의 MQTT 계약 ID입니다. 토픽별 상세 규칙(오류 코드, 중복 처리, 수락 기준)은 노션 행에 있고, 이 문서와 다르면 이 문서를 따릅니다.

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
| `source_id` | VM이 아닐 때 | `vm_id` 대신 쓴다. `dom0-server`, `dom0-gateway`, `jetson`, `esp32` |
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
- 펌웨어(ESP32, Zephyr)는 `message_id`를 단조 증가 정수 문자열로 대신할 수 있다.

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
| `message-board` | 메시지 보드 VM | `linux` | 관제 메시지, 모드·장애 알림 표시, 자율주행 재개 요청 |
| `perception` | 젯슨 (`source_id: jetson`) | — | 객체인식 결과. 외부 발행자이며 앱 3종에 포함하지 않는다 |
| `vehicle` | dom0 서버·게이트웨이 | — | 모드 전환·장애 알림 |

- `guest_kind` 값은 `linux`, `zephyr` 두 가지다 (VM 관리 API 계약과 같다).
- 자율·수동주행 VM은 Zephyr다 (2026-10-20 [RC카 시나리오](../scenario-rc-car.md) §2). Zephyr에는 VM 에이전트가 없어 `agent.*` 토픽을 내지 않는다. 앱 프로세스 생존은 `app.heartbeat`(6.1)로 알린다.

## 5. 주행 제어

### 5.1 역할 분리

| 판정 | 주체 | 근거 신호 |
| --- | --- | --- |
| 제어권 전환 (자율 ↔ 수동) | **dom0 게이트웨이** (관리 서버와 별도 프로세스) | 활성 앱의 `drive.command` 끊김 100 ms |
| VM 재시작 | 복구 서비스 | 에이전트 `OFFLINE` + Xen 도메인 상태 |
| 최종 정지 | ESP32 | `drive.command` 끊김 300 ms (와치독) |

- 게이트웨이는 VM을 재시작하지 않고, 복구 서비스는 제어권을 바꾸지 않는다.
- 잘못된 승격보다 정지가 낫다. 수동 VM의 명령이 신선하지 않으면 승격하지 않고 `SAFE_STOP`으로 간다.

### 5.2 주행 모드

| 모드 | 활성 앱 | 들어가는 조건 |
| --- | --- | --- |
| `AUTO` | autonomous | 시작, 또는 `AUTO_READY`에서 자율주행 재개 요청 |
| `MANUAL` | manual | `AUTO`에서 자율 명령이 100 ms 끊기고 수동 명령이 신선할 때, 또는 `SAFE_STOP`에서 수동 명령이 신선해졌을 때 |
| `SAFE_STOP` | 없음 | 활성 앱 명령이 끊겼는데 넘길 앱이 없을 때 |
| `AUTO_READY` | manual | `MANUAL` 중 복구된 자율 VM의 명령이 3초 연속 신선하고 에이전트가 `ONLINE`일 때 |

- 대기 중인 앱도 `drive.command`를 계속 발행한다. 게이트웨이는 활성 앱 명령만 유효로 보고, 대기 앱 명령은 신선도 확인에만 쓴다. 수동 VM은 키 입력이 없으면 `v_mps = 0`을 보낸다.
- 모드가 바뀌거나 활성 앱이 바뀔 때마다 게이트웨이는 `generation`을 1 올린다.

### 5.3 토픽

| ID | 토픽 | `type` | 발행 → 구독 | 주기 | QoS | retain |
| --- | --- | --- | --- | --- | --- | --- |
| MQTT-06 | `xenova/v1/apps/autonomous/events` | `drive.command` | 자율 VM → ESP32, 게이트웨이 | 50 Hz | 0 | X |
| MQTT-06 | `xenova/v1/apps/manual/events` | `drive.command` | 수동 VM → ESP32, 게이트웨이 | 50 Hz | 0 | X |
| MQTT-06 | `xenova/v1/apps/perception/events` | `perception.result` | 젯슨 → 자율 VM | 15 Hz | 0 | X |
| MQTT-09 | `xenova/v1/vehicle/control` | `vehicle.control` | 게이트웨이 → ESP32, 앱 전체 | 변경 시 | 1 | **O** |
| MQTT-10 | `xenova/v1/vehicle/status` | `vehicle.status` | ESP32 → 게이트웨이, 앱 전체 | 20 Hz | 0 | X |

- 주행 토픽은 QoS 0, 받는 쪽은 토픽별 최신 1개만 유지한다 (latest-only). 50 Hz라 재전송보다 다음 명령이 빠르다.
- `vehicle/control`은 retained라 ESP32가 재부팅해도 현재 활성 앱과 세대를 바로 받는다.
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

**`vehicle.status`** (ESP32, `source_id: esp32`)

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `applied_generation` | uint32 | 마지막으로 적용한 명령의 세대 |
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

### 5.5 ESP32 수용 규칙

1. `vehicle/control`의 `active_app` 토픽에서 온 `drive.command`만 받는다.
2. `generation`이 현재 세대와 다르면 버린다.
3. 같은 세대에서 `seq`가 마지막 수신값 이하이면 버린다. 세대가 바뀌면 `seq` 기준을 새로 잡는다.
4. 받은 명령이 300 ms 동안 없으면 정지한다.

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
- 에이전트가 없는 Zephyr 주행 VM에서는 이 신호가 앱 쪽 생존 정보다. 복구 서비스의 VM 생존 판정(도메인 상태 + 명령 신선도)과 §5.2 `AUTO_READY`의 "에이전트 `ONLINE`" 조건을 무엇으로 바꿀지는 시나리오 §8 D8에서 정한다.

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
| 게이트웨이 | `xenova/v1/vehicle/control`, `xenova/v1/apps/vehicle/events` |
| ESP32 | `xenova/v1/vehicle/status` |
| 백엔드 | `xenova/v1/apps/manual/input`, `xenova/v1/vms/{vm_id}/command` |
| 각 VM 측정 클라이언트·응답기 | `xenova/v1/probes/#` (자기 `vm_id`가 `source_vm_id`·`target_vm_id`와 맞는 것만) |

브로커 계정·ACL 파일은 [브로커 구성](../../platform/deploy/mosquitto/README.md)(`platform/deploy/mosquitto/etc/acl`)에서 관리한다.

- 계정 이름 = 토픽 안의 id: 에이전트는 `vm_id`, 앱 VM 은 `app_id`, 젯슨은 `perception`, 그 외 `gateway`·`esp32`·`backend`. ACL 이 `%u` 패턴으로 "자기 토픽에만 발행" 을 건다.
- 구독(read)은 개발 단계에서 모든 계정에 `xenova/v1/#` 를 연다. 계정별 구독 제한은 v1.1 에서 정한다.
- `probes/#` 의 "자기 vm_id 만" 조건은 payload 기준이라 브로커가 거르지 못한다. 받는 쪽이 검사한다.

## 10. 미확정

- 명령 만료 판정에 쓰는 시각 오차 허용 범위와 동기화 방식 (MQTT-04 `CLOCK_UNTRUSTED`)
- Zephyr 주행 VM(자율·수동)의 네트워크. 현재 Zephyr 템플릿은 PV 네트워크를 지원하지 않는다 (`platform/guests/zephyr/xen.cfg.in`). 안 되면 vchan + dom0 중계로 대체
- 와치독 300 ms, 전환 100 ms, `AUTO_READY` 3초는 실보드 측정 후 확정

## 관련 문서

- [VM 관리 API 계약 v1](vm-api-contract-v1.md) — `vm_id`, `guest_kind`
- [아키텍처 설계서](../architecture.md)
- [차량 제어 상세 명세](../design/vehicle-control-spec.md)
