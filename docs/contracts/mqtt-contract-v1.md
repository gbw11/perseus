# MQTT 메시지 계약 v1

**현행 프로필** v1 공통 봉투 + verified-drive-1 (공통 A/B, 직접 MQTT·현재 실행 바인딩·준비 보고). Legacy 분리 앱과 v2 Telemetry는 별도 계약이다.
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
| `interaction` | 인터랙션 VM | `linux` | 관제 메시지, 모드·장애 알림 표시, 자율주행 재개 요청 |
| `perception` | 젯슨 (`source_id: jetson`) | — | 객체인식 결과. 외부 발행자이며 앱 3종에 포함하지 않는다 |
| `vehicle` | dom0 서버·게이트웨이 | — | 모드 전환·장애 알림 |

- `guest_kind` 값은 `linux`, `zephyr` 두 가지다 (VM 관리 API 계약과 같다).
- `interaction` 은 앱 이름이 아니라 VM 역할 ID 다. 이 VM 에 어떤 앱을 올릴지는 정하지 않았고, 무엇을 올려도 토픽·브로커 계정·VM `role` 값은 이 ID 를 쓴다.
- 자율·수동주행 VM은 Zephyr다 (2026-10-20 [RC카 시나리오](../scenario-rc-car.md) §2). Zephyr에는 VM 에이전트가 없어 `agent.*` 토픽을 내지 않는다. 앱 프로세스 생존은 `app.heartbeat`(6.1)로 알린다.
- ESP32는 MQTT에 붙지 않는다. dom0 USB 시리얼(이후 CAN)로 게이트웨이와만 통신한다 (시나리오 §8 D1). 이 문서에서 ESP32로 가는 명령·ESP32에서 오는 상태는 모두 게이트웨이를 거친다.

### 4.1 Zephyr VM 연결 (netfront 직접 MQTT)

공통 A/B 펌웨어는 Xen netfront → 내부 bridge → broker의 직접 TCP/MQTT 경로를 쓴다.
dom0 vchan 중계는 과거 설계이며 현재 경로를 대신하지 않는다. VM 계정은 자기 UUID다.
적용된 실행별 Bootstrap과 trusted Manager execution의 대응은 Worker가 root 바인딩으로
전달하고 Gateway/Worker가 토픽·봉투·현재 boot/instance와 함께 대조한다.
v1 앱과 v2 Publisher는 같은 guest boot여도 서로 다른 instance와 socket/receipt를 쓴다.

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

다음 표는 명시적 `legacy` 분리 앱 프로필이다. 기본 verified 공통 A/B는 아래 별도 규칙을 따른다.

| 모드 | 활성 앱 | 들어가는 조건 |
| --- | --- | --- |
| `AUTO` | autonomous | 시작, 또는 `AUTO_READY`에서 자율주행 재개 요청 |
| `MANUAL` | manual | `AUTO`에서 자율 명령이 100 ms 끊기고 수동 명령이 신선할 때, 또는 `SAFE_STOP`에서 수동 명령이 신선해졌을 때 |
| `SAFE_STOP` | 없음 | 활성 앱 명령이 끊겼는데 넘길 앱이 없을 때 |
| `AUTO_READY` | manual | `MANUAL` 중 복구된 자율 VM이 3초 연속 아래를 모두 만족할 때: Xen 도메인 실행 중, `app.heartbeat` 신선(`state`가 `error` 아님), `drive.command` 신선 |

- 대기 중인 앱도 `drive.command`를 계속 발행한다. 게이트웨이는 활성 앱 명령만 유효로 보고, 대기 앱 명령은 신선도 확인에만 쓴다. 수동 VM은 키 입력이 없으면 `v_mps = 0`을 보낸다.
- 모드가 바뀌거나 활성 앱이 바뀔 때마다 게이트웨이는 `generation`을 1 올린다.
- 자율 VM은 Zephyr라 에이전트가 없다. `AUTO_READY` 판정에 에이전트 상태를 쓰지 않는다 (시나리오 §8 D8).
  설계의 Xen 도메인 실행 확인은 현재 Gateway의 MQTT 판정 경로에는 연결되어 있지 않다.
  MQTT 진행·입력 준비 관측과 Manager의 실제 Xen 실행 신원 검증을 동일하게 취급하지 않는다.

#### 공통 A/B 펌웨어 프로필

`verified-drive-1`은 동일한 v1 envelope/payload 기반의 명시적인 수락 프로필이다.
Gateway 기본값은 `verified`이며 두 UUID·영구 원본·신뢰된 바인딩이 필수다.
Legacy 수신값이나 readiness 없는 앱을 자동 READY로 취급하지 않는다.

| 구분 | 규칙 |
|---|---|
| 앱 발행 토픽 | `xenova/v1/vms/{vm_id}/apps/{app_id}/events`, app_id는 autonomous/manual |
| 기존 payload | drive.command와 vehicle.control의 핵심 필드를 유지. VM별 events에도 기존 v1 봉투 사용 |
| 준비 | §6.2의 실제 계산 진행·현재 세대·신선한 READY를 3초 유지. 명령 스트림은 준비 보고를 대신하지 않음 |
| 모드 | 인지 부적합 + 같은 VM MANUAL 준비 → MANUAL. AUTO 준비 유지 → AUTO_READY → 명시적 요청·3초 카운트다운·새 적용 → AUTO |
| 승계 | 현재 모드의 준비된 다른 실행 선택. 불가능하면 SAFE_STOP. VM 재시작·펌웨어 교체·CPU 변경으로 모드를 바꾸지 않음 |
| 정지 복귀 | 출력 없이 다른 VM 준비 확인 가능. MANUAL 선택 후 disarmed 재무장. 마지막 owner 회복만 기다리지 않음 |
| 재합류 | 새 trusted execution/Bootstrap·제어 적용·준비를 확인해 Standby. 기존 정상 Active를 자동 교체하지 않음 |
| 제어 원본 | root 전용 영구 generation·mode·owner. 저장/fsync → 게시 → PUBACK → 앱 적용. retained는 배포 사본 |
| 적용 기한 | 원 전환 발급 시 고정 1초. 재전달은 같은 generation/문맥, 기한 불연장. 미확인 시 이유와 STOP 보존 |
| 출력 | 현재 binding·선택 VM/app·현재 세대·100ms 이내 명령·현재 준비·별도 재무장 허가를 모두 확인 |
| 재시작 | 마지막 선호 모드/owner 복원, 출력은 금지. 현재 준비 유지·명시적 재무장·카운트다운·새 적용 필요 |

이전 세대 명령은 제한적인 실행 통신 관측일 수 있지만 적용·준비·출력의 근거는 아니다.
전환 실패는 VM 사망과 다르다. 무한 pending, 확인마다 새 generation 발급, 자동 force를 하지 않는다.
상태 유실/손상·더 최신 retained·선택 충돌은 출력 금지와 운영자 조정 상태로 남긴다.
uint32 generation 소진 시 wrap/reset하지 않는다. 전역 flock은 dom0 Gateway 중복 실행을 막는다.
실행 바인딩 lease는 같은 dom0 boot의 3초이며 관측 실패/만료 시 과거 세션으로 새 실행을 승인하지 않는다.

재개 요청은 기존 두 토픽(`console/resume`, `apps/interaction/events`)을 유지한다.
verified payload의 필수 `expected_generation`은 현재 uint32 세대, 선택 `requested_by`는 string이다.
기존 envelope `causation_id`는 현재 Gateway가 ACK 확인한 control의 message_id다.
`message_id`는 요청 중복 방지에 사용한다. 과거 세대/게시 문맥·retained 요청·재전달 중복은
새 승인으로 쓰지 않는다. 재연결 뒤에는 새 control 문맥을 확인한다. 출력 금지 상태의
MANUAL/AUTO 재무장도 이 요청을 사용하며 legacy의 AUTO_READY 전용 규칙과 구별한다.

```json
{"type":"drive.resume.requested","causation_id":"<현재 control message_id>",
 "payload":{"expected_generation":42,"requested_by":"operator"}}
```

이는 전체 envelope가 아닌 관련 필드 예시다. 공통 필수 신원·seq 필드는 §2를 따른다.
소비자 이행과 설치는 [공통 펌웨어 안내](../../platform/apps/drive/README.md)에 있다.
Spring/표시 소비자의 새 토픽·재개 문맥·정지 중 입력 공급은 아직 제품 연결이 필요하다.

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

아래는 legacy 수용 규칙이다. verified는 §5.2의 토픽/현재 실행/준비/출력 허가를 함께 적용한다.

1. `vehicle/control`의 `active_app` 토픽에서 온 `drive.command`만 시리얼로 넘긴다. 대기 앱 명령은 신선도 확인(5.2)에만 쓰고 넘기지 않는다.
2. `generation`이 현재 세대와 다르면 버린다.
3. 같은 세대에서 `seq`가 마지막 수신값 이하이면 버린다. 세대가 바뀌면 `seq` 기준을 새로 잡는다.
4. 넘길 명령이 없으면(`SAFE_STOP`, 활성 앱 명령 끊김) 정지 프레임을 보낸다. 마지막 명령을 반복하지 않는다.

- ESP32 쪽 규칙(세션·epoch·counter 검사, 시리얼 프레임 300 ms 끊김 정지)은 [차량 제어 상세 명세](../design/vehicle-control-spec.md)와 시리얼 프레임 형식(-241)을 따른다. MQTT의 `generation`·`seq`는 시리얼로 그대로 가지 않는다.
- 게이트웨이가 죽으면 시리얼 프레임이 끊기므로 ESP32 와치독이 차를 세운다.

## 6. 앱 이벤트

| ID | 토픽 | `type` | 발행 → 구독 | QoS | 주요 payload |
| --- | --- | --- | --- | --- | --- |
| MQTT-06 | `xenova/v1/apps/autonomous/events` | `perception.event` | 자율 VM → 인터랙션 VM | 1 | `kind`(obstacle 등), `detail` |
| MQTT-06 | `xenova/v1/apps/interaction/events` | `message.posted` | 인터랙션 VM → 주행 화면 | 1 | `text`, `level` |
| MQTT-06 | `xenova/v1/apps/interaction/events` | `drive.resume.requested` | 인터랙션 VM → 게이트웨이 | 1 | `requested_by` |
| MQTT-06 | `xenova/v1/apps/vehicle/events` | `drive.mode.changed` | 게이트웨이 → 인터랙션 VM, 주행 화면, 백엔드 | 1 | `from`, `to`, `generation`, `reason` |
| MQTT-06 | `xenova/v1/apps/vehicle/events` | `drive.fault.detected` | 게이트웨이 → 인터랙션 VM, 백엔드 | 1 | `app_id`, `vm_id`, `reason`, `detected_at` |

- Legacy의 `drive.resume.requested`는 `AUTO_READY`일 때만 받아들인다. 다른 모드에서는 무시하고 `drive.mode.changed`를 내지 않는다.
- 백엔드는 `drive.mode.changed`, `drive.fault.detected`를 콘솔 이벤트 로그(REST-18, `/ws/v1/events`)에 남긴다. 심각도는 모드 전환 `INFO`, `SAFE_STOP` 진입·장애 `WARNING`.

### 6.1 앱 생존 신호 `app.heartbeat`

| ID | 토픽 | `type` | 발행 → 구독 | 주기 | QoS | 주요 payload |
| --- | --- | --- | --- | --- | --- | --- |
| MQTT-06 | `xenova/v1/apps/{app_id}/events` | `app.heartbeat` | 앱 VM → 백엔드, 인터랙션 VM, 주행 화면 | 1초 | 0 | `state`, `uptime_s` |

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `state` | string | `active`(활성 앱) / `standby`(대기, 주행 명령은 계속 발행) / `idle`(`vehicle/control`을 아직 못 받음, 주행과 무관한 앱의 평상시) / `error` |
| `uptime_s` | float | 앱 프로세스 시작 후 경과 초 |

- 앱 **프로세스**가 살아 있는지와 상태를 알린다. VM 생존은 `agent.heartbeat`(3장), 제어권 전환은 `drive.command` 끊김 100 ms(5장)로 판정하며 이 신호를 쓰지 않는다.
- 계산 main이 실제 완료를 근거로 생성한다. 별도 통신 worker는 그 보고만 전달하며 관측 시각·완료 순번을 새로 만들지 않는다. 입력 부족은 verified에서 app.readiness로, 계산 실패는 heartbeat error와 준비 오류로 구분한다.
- 받는 쪽은 3초 동안 못 받으면 그 앱을 끊김으로 표시한다.
- verified의 `active`는 게스트가 적용한 control에서 자신이 선택됐다는 보고이며 Gateway의 실제 출력 허가가 아니다. 준비·승격은 §6.2를 따른다.
- Legacy의 에이전트가 없는 Zephyr 주행 VM에서는 이 신호가 앱 쪽 생존 정보다. 복구 서비스의 VM 재시작 판정(5.1)과 게이트웨이의 `AUTO_READY` 판정(5.2)은 Xen 도메인 상태와 이 신호를 쓴다 (시나리오 §8 D8).
- Linux VM 에서 도는 앱도 같은 토픽·형식으로 보낸다(어떤 앱을 올릴지는 4장 앱 구성에 따른다). 주행과 무관한 앱의 평상시 상태는 `idle` 이다. 에이전트의 `agent.heartbeat`(3장)는 VM·에이전트 생존이고 앱 생존이 아니며, `metrics` 의 `processes[]` 는 CPU 상위 목록이라 앱 생존 근거로 쓰지 않는다.
- Linux 앱의 이 신호는 **상태 표시용**이다. 복구 서비스는 Linux VM 을 이 신호로 재시작하지 않는다(Linux VM 재시작 근거는 Xen 상태와 `agent.heartbeat`). 멈춘 Linux 앱은 게스트 안 systemd 가 `Restart=on-failure`·`WatchdogSec` 으로 되살린다. 앱은 처리 루프에서 이 신호와 watchdog 통지를 함께 보낸다(공용 클라이언트 `xenova_mqtt.AppHeartbeat`).

### 6.2 앱 준비 보고 `app.readiness`

verified 앱 전용이며 v2 진단과 구별한다. 공통 봉투의 VM/app/boot/instance/seq를 재사용한다.

| 필드 | 타입·범위 | 의미 |
|---|---|---|
| `applied_generation` | JSON integer, 0..4294967295 | 검증 후 앱 연결부에 적용한 control 세대. 미적용은 0이며 READY 불가 |
| `progress_seq` | uint64의 정규 십진 string, 0..18446744073709551615 | 해당 앱 실제 완료 순번. 앞자리 0 금지(0 제외). 전송 횟수 아님 |
| `readiness` | PREPARING / READY / NOT_READY | 현재 입력·앱 내부 상태의 인계 준비 |
| `reason` | READY는 null, 나머지는 아래 제한 코드 | 준비 미완료 이유 |

reason: CONTROL_UNAPPLIED, INPUT_UNAVAILABLE, INPUT_STALE, INPUT_INVALID,
STATE_PREPARING, CALCULATION_FAILED, OUTPUT_INVALID. READY는 세대/진행 모두 1 이상이다.
공통 drive 어댑터의 CALCULATION_FAILED에는 필수 계산의 실제 자원 실패·기존 release deadline
미충족도 포함한다. 세부 원 errno/구간은 [local 건강 근거](health-evidence-v1.md)에 남기며
새 reason·type·필드를 추가하지 않는다. CPU 사용률·v2 미수신만으로 이 사유를 만들지 않는다.
알 수 없는 필드·상태·이유·범위·자료형은 거절한다. C와 Python은 같은 집합을 사용한다.

```json
{"applied_generation":42,"progress_seq":"1084","readiness":"READY","reason":null}
```

100ms마다 main의 새 계산 결과로 생성하고 **QoS 0, retain false, 전체 envelope 최대
1024 bytes**다. 앱별 최신 보고 칸을 사용하며 worker 대기 한계도 300ms다.
변경된 상태는 다음 100ms 보고에 반영한다. 사건 FIFO에 READY를 쌓아 재생하지 않는다.
Gateway 수신 lease는 300ms, 연속 준비 유지는 3초다. 새 완료 순번이 없는 같은 READY는
수신 lease/준비 유지를 갱신하지 않는다. 같은 진행의 NOT_READY는 준비를 취소할 수 있다.
이전 실행·세대·역전 순번·retained 보고는 준비 근거가 아니다. 재연결 뒤에는 새 control
문맥과 실제 완료를 다시 확인한다. 수신기 단조 시각·게스트의 원 입력 유효성은 각각
별도 근거이며 다른 장치의 단조 시각을 빼서 입력 나이나 편도 지연을 만들지 않는다.

READY는 출력 권한이 아니다. Standby·SAFE_STOP에서도 허용되며, Gateway가 새 owner/
mode를 발급하면 해당 새 세대 적용을 다시 확인한다. 상태 있는 앱은 계산 완료 외에
별도 warm-up/상태 준비가 필요하다. 앱 메모리 전체 복제를 뜻하지 않는다.

## 7. 수동주행 입력

아래 표는 현재 제품 backend의 공급 동작이다. verified의 정지/Standby 준비에 필요한
신선한 실제 입력 또는 중립 입력을 모드와 독립적으로 공급하는 연결은 아직 필요하다.
출력 허가를 먼저 주거나 과거 입력으로 READY를 만들지 않는다.

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
| 공통 Zephyr 직접 MQTT | username=vm_id. `xenova/v1/vms/%u/apps/autonomous/events`와 `.../manual/events`만 쓰기. 토픽과 봉투 VM/app를 대조하고 현재 실행은 root 바인딩으로 확인 |
| 백엔드 | `xenova/v1/apps/manual/input`, `xenova/v1/vms/{vm_id}/command`, `xenova/v1/console/resume` |
| 각 VM 측정 클라이언트·응답기 | `xenova/v1/probes/#` (자기 `vm_id`가 `source_vm_id`·`target_vm_id`와 맞는 것만) |
| dom0 Recovery Worker (`dom0-server`) | `xenova/v1/recovery/events`·`xenova/v1/recovery/health/{vm_id}` (11장, ACL 은 `xenova/v1/recovery/#`). 구독(read)은 `xenova/v1/vms/+/heartbeat`, legacy `xenova/v1/apps/+/events`, verified `xenova/v1/vms/+/apps/+/events`(Zephyr `app.heartbeat`, 6.1). 개발 단계의 전체 읽기 허용에 포함되며 계정별 구독 제한(다음 개정)에서도 유지한다. 계정 `dom0-server`와 이 발행·구독 규칙은 `platform/deploy/mosquitto/etc/policy.json`에서 함께 관리하며 개발용 accounts·acl을 생성한다. Worker 는 필터별 구독 승인(SUBACK)에 더해 승인 뒤 실제 수신을 경로의 증거로 삼는다. Mosquitto 는 읽기 ACL 에 막힌 구독도 SUBACK 성공으로 답하고 전달만 거르므로(실보드 확인), 승인만으로는 경로를 믿지 않고 그 경로로 메시지를 받기 전에는 손실을 판정하지 않는다 |

브로커 계정·ACL 파일은 [브로커 구성](../../platform/deploy/mosquitto/README.md)(`platform/deploy/mosquitto/etc/acl`)에서 관리한다.

- 계정 이름 = 토픽 안의 id: 에이전트는 `vm_id`, legacy 앱은 `app_id`, 젯슨은 `perception`, 그 외 `gateway`·`backend`. 공통 정책에서 계정별 ACL을 생성해 자기 토픽만 허용한다. 전역 pattern으로 v2·읽기 전용 계정에 v1 쓰기를 부여하지 않는다. ESP32는 MQTT에 붙지 않으므로 계정이 없다 (v1.1).
- 구독(read)은 개발 단계에서 v1 계정에 `xenova/v1/#`를 연다. `observer`는 이 읽기만 허용하며 probe도 발행하지 않는다. 별도 v2 계정에는 v1 읽기·쓰기를 부여하지 않는다. 계정별 구독 제한은 다음 개정(v1.2)에서 정한다.
- `probes/#` 의 "자기 vm_id 만" 조건은 payload 기준이라 브로커가 거르지 못한다. 받는 쪽이 검사한다.

## 10. 미확정

- 명령 만료 판정에 쓰는 시각 오차 허용 범위와 동기화 방식 (MQTT-04 `CLOCK_UNTRUSTED`)
- 와치독 300 ms, 전환 100 ms, `AUTO_READY` 3초는 실보드 측정 후 확정

## 11. 복구 이벤트 (dom0 → 백엔드)

자동 복구는 dom0의 Recovery Worker가 수행한다(VM API 계약 1.2절). Worker는 `xenova/v1/vms/+/heartbeat`(Linux, `agent.heartbeat`)와 legacy `xenova/v1/apps/+/events` 또는 verified `xenova/v1/vms/+/apps/+/events`의 `app.heartbeat`(Zephyr, 6.1)를 직접 구독해 Xen 도메인 상태와 함께 장애를 판정하고, 시도마다 아래 토픽으로 이벤트를 낸다. 객체 정의는 VM API 계약 2.5절이다. VM 마다의 현재 건강은 11.1 의 retained 스냅샷이다.

| ID | 토픽 | `type` | 방향 | QoS | retain | payload |
| --- | --- | --- | --- | --- | --- | --- |
| MQTT-12 | `xenova/v1/recovery/events` | `recovery.started` / `recovery.completed` / `recovery.recovered` / `recovery.cancelled` / `recovery.failed` | dom0 Recovery Worker → 백엔드 | 1 | X | VM API 계약 2.5절 복구 이벤트 객체 |
| MQTT-13 | `xenova/v1/recovery/health/{vm_id}` | `vm.health` | dom0 Recovery Worker → 게이트웨이·백엔드 | 1 | O | 11.1 건강 스냅샷 |

- 두 토픽 모두 봉투의 발행자는 `source_id = "dom0-server"`다(2장). MQTT-12 복구 이벤트의 `vm_id`는 payload 안에 있다(이 토픽에는 `{vm_id}`가 없다). MQTT-13 은 토픽에 `{vm_id}`가 있고 payload 의 `vm_id` 도 같은 값이다(11.1).
- 백엔드는 `event_id`로 중복을 거르고 `EventType` `recovery.*`로 기록한다(VM API 계약 5장). 백엔드가 재시작해 빠진 구간은 `GET /api/v1/recovery/events?after_event_id=`(VM API 계약 4.7절)로 채운다.
- Worker가 Spring 연결이나 브로커 없이도 복구를 수행하는 것이 전제이므로, 이 토픽은 알림일 뿐 복구 실행의 조건이 아니다.
- 브로커 계정 `dom0-server`(publish `xenova/v1/recovery/#`, read `xenova/v1/vms/+/heartbeat`·legacy `xenova/v1/apps/+/events`·verified `xenova/v1/vms/+/apps/+/events`. 9장과 같다)는 `platform/deploy/mosquitto/etc/accounts`·`acl`에 포함한다. 배포 시 실제 계정 비밀번호를 발급하고 Worker의 root 전용 설정에 전달한다.
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
| `state` | `HEALTHY`(실행 중이고 생존 신호가 신선함. 신호가 없는 종류는 Xen 실행만 근거) / `STARTING`(실행 중이지만 이번 부팅의 생존 신호 전, 유예 중) / `UNHEALTHY`(판정 사유가 있거나 `paused`·`stopping`, 신호 끊김) / `STOPPED`(도메인이 없고 의도가 `running` 이 아님) / `OBSERVATION_UNAVAILABLE`(생존 신호를 관측할 수 없음: 구독 미승인, 승인 뒤 그 경로로 아무것도 받지 못함, 이 VM 의 신호를 받은 적이 없음, 또는 Worker 의 Zephyr 대상 기록 손상. **장애가 아니다**) |
| `desired_state` | 플랫폼 registry 의도 `running` / `stopped` / `unknown` |
| `xen_state` | VM API 계약 3.2 의 Xen 상태, 도메인이 없으면 `null` |
| `heartbeat_age_s` | 이번 부팅에서 마지막 생존 신호(Linux `agent.heartbeat`, Zephyr `app.heartbeat`)를 받은 뒤 지난 초. 없으면 `null` |
| `reason` | 복구 판정이 내려진 상태면 VM API 계약 2.5 의 `reason`, 아니면 `null` |
| `manager_seq` | 같은 VM 에서 내용이 바뀔 때만 1 증가(주기 재발행에서는 그대로, `heartbeat_age_s` 만 갱신). 받는 쪽이 재시작 뒤 retained 값과 새 값을 구별한다 |
| `lease_s` | 이 스냅샷의 유효 시간(초). Worker 는 이 시간의 1/3 마다 다시 낸다. 받는 쪽은 마지막 수신 뒤 이 시간이 지나면 `OBSERVATION_UNAVAILABLE` 로 본다 |

- 봉투의 발행자는 `source_id = "dom0-server"` 다. 받는 쪽은 토픽의 `{vm_id}` 와 payload `vm_id` 가 같은 것만 쓴다.
- Linux VM 의 `HEARTBEAT_LOST` 판정은 30초, Zephyr VM 의 `APP_HEARTBEAT_LOST` 는 15초 무수신이다(VM API 계약 2.5). 게이트웨이의 제어권 전환은 이 스냅샷이 아니라 `drive.command` 신선도(5장)로 한다.
- 무수신 판정은 Worker 가 **그 VM 의 신호를 받은 적이 있을 때만** 한다. 다른 VM 의 수신은 그 VM 의 전달 근거가 아니다. 받은 적 없는 VM(계정·ACL 오류, 배포 누락, 첫 부팅 정체를 구별할 수 없음)은 `OBSERVATION_UNAVAILABLE` 이고 자동 복구하지 않는다. 받은 적 있다는 기록은 현재 신호가 아니다(재부팅 뒤 이전 부팅의 신호는 세지 않는다).
- `HEALTHY` 는 Xen 실행과 생존 신호가 정상이라는 뜻이다. CPU·메모리·앱의 실제 작업까지 정상이라는 뜻은 아니다([건강 판정 근거](health-evidence-v1.md)).
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
| v1.3 | 2026-10-26 | Linux 앱도 `app.heartbeat`(6.1)를 같은 형식으로 보낸다. 상태 표시용이며 복구 서비스의 Linux VM 재시작 근거가 아니다. 멈춘 Linux 앱은 게스트 systemd 가 재시작 (S15P31E110-301) |
| v1.4 | 2026-10-27 | Linux 인터랙션 VM 의 app_id 를 `message-board` 에서 `interaction` 으로 바꾼다(4장). 올릴 앱이 정해지지 않아 앱 이름 대신 역할 ID 를 쓴다. 토픽 `apps/message-board/events` → `apps/interaction/events`(5장 재개 요청, 6장), 구독자 표기 "메시지 보드" → "인터랙션 VM". type·payload 는 그대로 (S15P31E110-303) |

공식 전송 경계 참고: [MQTT 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)의
QoS 교환은 메시지 전달을 위한 것이며 앱/장치 적용 확인을 대신하지 않는다.
계정별 경로 제한은 [Mosquitto ACL](https://mosquitto.org/man/mosquitto-conf-5.html)의
username `%u` 치환 규칙을 사용한다.
