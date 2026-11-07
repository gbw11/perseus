# VM 부하·장애 주입 명령 API v1

**대상:** 사용자 JWT로 호출하는 Spring Backend REST API  
**MQTT 연계:** [`mqtt-contract-v1.md`](mqtt-contract-v1.md)의 MQTT-04·05

## 1. 공통 처리

- 모든 경로는 사용자 JWT 인증이 필요하다.
- 명령 제출 성공은 `202 Accepted`이며, 응답의 `command_id`로 상태를 조회한다.
- 백엔드는 MQTT 발행 전에 명령을 `PENDING`으로 저장한다. 발행에 실패하면 해당 행을 제거하고
  `503 MQTT_UNAVAILABLE`을 반환한다.
- Linux는 v1 Agent heartbeat, Zephyr는 v2 telemetry heartbeat가 신선하고 `boot_id`가 있어야 한다.
  없거나 만료되면 `409 AGENT_UNAVAILABLE`이다.
- MQTT 토픽은 `xenova/v1/vms/{vm_id}/command`, QoS 1, retain=false다.

## 2. CPU 부하

```http
POST /api/v1/vms/{vmId}/stress/cpu
Content-Type: application/json
```

```json
{"workers":1,"load_percent":80,"duration_s":30}
```

| 필드 | 범위 | Linux | Zephyr |
| --- | --- | --- | --- |
| `workers` | 1..4 | `stress.parameters.cpu_workers` | 반드시 1, `cpu_stress.parameters.workers` |
| `load_percent` | 10..100, 선택 | 사용하지 않으며 응답은 null | 필수, duty cycle |
| `duration_s` | 1..120 | 실행 시간 | 실행 시간 |

Zephyr에서 `workers != 1` 또는 `load_percent` 누락은 `400 INVALID_STRESS_PARAMETERS`다.

## 3. 메모리 부하

```http
POST /api/v1/vms/{vmId}/stress/memory
Content-Type: application/json
```

```json
{"memory_mib":8,"duration_s":30}
```

- Linux: `memory_mib` 1..512, MQTT action `stress`.
- Zephyr: `memory_mib` 1..8, MQTT action `memory_stress`. 8을 넘으면
  `400 INVALID_STRESS_PARAMETERS`다.

## 4. Zephyr AUTO 연산 지연

```http
POST /api/v1/vms/{vmId}/faults/auto-delay
Content-Type: application/json
```

```json
{"delay_ms":50,"duration_s":30}
```

- `delay_ms`: 1..100
- `duration_s`: 1..120
- Zephyr VM 전용이며 MQTT action은 `auto_delay`다.
- Linux VM은 `400 UNSUPPORTED_GUEST_KIND`다.

## 5. 제출 응답

CPU·메모리 응답은 다음 형식이다. `amount`는 CPU에서는 workers, 메모리에서는 MiB다.
Zephyr CPU만 `load_percent`가 채워진다.

```json
{
  "command_id": "...",
  "operation_id": "...",
  "vm_id": "...",
  "type": "CPU",
  "amount": 1,
  "load_percent": 80,
  "duration_s": 30,
  "submitted_at": "2026-11-07T06:00:00Z",
  "expires_at": "2026-11-07T06:00:30Z"
}
```

`auto-delay` 응답은 `type`, `amount`, `load_percent` 대신 `delay_ms`를 제공한다.

## 6. 명령 상태 조회

```http
GET /api/v1/vms/{vmId}/commands/{commandId}
```

```json
{
  "vm_id": "...",
  "command_id": "...",
  "operation_id": "...",
  "status": "RUNNING",
  "submitted_at": "2026-11-07T06:00:00Z",
  "started_at": "2026-11-07T06:00:01Z",
  "finished_at": null,
  "duration_ms": null,
  "result": {"workers":1,"load_percent":80,"duration_s":30},
  "error": null,
  "received_at": "2026-11-07T06:00:01Z",
  "contract_violation": false,
  "violation_detail": null
}
```

상태는 `PENDING`, `ACCEPTED`, `RUNNING`, `SUCCEEDED`, `FAILED`, `REJECTED`다.
없는 `(vm_id, command_id)`는 `404 COMMAND_NOT_FOUND`다. `result`와 `error`는 MQTT-05의 JSON을
그대로 객체로 반환한다.

QoS 1 동일 재전달은 상태를 바꾸지 않는다. `RUNNING → SUCCEEDED/FAILED`만 정상 실행 전이다.
종결 상태 뒤 다른 종결 상태를 받으면 원래 종결 결과를 보존하고 `contract_violation=true`와
`violation_detail`을 기록한다.
