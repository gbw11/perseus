# VM 부하·장애 주입 명령 API v1

**대상:** 사용자 JWT로 호출하는 Spring Backend REST API  
**MQTT 연계:** [`mqtt-contract-v1.md`](mqtt-contract-v1.md)의 MQTT-04·05

## 1. 공통 처리

- 모든 경로는 사용자 JWT 인증이 필요하다.
- 명령 제출 성공은 `202 Accepted`이며, 응답의 `command_id`로 상태를 조회한다.
- 백엔드는 MQTT 발행 전에 명령을 `PENDING`으로 저장한다. 발행에 실패하면 해당 행을 제거하고
  `503 MQTT_UNAVAILABLE`을 반환한다.
- Linux 부하 명령은 v1 Agent heartbeat가 신선하고 `boot_id`가 있어야 한다.
  없거나 만료되면 `409 AGENT_UNAVAILABLE`이다. Zephyr 연산 지연은 v2 telemetry heartbeat를 사용한다.
- MQTT 토픽은 `xenova/v1/vms/{vm_id}/command`, QoS 1, retain=false다.

## 2. CPU 부하

```http
POST /api/v1/vms/{vmId}/stress/cpu
Content-Type: application/json
```

```json
{"workers":1,"load_percent":80,"duration_s":30}
```

| 필드 | 범위 | Linux |
| --- | --- | --- |
| `workers` | 1..4 | `stress.parameters.cpu_workers` |
| `load_percent` | 10..100, 선택 | 호환성을 위해 입력은 받지만 사용하지 않으며 응답은 null |
| `duration_s` | 1..120 | 실행 시간 |

Zephyr VM은 `400 UNSUPPORTED_GUEST_KIND`이며 `/faults/auto-delay`를 사용한다.

## 3. 메모리 부하

```http
POST /api/v1/vms/{vmId}/stress/memory
Content-Type: application/json
```

```json
{"memory_mib":8,"duration_s":30}
```

- Linux 전용이며 `memory_mib`는 1..512, MQTT action은 `stress`다.
- Zephyr VM은 `400 UNSUPPORTED_GUEST_KIND`이며 `/faults/auto-delay`를 사용한다.

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

## 5. Linux Agent 종료

```http
POST /api/v1/vms/{vmId}/faults/agent-exit
```

- 요청 본문은 없다.
- Linux VM 전용이며 MQTT action은 `agent_exit`, parameters는 빈 객체다.
- Agent heartbeat가 온라인이고 `boot_id`가 있어야 한다.
- Zephyr VM은 `400 UNSUPPORTED_GUEST_KIND`다.
- 대상 Agent에서 `XENOVA_AGENT_DEMO_COMMANDS=1`이어야 실제 종료된다. 비활성화 상태이면
  명령 결과가 `REJECTED`와 `DEMO_DISABLED`로 저장된다.
- Agent는 `ACCEPTED` 결과의 MQTT PUBACK을 확인한 뒤 종료한다. 이후 heartbeat 손실은 플랫폼
  Recovery Worker의 자동복구 판정 대상이다.

## 6. 제출 응답

CPU·메모리 응답은 다음 형식이다. `amount`는 CPU에서는 workers, 메모리에서는 MiB다.
Linux Agent 계약에는 duty cycle이 없으므로 `load_percent`는 null이다.

```json
{
  "command_id": "...",
  "operation_id": "...",
  "vm_id": "...",
  "type": "CPU",
  "amount": 1,
  "load_percent": null,
  "duration_s": 30,
  "submitted_at": "2026-11-07T06:00:00Z",
  "expires_at": "2026-11-07T06:00:30Z"
}
```

`auto-delay` 응답은 `type`, `amount`, `load_percent` 대신 `delay_ms`를 제공한다.
`agent-exit` 응답은 추적용 `command_id`, `operation_id`, `vm_id`, `submitted_at`, `expires_at`을 제공한다.

## 7. 명령 상태 조회

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
  "result": {"cpu_workers":1,"memory_mib":0,"duration_s":30},
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
