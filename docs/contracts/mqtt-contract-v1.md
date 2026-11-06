# MQTT 메시지 계약 v1

**현행 프로필** v1 공통 봉투 + verified-drive-1. 명시적으로 선택하는 warm-drive-1 기반 시험 구성은 6.3절을 따른다. 두 프로필 모두 공통 A/B·직접 MQTT·현재 실행 바인딩을 사용한다. Legacy 분리 앱과 v2 Telemetry는 별도 계약이다.
**대상 시스템** Raspberry Pi 4 + Xen 멀티 OS 관리 플랫폼, RC카 시나리오
**브로커** dom0 Mosquitto

VM 에이전트, RC카 앱 VM, dom0 서버·게이트웨이, 젯슨이 주고받는 MQTT 메시지의 기준 문서입니다. ESP32는 게이트웨이의 시리얼 경로를 사용합니다(4장). Linux 에이전트 수신은 Spring에 구현돼 있습니다. 3.1절의 Zephyr 공통 네트워크 지표는 플랫폼 발행과 로컬 브로커 검증까지 완료했고, Spring의 저장·조회 연결이 남았습니다.

`MQTT-NN`은 노션 인터페이스 계약의 MQTT 계약 ID입니다. 토픽별 상세 규칙(오류 코드, 중복 처리, 수락 기준)은 노션 행에 있고, 이 문서와 다르면 이 문서를 따릅니다.

토픽·필드·QoS를 바꾸면 발행하는 쪽과 구독하는 쪽 담당이 함께 리뷰합니다. 한쪽만 바꾸면 메시지가 조용히 버려집니다.

---

## 1. 공통 규칙

| 항목 | 규칙 |
| --- | --- |
| 토픽 접두사 | `xenova/v1/`. 10/20 이름 통일로 접두사도 바꿨다. 이전 `perseus/v1/`는 어디에도 배포된 적이 없어 호환 처리는 없다 |
| 인코딩 | UTF-8 JSON, 필드 이름은 snake_case |
| 시각 | `sent_at`은 ISO-8601 UTC. Zephyr MQTT-03에서 UTC를 확보하지 못하면 null이다. 신선도는 수신 시각으로 잰다 |
| 단위 | 속도 `m/s`, 조향 `rad`, 시간 `ms`, 메트릭 `percent`·`bytes`. (에이전트 MQTT-03 기준. dom0의 xentop 원시값은 MQTT가 아니라 VM API 계약 4.6 `GET /api/v1/stats`로 전달한다) |
| 크기 | 메시지 64 KB 이하. 주행 토픽(`drive.command`, `vehicle/*`)은 1 KB 이하 |
| 세션 | clean session. 오프라인 큐에 기대지 않는다 |

## 2. 공통 봉투

공통 봉투를 사용한다. Spring의 `AgentEnvelope`와 `AgentMessageHandler`는 Linux 에이전트 메시지를 검사한다. Zephyr MQTT-03의 시각·누적값 처리 조건은 3.1절을 따른다.

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
| `sent_at` | O | ISO-8601 UTC. Zephyr MQTT-03에서 UTC 미확보 시 null |
| `payload` | O | JSON 객체 |
| `correlation_id` | 선택 | 앱 간 한 흐름을 묶는 ID |
| `causation_id` | 선택 | 직접 원인이 된 메시지의 `message_id` |

- 노션 MQTT-06의 AppEventEnvelope는 이 봉투로 합친다: `event_id` → `message_id`, `source_vm_id` → `vm_id`, `source_app_id` → `app_id`, `event_type` → `type`.
- 백엔드가 에이전트에 보내는 명령(MQTT-04)만 예외다. 공통 필드(`schema_version`, `message_id`, `vm_id`, `type`, `sent_at`)에 명령 전용 필드를 더한 CommandEnvelope를 쓰고, `boot_id`·`agent_instance_id`·`seq` 대신 `expected_boot_id`·`command_id`를 쓴다.
- `boot_id`나 `agent_instance_id`가 바뀌면 받는 쪽은 그 발행자의 `seq` 기준을 새로 잡는다.
- Zephyr 앱 토픽은 `message_id`에 단조 증가 정수 문자열을 쓸 수 있다. Zephyr MQTT-03은 저장소의 36자 표본 키에 맞춰 메시지별 UUID를 사용한다.

## 3. VM 에이전트·자원 지표 토픽

각 VM의 에이전트와 백엔드가 주고받는다. heartbeat·inventory·metrics 수신은 백엔드에 구현돼 있다. 명령(04·05)은 Linux 에이전트 0.4.0 이 받으며(3.2절), 백엔드 발신은 아직 없다.

| ID | 토픽 | `type` | 방향 | QoS | retain | payload |
| --- | --- | --- | --- | --- | --- | --- |
| MQTT-01 | `xenova/v1/vms/{vm_id}/heartbeat` | `agent.heartbeat` | 에이전트 → 백엔드 | 1 | X | `{}` (봉투만) |
| MQTT-02 | `xenova/v1/vms/{vm_id}/inventory` | `agent.inventory` | 에이전트 → 백엔드 | 1 | **O** | `os{id, version_id, pretty_name}`, `kernel_release`, `hostname`, `interfaces[{name, mac, addresses[{address, family}]}]`, `agent_version`, `capabilities{commands[], metrics[]}` |
| MQTT-03 | `xenova/v1/vms/{vm_id}/metrics` | `agent.metrics` | Linux Agent·Zephyr Publisher → 백엔드 | 0 | X | 공통 `window_ms`·`values[]`. Linux는 `processes[]`·`process_collection`, Zephyr는 `observed_uptime_ms`(3.1절) |
| MQTT-04 | `xenova/v1/vms/{vm_id}/command` | `agent.command` | 백엔드 → 에이전트/drive-v1 | 1 | X | `command_id`, `operation_id`, `expected_boot_id`, `expires_at`, `action`(`stress`/`agent_exit`/`auto_delay`), `parameters` |
| MQTT-05 | `xenova/v1/vms/{vm_id}/command-result` | `agent.command_result` | 에이전트 → 백엔드 | 1 | X | `command_id`, `operation_id`, `status`(`ACCEPTED`/`RUNNING`/`SUCCEEDED`/`FAILED`/`REJECTED`), `started_at`, `finished_at`, `duration_ms`, `result`, `error` |

- inventory는 retained다. 최초 기동과 OS·네트워크 정보가 바뀔 때 전체를 다시 보낸다. retained 메시지는 마지막 보고일 뿐 지금 `ONLINE`이라는 증거가 아니다. VM을 삭제하면 백엔드가 그 VM의 retained inventory를 지운다.
- inventory 필수값은 `os.id`, `kernel_release`, `hostname`. os-release가 없는 Zephyr는 `os.id = "zephyr"`, `kernel_release = "<Zephyr 버전>"`으로 보낸다.
- v1 metrics의 `unit`은 `percent`, `bytes`다. 다른 단위는 `METRIC_UNIT_MISMATCH`로 거절한다. `value`가 null이면 `unavailable_reason`을 붙인다. 발행자는 `source`를 보내지 않으며, Spring은 Linux Agent 표본을 `guest_agent`로 저장한다. dom0의 xentop 관측값은 VM API 계약 2.4·4.6절의 `source = xen`을 사용한다.
- metrics의 `unavailable_reason`은 `first_sample`(구간 없음), `counter_reset`(누적 카운터가 되돌아감, 그 값이 새 기준), `not_supported`, `read_failed`, `parse_error`, `device_not_found`다. 받는 쪽은 모르는 사유도 문자열 그대로 받는다
- `process_collection`은 `processes[]`가 어떻게 모였는지다. 에이전트 0.2.2부터 보내며, 백엔드는 최신 프로세스 목록과 함께 저장해 `GET /api/v1/vms/{vmId}/processes`로 반환한다. 보고가 없는 구형 에이전트나 기존 저장 행은 `null`이며, 이 경우 빈 목록의 이유는 미확인이다.

  | 필드 | 의미 |
  | --- | --- |
  | `status` | `first_sample`(첫 표본이라 사용률 없음) / `ok` / `partial`(일부 프로세스를 권한·형식 때문에 못 읽음. 첫 표본이어도 실패가 있으면 partial) / `read_failed`(/proc 목록을 못 읽음) / `disabled`(상위 프로세스 0개 설정, 수집 안 함) |
  | `discovered`, `read_ok`, `vanished`, `failed` | 발견한 PID 수, 읽기 성공 수, 읽는 사이 끝난 수(정상), 읽기 실패 수 |
  | `truncated` | top-N 이나 메시지 크기 상한 때문에 목록 밖으로 밀린 프로세스가 있음. 목록에 없다고 끝난 프로세스가 아니다 |
  | `error` | `read_failed`의 원인 종류. 예외·errno 이름만(예: `PermissionError(EACCES)`), 그 밖에는 `null` |

  `processes[]`는 CPU 상위 목록이라 이 상태가 `ok`여도 특정 앱의 생존 근거가 아니다(6.1)
- Linux 에이전트(0.3.0부터)는 v1 토픽과 별도로 자기 상태(inventory·metrics 루프가 돌고 있는지, 수집·발행 실패 누계)를 `xenova/v2/vms/{vm_id}/telemetry/{inventory|heartbeat|metrics}`에 Zephyr 게스트와 같은 [Telemetry 계약 2.1](../../platform/guests/zephyr/telemetry/contract/CONTRACT.md) 형식으로 보낸다(`firmware.measurement_profile = linux_agent`, metrics family `publisher`). v1 heartbeat 는 에이전트 프로세스의 생존이고, v2 heartbeat 의 `publisher_state` 는 metrics 루프의 진행 여부다. 복구 Worker 의 판정 입력이 아니다. 백엔드는 아래 v2 metrics 수신 규칙대로 이 `publisher` 표본도 함께 저장한다
- 백엔드는 v2의 `xenova/v2/vms/+/telemetry/metrics`와 `xenova/v2/vms/+/telemetry/heartbeat`를 QoS 0으로 구독한다. inventory·events·snapshot은 구독하지 않는다.
  - heartbeat는 Linux·Zephyr 공통 수집기 보고다. 백엔드는 VM별 최신 보고를 저장하고 `GET /api/v1/vms/{vmId}/telemetry/heartbeat`에서 `FRESH`·`STALE`·`UNKNOWN`과 함께 제공한다. `FRESH`일 때만 `publisher_state`를 현재 수집 상태로 해석한다. 실패 누계와 마지막 수집 성공 시각은 별도 근거다. 조회 형식은 [백엔드 README](../../backend/README.md)를 따른다.
  - 받는 조건: `schema_version = "2.1"`, `type = "telemetry.metrics"`, 토픽 `{vm_id}` 와 `vm_id` 일치, `message_id`·`payload.samples[]` 존재. 하나라도 어긋나면 버린다
  - 지표 출처는 등록 VM 종류에 따라 `linux_telemetry` 또는 `zephyr_telemetry`다. 등록 전 공통 Publisher 지표는 `vm_telemetry`다. 표본 ID는 `message_id`로 만든 UUID, 관측 시각은 `clock.sent_at` 또는 수신 시각, `window_ms`는 계측 구간 차이다.
  - 값: `status = ok` 이면 숫자 또는 u64 10진 문자열을 숫자로 바꾸고, 그 외에는 `value = null`, `unavailable_reason` = `reason`(없으면 `status`). `labels` 는 문자열 맵으로 저장한다
  - 화면은 Linux 의 `cpu_percent_allocated`·`memory_used_bytes` 대신 Zephyr 의 `cpu.system.percent`, `memory.kernel_heap.used_bytes / (used_bytes + free_bytes)` 를 쓴다
  - 브로커의 `backend` 계정에 두 토픽의 읽기 권한이 있어야 한다(9장)
- heartbeat 주기는 5초. 에이전트 상태는 **백엔드가 heartbeat 나이로 판정**한다 (`ONLINE` → 15초 `STALE` → 30초 `OFFLINE`). 에이전트가 스스로 상태를 선언하지 않는다.
- heartbeat는 **VM 재시작 판정에만** 쓴다. 주행 제어권 전환 판정에는 쓰지 않는다 (5장).

### 3.1 Zephyr의 공통 네트워크 지표 (MQTT-03)

- Zephyr Telemetry Publisher도 자기 VM의 `xenova/v1/vms/{vm_id}/metrics`에 2초 주기로 QoS 0, retain 없이 `agent.metrics`를 발행한다. 봉투는 `1.0`이며 `agent_instance_id`는 그 Publisher 인스턴스 UUID다. v1 네트워크 표본은 1024 bytes 이하다.
- `payload.values`는 `net_rx_bytes_total`, `net_tx_bytes_total` 두 값이다. 단위는 `bytes`이며 유효한 값은 정밀도를 보존하는 unsigned 64-bit 십진 문자열이다. RX는 guest 스택이 수락한 Ethernet 프레임 바이트, TX는 guest netfront TX 링에 제출한 Ethernet 프레임 바이트다.
- `labels.iface`와 `labels.counter_epoch`를 붙인다. 차분은 VM·부팅·인터페이스·링 세대가 같을 때만 한다. `payload.observed_uptime_ms`는 캐시의 관측 단조 시각을 십진 문자열로 보내며 유효한 관측이 없으면 null이다. 누적값이므로 `window_ms`는 null이다.
- 초기 미관측·미지원·수집 실패·포화·만료에는 값 대신 null과 각각 `first_sample`, `not_supported`, `read_failed`, `counter_saturated`, `stale_sample` 사유를 보낸다. `source_rc`는 원 오류 코드이며 유효한 값은 `0`이다. 실제 0 바이트는 `"0"`이다. `sent_at`은 검증된 UTC를 사용하며 미확보 시 null이다. 소비자는 이때 수신 시각을 조회 시각으로 삼고, 차분 간격에는 관측 단조 시각을 사용한다.
- 프로세스 목록과 수집 상태는 이 표본에 없다. Spring 연동은 VM 종류에 따라 출처를 구분하고 메트릭만 저장하며, 숫자로 바꾸기 전에 누적값의 원 정밀도를 보존해야 한다. 자원 지표 수신을 Agent나 앱 생존으로 해석하지 않는다.
- 직접 계측한 `rx_bytes_per_s`와 패킷·힙·풀·스레드 상세는 [Telemetry 2.1](../../platform/guests/zephyr/telemetry/contract/CONTRACT.md)을 따른다.

### 3.2 데모 명령 (MQTT-04·05)

격리·자동 복구 시연용 명령이다. Linux의 `stress`·`agent_exit`는 에이전트 설정 `XENOVA_AGENT_DEMO_COMMANDS=1`일 때만 실행한다. Zephyr drive-v1은 빌드 설정 `XENOVA_AUTO_DELAY_INJECTION`일 때 `auto_delay`만 실행한다.

**명령 (MQTT-04)**: 봉투는 2장 예외(`boot_id`·`agent_instance_id`·`seq` 없음)이고 `vm_id` 는 대상 VM, `type` 은 `agent.command` 다. 에이전트는 command 토픽에 온 다른 type 은 실행하지 않고 버린다. payload:

| 필드 | 규칙 |
| --- | --- |
| `command_id` | 필수. 128자 이하 문자열. 결과와 중복 판정의 키. 없으면 결과를 보내지 않고 버린다 |
| `operation_id` | 선택. 주면 문자열이어야 한다(아니면 `INVALID_COMMAND`). 결과에 그대로 돌려준다 |
| `expected_boot_id` | 필수. 대상 VM 의 현재 boot_id(inventory 나 heartbeat 봉투에서 얻는다). 다르면 `BOOT_MISMATCH` |
| `expires_at` | 필수. ISO-8601 UTC. 봉투 `sent_at` 보다 늦고 300초 이내여야 한다(`INVALID_COMMAND`). 받은 시각이 이 시각 이후면 `EXPIRED` |
| `action` | `stress` / `agent_exit` / `auto_delay`. 그 밖은 `UNSUPPORTED_ACTION` |
| `parameters` | 객체. `stress`: `cpu_workers` 0..4, `memory_mib` 0..512, `duration_s` 1..120, 모두 정수(bool·실수 불가), 앞의 둘 중 하나는 1 이상. `agent_exit`: 빈 객체. `auto_delay`: `delay_ms` 1..100과 `duration_s` 1..120 정수만 허용. 그 밖의 키는 거절 |

- 시각: 에이전트는 자기 UTC 시계로 만료를 판정한다. `sent_at` 이 에이전트 시계보다 30초 넘게 앞서면 시계를 믿을 수 없다고 보고 `CLOCK_UNTRUSTED` 로 거절한다
- retained 로 온 명령은 실행하지 않는다(`INVALID_COMMAND`). 명령은 retain 하지 않는다
- 중복: 같은 `command_id` 에 같은 요청(`action`·`parameters`·`expected_boot_id`·`operation_id`, 만료 시각은 제외)이 다시 오면 실행하지 않고 마지막 결과를 다시 보낸다. 요청이 다르면 `IDEMPOTENCY_CONFLICT` 로 거절하고 원래 기록은 그대로 둔다. 에이전트는 실행 전에 기록을 부팅 동안 남기므로(`/run`) 같은 부팅 안에서 에이전트가 다시 시작해도 반복하지 않는다. 부팅이 바뀌면 `expected_boot_id` 로 거절된다
- 기록을 남기지 못하면 실행하지 않는다(`STATE_UNAVAILABLE`). 기록 파일을 읽지 못하거나 손상됐으면 중복을 가를 수 없으므로 모든 명령을 `STATE_UNAVAILABLE` 로 거절한다
- `stress` 는 한 번에 하나다. 도는 중이면 `BUSY`. `memory_mib` 이 대상 VM 의 MemAvailable − 64MiB 를 넘으면 `RESOURCE_UNAVAILABLE`

**결과 (MQTT-05)**: 일반 봉투. payload 는 아래 키를 모두 갖고 해당 없는 값은 null 이다.

| 필드 | 뜻 |
| --- | --- |
| `command_id`, `operation_id` | 명령의 값 |
| `status` | `REJECTED`(검사 실패, 끝) / `RUNNING`(stress 시작) / `SUCCEEDED`(stress 시간을 채움, 끝) / `FAILED`(끝) / `ACCEPTED`(agent_exit 수락, 끝) |
| `started_at`, `finished_at` | ISO-8601 UTC. `REJECTED` 는 `finished_at` 만, `RUNNING`·`ACCEPTED` 는 `started_at` 만 |
| `duration_ms` | stress 의 실제 실행 시간(끝난 결과만) |
| `result` | stress 의 적용 값 `{cpu_workers, memory_mib, duration_s}` |
| `error` | `{code, message}`. 코드: `INVALID_COMMAND`, `UNSUPPORTED_ACTION`, `DEMO_DISABLED`, `BOOT_MISMATCH`, `CLOCK_UNTRUSTED`, `EXPIRED`, `INVALID_PARAMETERS`, `BUSY`, `RESOURCE_UNAVAILABLE`, `STRESS_FAILED`(부하 프로세스가 일찍 죽음), `AGENT_EXITING`(agent_exit 가 stress 를 끝냄, 또는 종료를 기다리는 동안 온 새 명령), `AGENT_STOPPING`(에이전트 정지·재시작으로 끝남), `IDEMPOTENCY_CONFLICT`(같은 `command_id` 의 다른 요청), `STATE_UNAVAILABLE`(명령 기록을 남기거나 읽지 못함) |

- `stress`: `RUNNING` 다음에 `SUCCEEDED` 나 `FAILED` 하나가 온다. 부하는 에이전트와 다른 프로세스에서 내며, 시간이 지나면 남은 부하를 반드시 끝낸다
- `agent_exit`: 종결 결과는 `ACCEPTED` 하나다. 에이전트는 그 발행의 PUBACK 을 확인한 뒤에만 종료하고 다시 시작하지 않는다. 연결이 없거나 PUBACK 이 늦으면 다른 결과를 내지 않고 기다린다(QoS 1 발행은 늦게라도 전달될 수 있어 실패 결과를 따로 내면 모순된다). 기다리는 동안 새 명령은 `AGENT_EXITING` 으로 거절한다. PUBACK 전에 에이전트가 다시 시작하면 같은 `ACCEPTED` 를 다시 제출하고 확인되면 종료한다. 종료 완료를 기록하지 못하면 종료하지 않고 기록될 때까지 다시 시도한다. 확인 뒤 종료한 에이전트를 사람이 다시 시작했다면 다시 종료하지 않는다. 종료가 실제로 일어났는지는 heartbeat 가 끊기는 것(3장)으로 확인한다. 이후 복구는 Recovery Worker 의 판정을 따른다
- 결과를 받지 못하면(만료까지) 보낸 쪽은 결과 없음으로 처리한다. 같은 `command_id` 로 다시 보내면 실행 없이 마지막 결과만 돌아온다
- MQTT-05 는 QoS 1 이라 같은 결과가 두 번 이상 올 수 있다(재전달, 재시작 뒤 재제출). 백엔드는 `(vm_id, command_id)` 를 한 명령의 집계 키로 쓴다. 같은 `status`·같은 payload 의 재전달은 무시하고, `RUNNING` → `SUCCEEDED`/`FAILED` 는 정상 상태 전이로 처리한다. 종결 상태 뒤에 다른 종결 상태가 오면 계약 위반으로 기록한다
- Zephyr `auto_delay`는 UTC 시계가 없으므로 `expires_at`의 형식과 상한은 명령을 발행하는 백엔드가 검증한다. drive-v1은 현재 boot 일치, retained 여부와 파라미터를 검증하고 단조 시계로 실행 시간을 제한한다. 결과의 UTC 시각 필드는 null이다.
- 지연 주입 중 AUTO 계산이 release deadline을 연속 5회 넘으면 drive-v1은 해당 VM의 `apps/autonomous/events`에 `vm.trust`를 QoS 1로 한 번 발행한다. payload는 `trust=UNTRUSTED`, `reason=AUTO_DEADLINE_MISS`, `consecutive_misses=5`, `command_id`를 담는다. Gateway는 현재 AUTO 출력 owner의 유효한 이벤트만 받는다. 같은 세대의 미러 VM이 hold 조건까지 충족했으면 Authority 세대를 한 번 증가시키며 기존 owner 차단과 미러 owner 선정을 함께 기록하고, 미러가 새 세대를 적용한 뒤 출력을 허용한다. 준비된 미러가 없으면 `SAFE_STOP`으로 전환한다.

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
- 자율·수동주행 VM은 Zephyr다 (2026-10-20 [RC카 시나리오](../scenario-rc-car.md) §2). Zephyr Telemetry Publisher는 공통 네트워크 누적값을 MQTT-03으로 발행한다. 앱 프로세스 생존은 `app.heartbeat`(6.1)로 알린다.
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

받아들인 재개는 `drive.resume.accepted`(`switch_in_ms: 3000`)로 알리고 3초 뒤 AUTO로 바꾼다.
그 3초 동안 운영자는 같은 토픽(`console/resume`)으로 `drive.resume.cancel_requested`를 보내 취소할 수 있다(v1.7).
취소 요청도 `causation_id`(현재 control message_id)와 payload `expected_generation`(카운트다운을 시작한 세대)을 가진다.
둘이 맞고 카운트다운 중이면 Gateway는 카운트다운을 멈추고 `drive.resume.cancelled`(`reason: "operator_cancelled"`)를 낸다.
모드는 바꾸지 않으며(AUTO_READY 등 그대로), 다시 AUTO로 가려면 새 재개 요청이 필요하다. 자동 복귀는 없다.
카운트다운이 없거나 문맥이 맞지 않는 취소 요청은 이벤트 없이 무시한다. 취소는 반복돼도 결과가 같으므로 영구 중복 기록을 남기지 않는다.

```json
{"type":"drive.resume.cancel_requested","causation_id":"<현재 control message_id>",
 "payload":{"expected_generation":42,"requested_by":"operator"}}
```

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
| `manual_policy` | 선택 string | 세션 입력 계약의 `CONTINUE_SESSION` / `NEUTRAL_THEN_ACTION`. MANUAL·AUTO_READY에서 `manual_session`과 함께 사용 |
| `manual_session` | 선택 UUID string | 현재 운전자 세션. 앱 execution·instance와 별개. §7.1의 입력과 대조 |

선택 필드 쌍은 같은 generation에서 변경할 수 없다. 세션 계약을 선택한 실행은 필드가
없거나 세션 근거가 없다고 keys-only 입력으로 되돌아가지 않는다. 저장된 세션 계약과
동일 세대 retained의 선택 필드가 다르면 권위 충돌이다. 기존 필드·QoS·retain은 유지한다.

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
| — | `xenova/v1/console/resume` | `drive.resume.requested` | 백엔드(운영자 콘솔) → 게이트웨이 | 1 | `expected_generation`, `requested_by` (+ envelope `causation_id`) |
| — | `xenova/v1/console/resume` | `drive.resume.cancel_requested` | 백엔드(운영자 콘솔) → 게이트웨이 | 1 | `expected_generation`, `requested_by` (+ envelope `causation_id`) — v1.7 |
| MQTT-06 | `xenova/v1/apps/vehicle/events` | `drive.resume.accepted` | 게이트웨이 → 백엔드, 주행 화면 | 1 | `switch_in_ms`(3000), `generation`, `requested_by` |
| MQTT-06 | `xenova/v1/apps/vehicle/events` | `drive.resume.cancelled` | 게이트웨이 → 백엔드, 주행 화면 | 1 | `reason`, `generation`, (`operator_cancelled` 일 때) `requested_by` |
| MQTT-06 | `xenova/v1/apps/vehicle/events` | `drive.mode.changed` | 게이트웨이 → 인터랙션 VM, 주행 화면, 백엔드 | 1 | `from`, `to`, `generation`, `reason` |
| MQTT-06 | `xenova/v1/apps/vehicle/events` | `drive.fault.detected` | 게이트웨이 → 인터랙션 VM, 백엔드 | 1 | `app_id`, `vm_id`, `reason`, `detected_at` |

- Legacy의 `drive.resume.requested`는 `AUTO_READY`일 때만 받아들인다. 다른 모드에서는 무시하고 `drive.mode.changed`를 내지 않는다.
- `drive.resume.cancelled`의 `reason`: `operator_cancelled`(운영자 취소, v1.7), `readiness_lost`(대상 VM·앱 준비 상실·세대 변경), 그 밖에는 카운트다운을 끝낸 모드 전환의 reason(`autonomous_lost`, `command_timeout`, `no_fresh_standby` 등). 운영자 취소만 모드를 바꾸지 않는다.
- `console/resume` 토픽에는 노션 계약 ID가 아직 없다(코드 주석의 "MQTT-13"은 11장 `recovery/health`와 겹치는 옛 표기).
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

### 6.3 대기·활성화 보고 `app.lifecycle`

`warm-drive-1`을 선택한 공통 펌웨어·root Bootstrap·Gateway·Worker가 함께 사용한다.
기본 `verified-drive-1`의 `app.readiness` 필드나 READY 의미는 바꾸지 않는다. 프로필이
다르거나 앱별 바인딩이 없으면 새 자격을 승인하지 않는다. Legacy READY를 새 프로필의
대기 자격으로 대신 쓰지 않는다. v2 Telemetry namespace와는 독립적이다.

토픽은 기존 `xenova/v1/vms/{vm_id}/apps/{app_id}/events`이며 공통 봉투를 재사용한다.
**QoS 0, retain false, 전체 envelope 1024 bytes 이하**다. 기존 자기 VM·앱 경로 ACL을 사용한다.

| 필드 | 타입·범위 | 의미 |
|---|---|---|
| `profile` | `warm-drive-1` | 소비자가 명시적으로 선택한 준비 프로필 |
| `applied_generation` | JSON integer, 0..4294967295 | 검증한 현재 제어 문맥. 미적용은 0 |
| `check_seq` | 정규 uint64 십진 string, 1..18446744073709551615 | supervisor의 실제 입력·설정·소유 상태 확인 순번. 계산·송신 횟수 아님 |
| `progress_seq` | 정규 uint64 십진 string, 0..18446744073709551615 | 앱 instance의 실제 계산 완료 순번. 무업무 대기 중 증가하지 않음 |
| `phase` | WAITING / ACTIVATING / EXECUTING / STOPPING / RESIDUAL | 무업무 대기 / 현재 문맥의 계산 대기 / 계산 실행·결과 관측 / 종료 중 / 정리 미확정 |
| `readiness` | QUALIFIED / READY / NOT_READY | 사전 대기 자격 / 실제 계산을 확인한 활성화 준비 / 제한 상태 |
| `reason` | QUALIFIED·READY는 null, NOT_READY는 제한 코드 | 6.2절 코드와 APP_STOPPING·RESOURCE_RETAINED |

앞자리 0은 값 0 외에는 허용하지 않는다. 알 수 없는 필드·프로필·enum·이유·자료형은
거절한다. QUALIFIED는 WAITING과 1 이상의 generation이 필요하다. READY는 EXECUTING과
1 이상의 generation·실제 progress가 필요하다. 다음은 계산 0회의 정당한 대기다.

```json
{"profile":"warm-drive-1","applied_generation":42,"check_seq":"15","progress_seq":"0","phase":"WAITING","readiness":"QUALIFIED","reason":null}
```

main의 실제 검사를 100ms마다 보고하며 phase·자격·이유·generation 변경은 다음 정기
보고를 기다리지 않는다. 앱별 최신 보고 칸·원 관측 시각을 사용하고 송신 대기는 300ms다.
이 값은 기반 시험 프로필 설정이며 미래 상태 있는 앱·차량의 운영 상한으로 확대하지 않는다.
송신 worker는 check/progress/관측 시각을 새로 만들지 않는다. 재연결·새 문맥에서는 새 검사와
제어 확인이 필요하다. 감독 주기를 활성화의 최소 대기로 사용하지 않는다.

Gateway 수신 lease는 300ms, 자격 유지는 3초다. 같은/역전 check·이전 generation은
갱신 근거가 아니다. 새 check만 있는 READY는 실제 계산 lease를 갱신하지 않는다.
새 제어로 대상을 선택하면 해당 문맥의 새 계산 READY와 기존 출력 보호를 다시 확인한다.
QUALIFIED는 계산 실행·출력 허가가 아니며 READY도 출력 허가가 아니다. AUTO_READY의
명시적 복귀 요청·기존 카운트다운·100ms 명령 신선도는 유지한다. 정상 A 출력을 유지하면서
B에 다른 준비 generation을 배포하는 계획 인계는 이 프로필의 지원 범위가 아니다.

root 바인딩은 현재 execution과 앱별 instance를 연결한다. 수신기는 **seq/latest 갱신 이전에도**
이를 검사해 옛 실행이 현재 실행의 중복 검사 기준을 바꾸지 못하게 한다. 앱 신원 교체는 대상
앱의 근거만 철회하며 다른 앱·guest boot·별도 Publisher를 교체하지 않는다. 게스트가 신원을
스스로 발급하지 않는다. root receipt의 갱신·Store 적용과 게스트의 종료 확인·새 계산 준비는
별도 단계다. receipt 적용 성공만으로 앱 재초기화 완료를 뜻하지 않는다. 바인딩은 관측 시점의
사본이며 외부 root 변경과 원자적이라는 보장은 없다.

기존 적용 상태에서 앱 하나만 바꾼 receipt가 prepared/uncertain이면, 변경 전 앱별 신원과
변경 대상이 보존된 경우에만 대상 앱을 provisioning으로 차단하고 영향 없는 앱의 바인딩을
유지한다. 최초 provisioning·검증 불가 원본·다른 앱까지 바뀐 기록은 이 예외에 해당하지
않는다. Store 미확정 앱의 옛 신원과 새 신원은 모두 준비 근거로 승인하지 않는다.

Worker는 현재 AUTO/MANUAL 두 instance의 감독 근거를 각각 보관한다. 한 앱이 다른 앱의
부재·만료를 덮지 않는다. 무업무 대기를 계산 정체로 분류하지 않고, 신선한 기능/입력 제한이나
정리 잔존을 VM 사망으로 바꾸지 않는다. 기능 근거가 부적합하면 UNKNOWN이며 출력 판단은
Gateway가 맡는다. 300ms 보고 만료와 기존 VM 손실 유예·부팅 유예·관측 경로 보호는 별도다.
Xen crash/absent, Engine 원 attempt·예산·force 승인 경계는 유지한다.

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

### 7.1 명시적 세션 입력 `manual-session-1`

현재 backend의 keys-only 생산 경로와 구분하는 선택 계약이다. 공통 C decoder/어댑터와
warm Gateway에 소비 경로가 있으며 Spring/UI의 실제 세션 생산·모드 독립 공급은 아직
연결되지 않았다. 기존 `drive.input` type·토픽·QoS 0·retain false·1024 bytes 상한을
재사용한다. 아래 필드를 모두 사용하고 추가 필드는 거절한다.

| 필드 | 타입·범위 | 의미 |
|---|---|---|
| `profile` | `manual-session-1` | 명시적으로 선택한 입력 계약 |
| `session_id` | 정규 소문자·0이 아닌 UUID | 입력 생산자가 확인한 운전자 세션. A/B 승계만으로 교체하지 않음 |
| `sample_seq` | 정규 uint64 십진 string, 1 이상 | 그 세션의 실제 입력/생존 확인 순번. 단순 MQTT 재발행마다 올리지 않음 |
| `action_seq` | 정규 uint64 십진 string, 0 이상 | 실제 사용자 조작 변경 순번. 같은 키의 정상 유지 확인은 이 값을 올리지 않음 |
| `generation` | JSON integer, 1..4294967295 | 생산자가 확인한 현재 제어 문맥. generation 교체는 새 사용자 조작을 뜻하지 않음 |
| `keys` | string[], 최대 5개 | 기존 다섯 키. 중복·미지원 키는 이 프로필에서 거절 |
| `valid` | JSON boolean | 실제 입력 세션의 유효성. 세션 단절을 합성 중립으로 숨기지 않음 |
| `age_ms` | JSON integer, 0..2147483647 | 발행 시 원 입력/세션 확인 표본의 실제 나이. 통신 재전달로 0으로 갱신하지 않음 |

수신기는 원 `age_ms`에 자기 시계의 수신 후 경과시간을 더해 기존 200ms 입력 신선도를
검사한다. 서로 다른 장치의 단조 시각을 빼지 않는다. 같은 sample은 현재성을 갱신하지
않으며 역전 sample/action은 거절한다. 같은 action에서 키가 바뀐 자료도 거절한다.
정상 세션을 실제로 재확인한 새 sample은 같은 키/action으로 갱신할 수 있다.

`CONTINUE_SESSION`은 같은 MANUAL 승계에서 현재 유효 세션의 누른 키를 이어 처리한다.
`NEUTRAL_THEN_ACTION`은 해당 generation·session에서 유효한 현재 중립을 확인하고,
그보다 큰 원 action의 조작이 온 뒤 이동을 허가한다. 중립 확인은 최신값 슬롯과 별도로
보관한다. 중립 전에도 정상 정지 계산/준비는 가능하지만 이동은 허가하지 않는다.
만료·세션 상실은 이동을 차단하며 재시작 전 중립/승인을 새 전환에 재사용하지 않는다.
활성 MANUAL에서 세션이 바뀌거나 만료되면 기존 SAFE_STOP·재무장 경계로 들어간다.
입력 근거가 없는 동안 대기 보고만으로 MANUAL을 반복 선택하거나 generation을 계속
발급하지 않는다. 새 세션의 정상 준비는 가능하지만 출력은 명시적 재무장 전 금지다.

새 제어 generation을 반영할 때 생산자는 현재 세션을 실제 확인한 sample을 제공한다.
옛 입력 사본의 generation·MQTT seq만 바꾸는 것은 현재 확인이 아니다. 이 생산자 보장은
소비자 parser만으로 입증되지 않으며 담당자 종단 수락이 필요하다. Gateway의 계획
MANUAL 커밋은 이 계약을 내구 선택하고 이후 세션 없는 입력으로 자동 하향하지 않는다.
정규 벡터는 [C/Python 공통 자료](vectors/manual-session-1.jsonl)에 둔다.

<a id="drive-state-1"></a>
### 7.3 drive-state-1 완전 사본과 적용

이번 제공자 구현에서 추가한 저빈도 상태 계약이다. 토픽은 기존 `xenova/v1/vms/{vm_id}/apps/{app_id}/events`,
type은 `app.state.snapshot`과 `app.state.applied`, QoS 1·retain false다. 제어·지도·heartbeat의 wire를 바꾸지 않는다.
전체 envelope는 최대 8192바이트, C payload parser는 4096바이트다. 미지/중복 key·혼합/부분 사본·비유한 수치는 거절한다.
기존 공통 수신 검증 후 상태 type만 strict JSON을 한 번 더 해석한다. drive/heartbeat의 기존 파싱 비용은 늘리지 않는다.

snapshot payload의 필수 필드는 다음과 같다. bool/문자열을 정수로 묵시 변환하지 않는다.

| 필드 | 의미·형식 |
|---|---|
| `profile` | `drive-state-1` |
| `execution_id`, `intent_revision`, `lifecycle_generation` | source의 현재 실행 UUID·의도/수명 revision(integer 0..2^63-1). envelope의 source VM/app/boot/instance와 현재 binding 대조 |
| `source_generation` | 당시 출력 권위 generation(integer 1..2^32-1). 상태 적용 시 새 권위로 복사하지 않음 |
| `state_revision` | 1 이상 canonical uint64 decimal string. 같은 source 문맥에서 증가 |
| `context` | `frame`, `configuration`, `maneuver`, `road_revision`, `route_revision`의 canonical uint64 decimal string, `odom_epoch` UUID |
| `maneuver` | `kind=FOLLOW|AVOID`, `side=NONE|LEFT|RIGHT`, `phase=FOLLOW|PASS|REJOIN`, `merge_segment` 1..64 identifier(alnum/`_.:-`) |

FOLLOW는 side NONE·phase FOLLOW이며 AVOID는 LEFT/RIGHT·PASS/REJOIN이다.
frame/configuration/maneuver/odom 문맥은 맞아야 한다. road/route revision의 기계적 동일성은 요구하지 않고,
실제 앱 adapter가 현재 입력에서 merge segment·기하/동역학적 적합성을 확인한다.
Gateway는 권한이 있을 때 수락한 완전 사본만 보관한다. 권한 종료 후 도착한 source 갱신은 현재 사본이 되지 않는다.
당시 수락 사본은 현재 호환하면 사용 가능하며 source generation을 새 출력 허가로 사용하지 않는다.

applied payload는 `profile`, 대상 `execution_id/intent_revision/lifecycle_generation`, `copy_message_id`,
`state_revision`, `context`, `origin=handoff|rebuilt`, `phase=APPLIED|REJECTED`를 정확히 가진다.
handoff의 copy_message_id는 source envelope UUID이며 Gateway의 실제 수락 사본과 일치해야 한다.
현재 입력으로 최초 재구축하면 origin rebuilt·copy_message_id null이다. 없는 planner 상태를 기다리며 무한 정지하지 않는다.
rebuilt는 연속 승계 증거가 아니므로 전환에서는 stop_then_resume로 구분한다.
사본 적용과 현재 generation의 실제 새 계산·lifecycle READY·출력 허가는 서로 다르다.
실제 source/target 제품은 자기 인증된 VM/app 토픽으로 발행해야 하며 PUBACK만으로 적용 완료를 만들지 않는다.

Python `xenova_mqtt.handoff.StateReceiver`·`ManeuverAdapter`, C `xm_state_snapshot/application_*`와
`drive_adapter_apply_maneuver`가 제공된다. 적용 훅은 불변 사본을 기존 preparation/join 수명에 묶으며 progress를 올리지 않는다.
현재 기본 C 알고리즘은 stateless다. [실제 MQTT·C/Python 교차 시험](../verification/recovery-scenario-closure-20261030.md#gateway-provider)은
테스트 stateful adapter의 경계 검증이며 실제 planner·차량 연속성 완료가 아니다.

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
| 공통 Zephyr 직접 MQTT | username=vm_id. `xenova/v1/vms/%u/apps/autonomous/events`와 `.../manual/events`로 앱 이벤트 발행. 토픽과 봉투 VM/app를 대조하고 현재 실행은 root 바인딩으로 확인 |
| 백엔드 | `xenova/v1/apps/manual/input`, `xenova/v1/vms/{vm_id}/command`, `xenova/v1/console/resume`, `xenova/v1/sim/command` (12장) |
| 시뮬레이터 (`simulator`, 시뮬레이터 구성만) | `xenova/v1/sim/pose`·`sim/world`·`sim/result`. 구독은 `xenova/v1/sim/command` 만. `vehicle.status` 는 게이트웨이 대역으로 `gateway` 계정을 따로 쓴다 (12장) |
| 각 VM 측정 클라이언트·응답기 | `xenova/v1/probes/#` (자기 `vm_id`가 `source_vm_id`·`target_vm_id`와 맞는 것만) |
| dom0 Recovery Worker (`dom0-server`) | `xenova/v1/recovery/events`·`xenova/v1/recovery/health/{vm_id}` (11장, ACL 은 `xenova/v1/recovery/#`). 구독(read)은 `xenova/v1/vms/+/heartbeat`, legacy `xenova/v1/apps/+/events`, verified `xenova/v1/vms/+/apps/+/events`(Zephyr `app.heartbeat`, 6.1). 개발 단계의 전체 읽기 허용에 포함되며 계정별 구독 제한(다음 개정)에서도 유지한다. 계정 `dom0-server`와 이 발행·구독 규칙은 `platform/deploy/mosquitto/etc/policy.json`에서 함께 관리하며 개발용 accounts·acl을 생성한다. Worker 는 필터별 구독 승인(SUBACK)에 더해 승인 뒤 실제 수신을 경로의 증거로 삼는다. Mosquitto 는 읽기 ACL 에 막힌 구독도 SUBACK 성공으로 답하고 전달만 거르므로(실보드 확인), 승인만으로는 경로를 믿지 않고 그 경로로 메시지를 받기 전에는 손실을 판정하지 않는다 |

브로커 계정·ACL 파일은 [브로커 구성](../../platform/deploy/mosquitto/README.md)(`platform/deploy/mosquitto/etc/acl`)에서 관리한다.

- 계정 이름 = 토픽 안의 id: 에이전트는 `vm_id`, legacy 앱은 `app_id`, 젯슨은 `perception`, 그 외 `gateway`·`backend`. 공통 정책에서 계정별 ACL을 생성해 자기 토픽만 허용한다. 전역 pattern으로 v2·읽기 전용 계정에 v1 쓰기를 부여하지 않는다. ESP32는 MQTT에 붙지 않으므로 계정이 없다 (v1.1).
- `backend`의 읽기 ACL은 `xenova/v1/#`와 `xenova/v2/vms/+/telemetry/+`다. 백엔드가 실제로 구독하는 v2 토픽은 `metrics`와 `heartbeat`다(3장).
- 젯슨 `perception`은 v1의 `apps`·`vms`·`vehicle`·`display`·`console`·`recovery`·`probes` 영역만 읽는다. 시뮬레이터 토픽은 제외한다(12장). `observer`는 `xenova/v1/#` 읽기 전용이다.
- VM별 Telemetry 계정(`telemetry-{vm_id}`)은 자기 VM의 v2 Telemetry와 v1 `metrics`에 발행할 수 있다. 읽기 권한은 없다.
- `probes/#` 의 "자기 vm_id 만" 조건은 payload 기준이라 브로커가 거르지 못한다. 받는 쪽이 검사한다.

## 10. 미확정

- 게스트 시계 동기화 방식. 명령 만료의 허용 범위(3.2절의 30초)는 실보드 Debian·Ubuntu 게스트의 시간 동기 상태를 확인한 뒤 확정한다
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

## 12. 시뮬레이터 전용 (실차에는 없음)

차를 시뮬레이터로 둔 구성(중간점검 기본)에서만 쓰는 메시지·API 다. 실차 구성에는 이 토픽이 없고, 받는 쪽은 없어도 동작해야 한다.
시뮬레이터는 ESP32 자리(게이트웨이 시리얼 뒤)에 있다. 아래는 **실차에 없는 값**(위치·트랙)과 **시연 조작**만이며, 관람 화면(모니터 1 세 번째 칸·관리 콘솔)과 콘솔 조작용이다.

**젯슨(인지)은 이 토픽을 받지 않는다.** 젯슨은 시뮬레이터의 전방 카메라 화면(HTTP MJPEG `GET /camera/stream.mjpg`)만 보고 `perception.result` 를 낸다. 트랙 모양·차 위치를 알고 주행하면 실차와 다르기 때문이다. 브로커 ACL 에서 `perception` 계정은 `xenova/v1/sim/#` 를 읽을 수 없다 (9장).

### 12.1 토픽

| ID | 토픽 | `type` | 발행 → 구독 | 주기 | QoS | retain |
| --- | --- | --- | --- | --- | --- | --- |
| MQTT-15 | `xenova/v1/sim/pose` | `sim.pose` | 시뮬레이터 → 백엔드 | 20 Hz | 0 | X |
| MQTT-16 | `xenova/v1/sim/world` | `sim.world` | 시뮬레이터 → 백엔드 | 바뀔 때 + 시작 | 1 | O |
| MQTT-17 | `xenova/v1/sim/command` | `sim.command` | 백엔드(운영자 콘솔) → 시뮬레이터 | 조작할 때 | 1 | X |
| MQTT-18 | `xenova/v1/sim/result` | `sim.command.result` | 시뮬레이터 → 백엔드 | 명령마다 1번 | 1 | X |

- 발행자 `source_id`: 시뮬레이터 `simulator`, 백엔드 `dom0-server` (계정 `backend`, 수동 입력·재개 요청과 같음). 봉투·seq 규칙은 2장과 같다.
- 좌표는 **트랙 고정 좌표**다 (5.4 `perception.result` 의 차량 기준 좌표와 다르다). x·y(m), `yaw` = +x 에서 반시계 rad.
- 값은 모두 **모의 단위**(m, m/s)다. 화면은 트랙의 `real_m` ÷ 중심선 한 바퀴 길이(약 51)를 곱해 실제 서킷 단위(km, km/h)로 보인다.

**`sim.pose`**

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `x`, `y` | float | 차 뒷바퀴 축 가운데 위치 (m) |
| `yaw` | float | 진행 방향 (rad, −π ~ π) |
| `v_mps`, `steer_rad` | float | 지금 속도(구동 바퀴 둘레 속도)·전륜각 (모델 값). 후진이면 `v_mps` < 0 |
| `world_revision` | uint32 | 이 위치를 계산할 때의 `sim.world.revision` |
| `surface` | string | 차 중심이 있는 노면 `road` · `grass` · `dirt`. 잔디·흙에서 차는 최고 0.3 m/s (넘으면 1.2 m/s² 로 줄어든다) |
| `race` | object | 선택. 출발 신호·랩 `{phase, lights_on, lap, lap_ms, last_lap_ms, best_lap_ms, jump_start}` — 아래 표. 시뮬레이터는 늘 보낸다. 받는 쪽은 없으면 표시하지 않는다 |

`race` (F1 방식 출발 신호, [RC카 시나리오](../scenario-rc-car.md)와 같은 시연 규칙 P7)

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `phase` | string | `lights`(출발 신호 중) · `go`(출발 뒤) |
| `lights_on` | uint8 | 켜진 빨간 불 0 ~ 5. 다시 출발 0.7 초 뒤부터 1 초마다 하나씩 켜지고, 다섯 개가 켜진 뒤 0.2 ~ 3.0 초 사이 무작위로 모두 꺼지면 `go`. `go` 면 0 |
| `lap` | uint32 | 끝낸 바퀴 수. 반 바퀴를 지난 뒤 출발선(`track.start_index`)을 넘으면 1 오른다 |
| `lap_ms` | uint32 | 지금 바퀴 경과 (ms, 시뮬레이터 시간). `lights` 면 0 |
| `last_lap_ms`, `best_lap_ms` | uint32 \| null | 지난·최고 랩 (ms). 아직 없으면 null |
| `jump_start` | bool | 부정 출발: 불이 하나라도 켜진 동안 수동 앱이 전진을 명령함. 첫 바퀴 랩 시간에 +5 초 |

- 출발 신호 중에는 시뮬레이터가 수동이 아닌 활성 앱(자율)의 전진을 막아 출발선에 둔다 — 자율 앱은 신호를 보지 않는다. 수동은 막지 않는다.
- 활성 앱은 `vehicle.control` 로 안다. 시뮬레이터가 게이트웨이 시리얼 프레임을 받는 구성(MQTT 를 보지 않음)에서는 활성 앱을 몰라 늘 막고, `jump_start` 는 false 다.

**`sim.world`**

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `revision` | uint32 | 트랙·장애물·E-stop 이 바뀔 때마다 1 오른다. 시작 1. 시뮬레이터가 다시 시작하면 1 부터 다시 세므로, 받는 쪽은 revision 이 줄어도 새 실행으로 보고 받는다. 시뮬레이터는 정상 종료할 때 retained 를 지운다 |
| `track` | object | 지금 트랙. 아래 표 |
| `tracks` | array | 고를 수 있는 트랙 `[{id, name, real_m}]`. 10개: `spielberg`·`monza`·`silverstone`·`spa`·`melbourne`·`hungaroring`·`catalunya`·`mexico`·`bahrain`·`interlagos`. 시작은 `spielberg` |
| `car` | object | `{length_m, width_m}` 판정에 쓰는 차 몸 크기. `0.42`·`0.20` |
| `time_scale` | float | 시뮬레이터 시간 배율 (1 = 실제 시간). 화면이 위치 사이를 이어 그릴 때 곱한다 |
| `obstacles` | array | `[{id, kind, x, y, w}]` — 종류·가운데 위치(m)·폭(m). 최대 8개 |
| `estop` | bool | 시연용 E-stop 이 걸려 있음 (`vehicle.status.flags.estop` 도 같이 true) |

`track`

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `shape` | string | `centerline` |
| `id`, `name` | string | 트랙 id·화면 이름 |
| `real_m` | uint32 | 실제 서킷 한 바퀴 길이 (m). 화면 단위 변환에 쓴다 |
| `lane_width_m`, `curb_m` | float | 차선 폭 `0.6`, 연석 폭 `0.06`. 연석 끝까지 아스팔트 |
| `centerline` | array | `[[x, y], …]` 차선 중심선, 약 0.2 m 간격, 닫힌 고리 (진행 방향 순서). 트랙마다 413 ~ 681점 |
| `wall_left_m`, `wall_right_m` | array | 점마다 중심선에서 왼쪽·오른쪽 벽까지 거리 (m). 연석 밖 ~ 벽 = 잔디·흙. 출발 직선은 잔디 없이 바로 벽, 코너 근처는 보통의 두 배 |
| `start_index` | uint32 | 출발선이 있는 `centerline` 점 |

- 트랙 모양 출처: TUM racetrack-database (LGPL-3.0, © OpenStreetMap contributors) 중심선을 1/50 로 줄이고 반경 0.8 m 미만 코너만 다듬었다. 벽 거리는 시뮬레이터가 계산해 넣는다 — 받는 쪽은 그대로 쓴다. 관리 콘솔 mock 과 같은 계산이다.
- `sim.world` 는 가장 긴 스파 기준 약 30 KB (1장 64 KB 이하).

**`sim.command`**

| `op` | 인자 | 동작 |
| --- | --- | --- |
| `obstacle.add` | `obstacle_id`, `kind`, `x`, `y` | 장애물 놓기. `kind` = `cone`(폭 0.18) · `box`(0.30) · `tires`(0.26), 없으면 `cone`. `obstacle_id` 는 백엔드가 만든다. 이미 있으면 무시하고 적용으로 답한다 (QoS 1 중복 전달) |
| `obstacle.move` | `obstacle_id`, `x`, `y` | 옮기기 |
| `obstacle.remove` | `obstacle_id` | 치우기 |
| `estop` | `engaged` (bool) | E-stop 걸기·풀기 |
| `push` | `dy_m` | 차를 진행 방향 기준 옆으로 옮긴다 (+ 왼쪽, \|dy_m\| ≤ 0.5). 자율주행이 차선으로 돌아오는지 보이는 시연용 |
| `track.select` | `track_id` | 트랙 바꾸기. 장애물을 모두 치우고 차를 출발선에 세운다. 출발 신호도 처음부터 |
| `race.start` | — | 다시 출발. 차를 출발선에 세우고 출발 신호·랩 기록(지난·최고·부정 출발)을 처음부터 |

- 모든 명령에 `request_id`(string)를 넣는다. 시뮬레이터는 명령마다 MQTT-18 `sim.command.result` 를 한 번 낸다. 적용되면 `sim.world`(revision +1)도 다시 낸다 — `push`·`race.start` 는 `sim.pose` 에만 보인다.
- 모르는 `op`·범위 밖 값은 `ok: false`, `code: INVALID` 로 답한다 (9번째 장애물, \|dy_m\| > 0.5, 없는 `track_id`, 없는 `obstacle_id`).
- **배치 검사** (`obstacle.add`·`obstacle.move`, 관리 콘솔 mock checkPlacement 와 같은 식·순서). 앞에서부터 처음 걸린 것으로 거부한다. 옮기기는 옛 자리를 빼고 본다.
  1. `OUT_OF_TRACK`: 장애물이 벽 밖으로 조금이라도 나감 (\|옆 거리\| > 벽 − 폭/2)
  2. `ON_CAR`: 차 몸(길이 0.42·폭 0.20)과 겹침
  3. `NO_GAP`: 장애물 앞뒤(차 길이 + 폭)를 3 cm 마다 보아, 가장 넓은 틈(벽~장애물~벽)이 0.26 m(차 폭 0.20 + 양쪽 0.03) 보다 좁음. `gap_m`·`need_m` 을 채운다
  4. `TOO_DENSE`: 틈은 있지만, 기준 회피 주행(mock 의 자율 규칙: 인지 4 m → 틈 고르기 → pure pursuit·속도 계획)을 장애물 묶음 4.8 m 앞에서 출발시켜 보면 서거나 닿음
  5. `TOO_CLOSE`: 출발 뒤(`race.phase` = `go`)이고 차 바로 앞(−0.3 ~ 4.8 m)이라, 지금 차 상태로 4 를 돌려 봐도 못 지나감
- 4·5 의 기준 회피 주행은 화면 판정용이다. 지금 자율 앱은 장애물을 피하지 않고 앞에서 선다.

**`sim.command.result`** (MQTT-18)

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `request_id` | string | 받은 명령의 `request_id` |
| `op` | string | 받은 명령의 `op` |
| `ok` | bool | 적용했나 |
| `code` | string \| null | 거부 사유 `OUT_OF_TRACK` · `ON_CAR` · `NO_GAP` · `TOO_DENSE` · `TOO_CLOSE` · `INVALID`. `ok` 이면 null |
| `message` | string \| null | 화면에 그대로 띄울 한 줄 |
| `gap_m`, `need_m` | float \| null | `NO_GAP` 일 때 모의 단위 가장 넓은 틈·필요 폭(0.26). 다른 경우 null |

### 12.2 콘솔 API (백엔드)

인증은 다른 콘솔 API 와 같은 사용자 JWT, 오류 본문은 [VM 관리 API 계약](vm-api-contract-v1.md) 8.2 `{code, message}`.

| ID | 메서드 | 경로 | 요청 | 성공 | 오류 |
| --- | --- | --- | --- | --- | --- |
| REST-30 | GET | `/api/v1/sim/state` | — | 200 `{available, pose, world, pose_age_ms}` | 401 |
| REST-31 | POST | `/api/v1/sim/obstacles` | `{kind, x, y}` (`kind` 기본 `cone`) | 202 `{obstacle_id, request_id}` | 401, 409, 422 |
| REST-32 | PUT | `/api/v1/sim/obstacles/{obstacle_id}` | `{x, y}` | 202 `{request_id}` | 401, 404, 409, 422 |
| REST-33 | DELETE | `/api/v1/sim/obstacles/{obstacle_id}` | — | 202 `{request_id}` | 401, 404, 409 |
| REST-34 | POST | `/api/v1/sim/estop` | `{engaged}` | 202 `{request_id}` | 401, 409, 422 |
| REST-35 | POST | `/api/v1/sim/push` | `{dy_m}` | 202 `{request_id}` | 401, 409, 422 |
| REST-36 | POST | `/api/v1/sim/track` | `{track_id}` | 202 `{request_id}` | 401, 409, 422 |
| REST-37 | POST | `/api/v1/sim/race/start` | — | 202 `{request_id}` | 401, 409 |
| WS-06 | WS | `/ws/v1/sim` | 첫 프레임 `{type:"auth", access_token}` | `sim.snapshot` 푸시 | 인증 실패 시 닫음 |

- `available` = 마지막 `sim.pose` 가 1초 안 (백엔드 단조 시계). `pose`·`world` 는 마지막으로 받은 payload 그대로 (없으면 null).
- 409 `SIM_UNAVAILABLE`: 시뮬레이터가 없음(`available` false). 404 `OBSTACLE_NOT_FOUND`: 백엔드가 아는 마지막 `sim.world` 에 그 id 가 없음. 422 `VALIDATION_ERROR`: 필드 타입·범위 (모르는 `kind`, \|dy_m\| > 0.5, 숫자가 아닌 x·y, 비어 있는 `track_id`, 장애물 8개 초과). 503 `MQTT_UNAVAILABLE`: 브로커에 연결되어 있지 않아 명령을 보내지 못함.
- 202 는 "명령을 보냈다" 이다. 적용 여부는 WS-06 의 `sim.command.result`(같은 `request_id`)로 본다. 콘솔은 `ok: false` 면 `message` 를 띄운다. 3초 안에 결과가 없으면 "시뮬레이터 응답 없음" 으로 본다.
- 없는 트랙 id 는 시뮬레이터가 `INVALID` 로 거부한다 (백엔드는 목록을 검사하지 않는다).
- WS-06 은 서버 → 콘솔 단방향. `{type:"sim.snapshot", available, pose, world}` 를 10 Hz(위치는 최신 것만)로, `sim.world` 가 바뀌면 바로 한 번 더 보낸다. MQTT-18 을 받으면 `{type:"sim.command.result", …payload}` 를 바로 보낸다.
- `sim.command.result` 는 명령을 보낸 콘솔만이 아니라 인증된 WS-06 연결 모두에 방송한다. 콘솔은 자기가 받은 202 의 `request_id` 로 짝을 짓고, 짝이 없는 결과(다른 콘솔의 명령)는 무시한다.
- 결과가 REST 202 응답보다 먼저 올 수 있다 (시뮬레이터가 MQTT-17 을 바로 적용하면). 콘솔은 짝이 없는 결과를 3초 동안 들고 있다가 뒤늦게 온 202 의 `request_id` 와 맞춘다.

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
| v1.5 | 2026-10-28 | MQTT-03 metrics 에 선택 필드 `process_collection`(수집 상태·발견/읽기 수·잘림·원인 종류)과 `unavailable_reason` 값 `counter_reset` 추가(3장). 기존 필드는 그대로이고 받는 쪽은 모르는 필드·사유를 무시해도 된다 (S15P31E110-309) |
| v1.5 (보완) | 2026-10-28 | Linux 에이전트 0.3.0 이 자체 상태를 v2 Telemetry(Zephyr 계약 2.1) 토픽으로 함께 보낸다는 주석(3장). v1 토픽·payload 는 그대로 (S15P31E110-310) |
| v1.5 (보완) | 2026-11-02 | 백엔드가 v2 `telemetry/metrics` 를 구독해 Zephyr CPU·메모리를 메트릭 표본(`source = zephyr_telemetry`)으로 저장한다(3장). `backend` 계정에 이 토픽 읽기 권한 추가(9장). v1 토픽·payload 는 그대로 |
| v1.5 (보완) | 2026-11-03 | 백엔드가 v2 `telemetry/heartbeat` 도 구독해 VM 별 Publisher 상태(`publisher_state`·마지막 수집 성공 경과·실패 누계)를 `GET /api/v1/vms/{vm_id}/telemetry/heartbeat` 로 보인다(3장, S15P31E110-358). `backend` 읽기 권한 표기를 실제 정책 `xenova/v2/vms/+/telemetry/+` 로 맞춤(9장). v1 토픽·payload 는 그대로 |
| v1.6 | 2026-10-29 | 시뮬레이터 전용 12장 추가 (S15P31E110-261·-270·-271): MQTT-15 `sim.pose`·MQTT-16 `sim.world`·MQTT-17 `sim.command`·MQTT-18 `sim.command.result`, 콘솔 REST-30~37·WS-06. vehicle 브랜치 초안(그쪽 번호 v1.3·v1.4 §11)을 옮기며 바꾼 점: 구독자에서 인지 대역을 뺐다(젯슨은 전방 카메라 화면만), 트랙은 실제 서킷 10개, 배치 검사는 `ON_CAR`·`OUT_OF_TRACK` 만, `race` 는 선택. 계정 `simulator` 추가, `backend` 에 `sim/command` 쓰기, `perception` 읽기에서 `sim/#` 제외(9장). 기존 토픽·payload 는 그대로 |
| v1.6 (보완) | 2026-11-03 | 12장 시뮬레이터 남은 항목 구현 (S15P31E110-319): `sim.pose.race` 를 늘 보냄(출발 신호·랩·부정 출발 규칙, 출발 신호 중 자율 차는 출발선에서 대기), 배치 검사 `NO_GAP`·`TOO_DENSE`·`TOO_CLOSE` 를 예약에서 구현으로(관리 콘솔 mock 과 같은 판정, `NO_GAP` 이면 `gap_m`·`need_m`), `OUT_OF_TRACK` 을 mock 과 같이 "벽 밖으로 조금이라도 나가면"으로 맞춤, 잔디·흙 최고 0.3 m/s 명시, `race.start`·`track.select` 가 출발 신호를 처음부터. 필드 이름·타입은 그대로 |
| v1.6 (보완) | 2026-11-02 | 12장 WS-06: `sim.command.result` 는 모든 콘솔 연결에 방송, 결과가 REST 202 보다 먼저 올 수 있음을 명시 (구현과 같음, S15P31E110-267). 토픽·payload 는 그대로 |
| v1.7 | 2026-11-04 | 재개 카운트다운 운영자 취소 (S15P31E110-280): `console/resume` 에 `drive.resume.cancel_requested`(`causation_id`·`expected_generation`·`requested_by`) 추가, `drive.resume.cancelled` reason `operator_cancelled`(모드 유지, 자동 복귀 없음)(5.2 verified, 6장). 6장 표에 그동안 게이트웨이 README 에만 있던 `drive.resume.accepted`·`cancelled` 와 `console/resume` 재개 요청 행을 올림. 기존 토픽·payload·ACL 은 그대로 |
| v1.8 | 2026-11-04 | 3.2절 데모 명령 규칙 추가 (S15P31E110-123): MQTT-04 payload 필드·범위(`stress` 의 `cpu_workers`·`memory_mib`·`duration_s`), 유효 기간 300초·시계 허용 30초(`CLOCK_UNTRUSTED`), 같은 `command_id`·같은 요청은 재실행 없이 마지막 결과 재전송, 다른 요청은 `IDEMPOTENCY_CONFLICT`, 기록을 남기지 못하면 실행하지 않음(`STATE_UNAVAILABLE`), MQTT-05 상태 흐름과 오류 코드, `agent_exit` 의 종결 결과는 `ACCEPTED` 하나이고 PUBACK 확인 뒤 종료(PUBACK 전 재시작은 이어서 진행), MQTT-05 중복은 백엔드가 `(vm_id, command_id)` 집계 키로 처리(같은 결과 재전달은 무시, `RUNNING` → 종결은 전이, 종결 뒤 다른 종결은 계약 위반). 데모 설정이 꺼진 에이전트는 `DEMO_DISABLED`. 10장 미확정에서 명령 만료 허용 범위 제외 |

공식 전송 경계 참고: [MQTT 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)의
QoS 교환은 메시지 전달을 위한 것이며 앱/장치 적용 확인을 대신하지 않는다.
계정별 경로 제한은 [Mosquitto ACL](https://mosquitto.org/man/mosquitto-conf-5.html)의
username `%u` 치환 규칙을 사용한다.
