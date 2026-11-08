# Gateway 제공자 계약 — 로컬 호출·상태 사본·장치 피드백

2026-11-01 제공자 구현이다. 이 문서의 wire 선택은 이번 변경에서 추가했다.
검증 후보·실행 결과·대역 경계는 [보고서](../verification/recovery-scenario-closure-20261030.md#gateway-provider)에 둔다.
외부 HTTP API·VM 복구 정책·실제 ESP32/차량 수락은 포함하지 않는다.
후속 실제 planner·binary 인계의 제공 범위와 검증 수준은
[drive-world-1](drive-world/README.md) 및 [현재 보고](../verification/recovery-scenario-closure-20261030.md#history-driving-app)를 따른다.

2026-11-09 신규 ESP32 연결 목표는 별도 [feedback-3 계약](gateway-feedback-3.md)이다.
원4시각/INSTALL ACK·원 absolute cutoff·STOP/HOLD와 측정 조향 품질을 정의하며
[고정 vector](vectors/gateway-feedback-3.json)·[actual codec 시험](../../platform/services/gateway/tests/test_feedback3_vectors.py)을 제공한다.
현재 ToF serial payload 수신·MCU 구현·실물 수락은 미완료다. 아래 G3의 feedback-1/2 원문과
기존 vector는 구형 장치의 명시 호환 선택으로 보존하고 자동 승격·downgrade하지 않는다.

## G1 설치와 호출

`xenova-gateway-client` RPM은 `xenova_gateway/__init__.py`, `client.py`, `ipc_contract.py`와 표준 라이브러리 의존성만 설치한다.
서버·Paho·serial·Authority를 import하지 않는다. 서버는 기존 `xenova-gateway` RPM, `python3 -m xenova_gateway --profile warm`에서 실행한다.
기본 endpoint는 `/run/xenova-gateway/control.sock`, override는 `--ipc-socket` 또는 `XENOVA_GATEWAY_IPC_SOCKET`이다.
root 소유 디렉터리 0700·소켓 0600과 Linux `SO_PEERCRED` UID=0을 모두 확인한다. client도 경로·소유·peer UID를 확인한다.
같은 UID의 프로그램을 서로 식별하거나 root 적대자를 격리하는 기능은 아니다. 운영 Worker도 현재 root 배치에서 접근한다.
다른 namespace에서는 해당 socket 경로만 제공해야 하며 전체 Authority 디렉터리를 공유할 필요가 없다.

```python
from xenova_gateway.client import GatewayClient, GatewayRejected, GatewayUnavailable, new_request_id

client = GatewayClient()  # 통신 budget_s=1.0; override는 0<값<=10초
request_id = new_request_id(host)  # 최초 송신 전에 한 번 생성하고 원 spec과 함께 보존
# request_id와 spec은 최초 호출 전에 Worker의 원 복구 기록에 보존한다.
# 아래 변수는 그 기록과 현재 Manager 실행/의도, client.context()에서 얻는다.
spec = dict(host_boot_id=host, target_vm=vm_id, target_execution_id=execution_id,
            intent_revision=intent_revision, lifecycle_generation=lifecycle_generation,
            expected_owner=owner, expected_generation=generation,
            deadline_ms=gateway_deadline_ms, parent_deadline_ms=recovery_deadline_ms)
try:
    result = client.submit(request_id, spec)  # 기본 purpose='protect'
except GatewayUnavailable as error:
    # UNAVAILABLE 또는 OUTCOME_UNKNOWN. timeout은 취소·효과 없음의 증거가 아니다.
    result = client.get(request_id)  # 같은 key. 재조회도 불가하면 원 작업을 미확정으로 보존
except GatewayRejected as error:
    raise  # error.code별 처리; 새 key/기한/force로 우회하지 않는다
```

필수 UUID는 canonical 문자열, revision은 bool이 아닌 0 이상 정수, generation은 1..2^32-1,
기한은 같은 host boot의 단조시각 ms 양의 정수다. Gateway 기한은 부모 복구 기한을 넘지 않는다.
`parent_deadline_ms` 생략은 최초 `deadline_ms`; 재전달에서는 저장된 원 값이다.
`purpose`는 `protect` 또는 `handoff`. handoff는 `mode=AUTO|MANUAL`을 받고 최초 생략은 현재 모드,
재전달 생략은 원 모드다. null·무효값·알 수 없는 필드는 거절한다.
필수 실행·의도·승인 문맥은 생략으로 복원하지 않는다.

MANUAL handoff의 선택 `manual_hold=true`는 명시적인 사용자 MANUAL 의도를 보존한다.
AUTO 입력/준비 회복만으로 복귀하지 않으며 기존 resume/countdown을 거쳐야 한다.
신규 생략/false는 기존 동작이고, 같은 원 요청의 재전달 생략은 원 선택을 사용한다.
true와 false의 실제 변경은 충돌하며 null·비불리언은 거절한다. 다른 목적/모드로 확대하지 않는다.

| 함수 | 입력·결과 |
|---|---|
| `context()` | 메모리의 현재 host/owner/generation/mode/phase, 출력 허가·저장 미확정, 대상별 binding 요약. Xen/Store 재조회 없음 |
| `history()` | 현재 보존 수·정리 가능 수·bytes·정책 요약. 조회는 정리나 fsync를 수행하지 않음 |
| `submit(request_id, spec)` | actor가 내구 수락한 원 작업 view. queue 접수만으로 성공 응답하지 않음 |
| `get(request_id)` | 원 진행/결과와 현재성. 조회가 준비·복구·승계를 새로 실행하거나 기한을 갱신하지 않음 |
| `commit(request_id)` | handoff의 현재 준비·원 승인 대조 후 기존 권위 전환. COMMITTED는 앱/장치 완료와 다름 |
| `cancel(request_id, *, context=None)` | handoff는 context 없이 지원 경계에서 취소. 커밋 후에는 COMMITTED 원 결과를 반환하며 rollback하지 않음 |
| `cancel(request_id, context=context)` | protection의 원 실행에 대한 명시 철회. 현재 host/generation/원 실행을 정확히 대조 |
| `finish(request_id, context)` | protection 대상의 정당한 새 binding 실행 확인 뒤 RELEASED. context 필드는 아래 세 개 |

보호 종결 context는 `host_boot_id`, `expected_generation`, `target_execution_id`다.
finish에는 새 실행, cancel에는 원 실행을 요구한다. 현재 승인 경합은 AUTHORITY_CHANGED이며 호출자가 재판단한다.
통신 종료·기한 만료가 보호를 자동 해제하지 않는다. 종결은 출력 허가·재무장·generation 발급이 아니다.
release 뒤 새 준비·계산·기존 명시 재무장 경로가 필요하다.

## 원 요청과 결과 읽기

같은 key의 정규화된 의미가 같으면 원 작업이다. JSON 순서·공백과 선택 기본값의 생략/동일 명시는 충돌하지 않는다.
다른 대상·실행·의도·owner/generation·목적·원 기한·승인은 IDEMPOTENCY_CONFLICT다. 원 기록을 바꾸지 않는다.
원 값은 현재 binding/기본값으로 다시 선택하지 않는다. 과거 성공은 현재 복구 허가가 아니다.

응답의 `acceptance=durable`은 기존 Authority 저장 성공이다. 원 보호는 `phase=PROTECTED|RELEASED|CANCELLED`,
계획은 `PREPARING|PREPARED|COMMITTED|CANCELLED|FAILED|ABANDONED`를 사용한다.
`original.app`·`original.device`는 원 기한 안에서 확인하고 한 번 저장한 단계 증거다. 미확인 단계는 null이다.
`app_applied`, `device`, `protection.current_target_matches/current_host_matches`는 현재 view다.
`device`가 다른 최근 명령을 가리킬 수 있으므로 `command_generation/counter/sequence/kind`, session과 나이를 함께 대조한다.
`original.device.physical_stop=true`도 장치가 보고한 stationary 증거이며 실제 차량 시험의 완료 선언이 아니다.

Worker는 보호 목적이면 원 key/host/대상/실행 일치, durable, PROTECTED, held·owner_excluded,
현재 host/대상 일치와 필요한 출력 측 단계를 확인한다. 그 뒤에도 Manager의 현재 intent/execution/소유·force guard가 필요하다.
보호 동안 해당 VM의 재선택을 중앙 Authority 발급과 binding/준비 경계에서 차단한다.
예비 대상은 exclusion만 저장하고 정상 Active의 STOP·새 generation을 만들지 않는다.
활성 대상은 RAM 출력 철회→STOP 시도→필수 저장 순서다.

계획 인계는 정상 A를 유지해 B를 준비하고, 커밋 뒤 새 문맥의 앱 적용·계산을 기다린다.
기존 stateless 경로는 유지한다. stateful의 `continuity=stop_then_resume`이면 연속 승계로 표시하지 않고 정지·명시 재개가 필요하다.

신규 `--drive-link` 프로필의 연속 인계는 직전 실제 명령의 **남은 원 유효기간**만 유지한다.
커밋·재전달이 command expiry를 갱신하지 않는다. 새 target의 현재 binding·자기 계산과
출력 guard를 통과해야 이후 명령을 제출한다. 장치 자체 응답 지연까지 제거하는 보장은 아니다.
`context().output_wait`는 정확한 원 목표 하나의 rate 대기와 현재 MANUAL 정지 확인 상태를
설명한다. 대기는 수락/제출/장치 적용 성공이 아니다. 새 MANUAL 이동은 해당 Pi/device session과
generation의 STOP/APPLIED·stationary 관측 뒤에 허용한다. 원 기한이 지나면
`PHYSICAL_STOP_UNCONFIRMED`로 제한하며 값 0·타이머·옛 ACK로 정지를 만들지 않는다.
같은 유효 driver session의 MANUAL A/B 연속은 새 정지/재조작과 구별한다.

| 코드·상황 | 호출자의 처리 |
|---|---|
| `UNAVAILABLE` | 접속 전 실패. 같은 원 key로 이후 재확인 |
| `OUTCOME_UNKNOWN` | 전송 뒤 timeout/EOF/해석 실패 또는 원 저장 미확정의 부재. 취소/미수락으로 단정하지 않음 |
| `NOT_FOUND` | 현재 정상 저장소에 원 key 없음. 오래된 승인/다른 host로 새 실행하지 않음 |
| `BUSY_NOT_ACCEPTED` | 해당 메시지는 mailbox에 넣지 못함. 기존 같은 key가 이미 존재할 가능성은 별도로 조회 |
| `INVALID_FRAME/INVALID_INPUT` | 문법·값 오류. 원 값으로 덮지 않음 |
| `TARGET_CHANGED/HOST_CHANGED/AUTHORITY_CHANGED/DEADLINE_EXCEEDED` | 현재 승인과 대상 재판단. 원 요청을 새 문맥으로 덮지 않음 |
| `STORAGE_UNCERTAIN` | 내구 수락/결과 미확정. 원 저장 수습 없이 정상 완료 주장 금지 |
| `HISTORY_FULL/HISTORY_BYTES_RESERVED` | 개수 또는 필수 후속 증거 예약 공간 부족. 활성 보호의 정당한 종결·보존 만료 후 같은 원 요청 확인. 강제 삭제 금지 |
| `REQUEST_WINDOW_EXPIRED` | 새 수락 가능 기간 밖의 key. 기한 변경·재발급으로 같은 요청을 실행하지 않음 |
| `RESULT_NOT_RETAINED_OR_UNKNOWN` | 결과 보존 밖일 수 있는 부재. 과거 수락/정리 사실이나 성공을 재구성하지 않음 |
| `LEGACY_KEY_NOT_ADMISSIBLE/HISTORY_POLICY_REQUIRED/REQUEST_DEADLINE_OUTSIDE_WINDOW/REQUEST_TIME_INVALID` | 신규 key/설정/원 예산의 계약 오류. 기존 원 결과는 같은 key로 조회하며 원장을 변환하지 않음 |
| `TRANSITION_REJECTED/INTERNAL_ERROR` | 기존 상태·상세 reason 또는 내부 원인 보존. 자동 force/rollback 없음 |

원 보호와 전환은 기존 64KiB Authority 안에 각각 최대 16/32개다. 별도 journal·DB·무제한 이력을 만들지 않는다.
명시 history 정책을 선택하면 아래 조건의 종결 이력만 신규 내구 수락과 함께 정리한다.
Gateway 재시작은 원 결과를 회수하지만 자동 출력·재무장을 하지 않는다. 새로운 optional 필드를 모르는 구형 내부 writer와 혼합하지 않는다.
외부 client는 wire v1을 사용하며 내부 Authority schema를 선택·해석하지 않는다.

### 이력 수명과 반복 운영

`--history-policy` / `XENOVA_GATEWAY_HISTORY_POLICY`는 root 소유 0600 JSON의 양의 정수
`retry_ms`, `retention_ms`를 받는다. 운영값은 승인 입력이며 코드에 기본 시간을 넣지 않는다.
미설정은 기존 UUID 신규 호출·기존 보존 한도를 유지한다. 이미 수명 fence를 저장한 뒤에는
정책 파일을 빼도 새 legacy UUID 수락을 다시 열지 않는다.

새 호출자는 `new_request_id(host_boot_id)`를 최초 송신 전에 한 번 사용한다.
key는 `g1:<host UUID>:<Pi monotonic 발급 ms>:<UUID nonce>`다. 같은 host의 발급 시각이며 벽시계를 사용하지 않는다.
형식은 client가 제공하고 호출자는 key와 spec을 원 attempt에 보존한다. 재조회·재연결·재시작에서 다시 만들지 않는다.
이 형식은 사용자가 승인한 시간 포함 key 확장이며 기존 원장은 변환하지 않는다.
정책 선택 후 신규 opaque UUID는 제한하고, 저장된 과거 UUID의 submit/get/commit/cancel/finish는 그대로 지원한다.
이 제한은 유한 저장에서 임의 UUID의 무기한 중복 판별을 약속하지 않기 위한 배포 호환 경계다.

새 요청의 원 deadline은 발급 시각+retry_ms 이내다. 수락 후 `retry_until_ms`와
`retained_through_ms=max(retry_until_ms, 원 deadline+retention_ms)`를 원 기록에 고정한다.
이는 **신규 수락 가능 기간**, **상세 결과의 최소 조회 보존 기간**, **실제 작업 기한**을 구분한다.
보존 기간은 실행/출력 허가가 아니다. 정책 변경이 이미 약속한 기간을 줄이지 않는다.

actor는 신규 수락 시 기존 최대 48개 기록을 한 번 확인한다. PROTECTED, PREPARING/PREPARED,
현재 PENDING 전환이 참조하는 COMMITTED, 저장 미확정은 정리하지 않는다.
나머지도 후속 증거의 원 deadline과 고정 조회 보존이 끝나야 정리한다.
정리와 신규 수락·최소 발급시각 fence는 같은 Authority 원자 교체/file·parent fsync에 들어간다.
실패하면 RAM 공간을 먼저 반환하지 않는다. get·history·주행 주기에는 정리 쓰기를 추가하지 않는다.

정리된 발급시각 이하의 key는 기한을 바꿔도 신규 수락되지 않는다. fence는 정책 기간을 늘려도 후퇴하지 않는다.
다른 host key는 시각을 비교하지 않고 HOST_CHANGED다. 기존 host의 활성 보호는 자동 삭제하지 않는다.
과거 key의 부재를 최초 부재·정리 완료로 무기한 정확히 구별하지 않으며
`RESULT_NOT_RETAINED_OR_UNKNOWN`은 그 한계를 표시한다. 만료된 승인으로 재실행하지 않는다.

전체 64KiB 상한은 유지한다. 신규 수락 전에 기록당 최대 4096 bytes와 현재 제어/pending 여유 4096 bytes를 예약한다.
따라서 16/32개는 별도 개수 상한이고, 실제 동시 보존 가능 수는 bytes 예약으로 더 작을 수 있다.
수락 주기×고정 보존 기간이 이 용량을 넘으면 근거 있는 수락 제한이며 무제한 queue로 우회하지 않는다.
운영 담당은 지원 호출 빈도·원 기한·필수 결과 회수 기간에 맞춰 두 시간을 승인해야 한다.

로컬 반복 예제는 `examples/provider_lab.py --directory <새 소유 xenova-gateway-lab-* 경로>
--history-retry-ms 180 --history-retention-ms 30`과 `tests/test_history.py`다.
180/30ms는 빠른 보존 경계 시험값이며 운영값이 아니다. 실제 별도 프로세스/UDS에서
100회 요청·종결을 같은 저장소로 반복하고, 원 결과·재시작·실패 보호를 확인한다.

## framing·수명·비용

4-byte network-order 길이 + 최대 8192바이트 UTF-8 JSON 객체, 연결당 요청 하나/응답 하나다.
wire envelope는 `version=1`, `action`, 필요한 `request_id`, `spec`이고 client가 처리한다.
응답은 `{version:1,ok:true,data:{...}}` 또는 `{version:1,ok:false,error:{code:...}}`다.
중복 JSON key·비유한 수치·미지 필드·초과 크기·EOF·부분/붙은 프레임을 검사한다.
수신 header부터 전체 I/O 기한은 1초, 연결 최대 16, 요청/응답 각각 최대 8192바이트다.
selector thread 하나와 기존 actor의 mailbox 4(cancel/finish)·4(get/context)·8(submit/commit)를 사용한다.
actor는 lane을 순환해 하나씩 처리한다. 느린 client의 write/recv는 actor에서 기다리지 않는다.
모든 연결을 점유하면 새 연결은 거절되고 기존 연결 만료 뒤 정상 회복한다. 모든 과부하에서 무거절을 약속하지 않는다.

기존 Gateway lease가 있는 프로세스만 endpoint를 만든다. 활성 socket·symlink·다른 파일은 unlink하지 않는다.
죽은 owned socket만 동일 inode/ECONNREFUSED 확인 후 회수하고 종료 때도 자기 inode만 제거한다.
unit의 RuntimeDirectory는 이 임시 socket만 다룬다. `/srv/xenova/drive-control`은 함께 지우지 않는다.
필수 저장은 actor에 남으며, 단일 device owner가 명령 만료의 STOP과 RX/TX를 담당한다.
실제 장치 I/O가 막히면 종료 미확정이며 다른 writer로 바꾸지 않는다. 장치 자체 watchdog은 별도 수락이다.
lookup/replay에 추가 fsync·전체 Xen/Store 관측은 없다. 앱/장치 원 단계 증거만 확인 시 최대 한 번씩 추가 저장한다.

## G2 완전 상태 사본

[MQTT 계약](mqtt-contract-v1.md#drive-state-1)의 `app.state.snapshot`, `app.state.applied`를 사용한다.
UDS로 지도·상태 본문을 보내지 않는다. Python `xenova_mqtt.handoff`와 C `xm_state_*`가 같은 codec을 제공한다.
Gateway는 당시 허용 출처의 완전 사본 최대 8개, 현재 target application 최대 4개를 RAM에 보관한다.
부분 수신 큐는 4개, MQTT 본문 최대 8192바이트이며 상태 payload C parser는 4096바이트다.
권위가 있던 때 수락한 사본은 현재 호환성 검사 후 사용하지만 권한 종료 후 도착한 옛 source 갱신은 거절한다.
재시작 후 사본이 없으면 다시 준비한다. 내구 원 전환 결과를 RAM 사본이 있다고 꾸미지 않는다.

실제 앱 연결: Python `StateReceiver.receive/rebuild`→`ManeuverAdapter.prepare/calculate`,
C `drive_adapter_apply_maneuver`→기존 `drive_state_preparation`을 사용한다.
앱은 merge segment·기하/동역학적 적합성과 실제 계산을 책임진다. 적용만으로 progress/READY를 발급하지 않는다.
기본 C 알고리즘은 stateless로 유지한다. 신규 지도형 프로필의 실제 planner/path 생산·전체 적용·
target 계산은 [drive-world-1](drive-world/README.md)의 별도 bounded binary 통로를 사용한다.
기존 JSON codec·테스트 stateful adapter의 성공을 신규 실제 계산의 수락으로 대신하지 않는다.

## G3 명시 선택 serial feedback-1

기본 `write-only`의 기존 bytes는 바꾸지 않는다. feedback-1은 warm+serial에서 명시 선택하고 승인된
`--feedback-timeout-ms` 또는 `XENOVA_GATEWAY_FEEDBACK_TIMEOUT_MS`를 반드시 제공한다.
패키지에는 운영 기한·센서 capability·차량 보정값을 넣지 않는다. 호환 firmware 미확인이면 선택하지 않는다.

모든 신규 외부 frame은 기존 COBS + 0 delimiter, raw `[id:u16le][length:u8][payload][CRC16/CCITT-FALSE:u16le]`다.
CRC 범위는 id/length/payload다. 최대 frame 256바이트, payload 최대 240바이트다.

| ID | payload (little endian, UUID는 RFC4122 16 bytes) |
|---|---|
| HELLO `0x0f00` | version:u8=1, pi_session:uuid |
| HELLO_ACK `0x0f01` | version:u8=1, echoed_pi_session:uuid, device_boot_session:uuid, capabilities:u16 |
| COMMAND `0x0110` | pi_session:uuid, device_session:uuid, sequence:u64 + **기존 완전 encoded DRIVE/STOP frame** |
| FEEDBACK `0x0f02` | pi_session:uuid, device_session:uuid, sequence:u64, generation:u32, counter:u8, status:u8, sample_seq:u64, flags:u8, velocity_mmps:i32, steering_mrad:i32, stationary:u8 |

capabilities: ACCEPT=1, APPLY=2 필수; VELOCITY=4, STEERING=8, STATIONARY=16 선택이다.
status: ACCEPTED=1/APPLIED=2/REJECTED=3/FAULT=4. flags: velocity=1/steering=2/stationary=4.
선언하지 않은 센서 flag는 거절한다. 미지원·미수신은 null이며 0/false로 채우지 않는다.
샘플은 device session별 증가하는 u64, 명령 sequence는 Pi session별 비순환 u64다. 기존 u8 counter의 wrap을 sequence와 혼동하지 않는다.
새 장치 session은 이전 명령/ACK를 무효화하고 fault로 출력 철회한다. 새 Pi 프로세스도 새 session으로 handshake한다.
역전/중복·다른 session·원 명령 불일치·기한 지난 ACK는 현재성을 갱신하지 않는다.
handshake/apply timeout·fault는 출력 차단/STOP에 연결되고 명시 재시작/기존 재무장 없이 DRIVE를 복구하지 않는다.
ACK는 비차단 read 완료 직후의 보수적 수신 시각으로 만료를 검사한다. 같은 read에서 완성한 frame은
그 시각을 쓰고 partial frame은 완성된 다음 read 시각을 쓴다. poll 시작 시각이나 이후 decode 시각으로
바꾸지 않는다. 기한 내 받은 적용 사실과 현재 age 유효성은 별도이며, 새 명령·fault·관측 만료는 계속
현재 view를 제한한다. RX는 최대4096바이트
안에서 검증된 frame부터 순차 적용하며, POSIX 장치 소유자는 수신 준비와 새 STOP 통지로 깨어난다.
고정 native POSIX backend의 소유 경로는 기존 readiness 뒤 최대4096바이트를 한 번 읽는다.
idle의 빈 읽기와 HUP는 같은 owner가 확인한 readable 근거로 구별한다. STOP/만료/종료가 RX보다
우선하며, 이 경로의 취소는 기존 owner wake를 사용한다. 직접 `poll()`의 pyserial `cancel_read()`와
URL/custom backend는 기존 public read 경로를 유지한다. 이 구분은 wire·ACK 기한을 바꾸지 않는다.
accepted는 적용이 아니고 applied는 물리 정지가 아니다. physical_stop은 해당 STOP에 대응하는 지원 stationary 보고가 있어야 값이 생긴다.

parser/RX/TX는 같은 device owner에서 처리한다. 1회 RX 최대 4096바이트, pending command 최대 32, 적용 사건 queue 최대 64다.
만료 STOP을 pending DRIVE보다 우선하며 STOP 참조 공간을 유지한다. 오래 막힌 실제 write는 안전 성공으로 표시하지 않는다.
Pi의 현재 device view는 broker/Jetson 접속을 기다리지 않는다. 새 Jetson odom 전송 기능은 이 프로필이 자동 제공하지 않는다.

codec·고정 vector·PTY 가상 장치는 [제품 codec](../../platform/services/gateway/xenova_gateway/feedback.py),
[시험](../../platform/services/gateway/tests/test_feedback.py), [가상 장치](../../platform/services/gateway/examples/virtual_esp32.py)에 있다.
고정 hex vector는 [feedback-1 JSON](vectors/gateway-feedback-1.json)이며 제품 시험이 매번 encode 결과와 대조한다.
실제 ESP32 생산·watchdog/E-stop·차량 물리 정지는 [장치 요청](../requests/20261029-request-esp32.md)과 7번에서 공동 수락한다.

### 명시 선택 feedback-2와 Pi 로컬 odometry

`--serial-profile feedback-2 --odometry-config <private-file>`에서만 선택한다.
feedback-1 명령/ACK frame은 유지하고 HELLO/ACK의 version을 2로 명시하며 capability
ODOMETRY=32를 추가로 요구한다. 구형 firmware에 자동으로 새 프레임을 보내지 않는다.
기존 COBS/CRC·single serial owner·부분 frame 상한을 그대로 사용한다.

명령의 `DEVICE_APPLY_TIMEOUT`은 출력 차단과 원 실패에 계속 남는다. 이 사유만 있을 때는
동일 device/Pi session의 독립 encoder probe·표본 수집을 계속한다. 기존 시각·순서·품질·연속성·만료
검사를 통과한 `vehicle_sample`만 제공하며 명령의 accepted/applied/physical_stop을 복구하지 않는다.
장치 재시작·link 상실·다른 fault는 센서 관측을 계속 차단한다. 유효 센서 수신은 재무장·DRIVE 허가가 아니다.
장치 소유자가 지연된 경우에도 `OutputOwner.device_view`는 게시된 원 표본 시각과 기존 age 한도로
차량 표본과 명령 관측의 현재성만 다시 제한한다. 원 snapshot을 변경하거나 owner lock·새 관측을 요구하지 않는다.

새 probe의 `ODOM_CLOCK_UNCERTAIN`, 원 probe의 `ODOM_PROBE_EXPIRED`, 미래 시각이 아닌
미등록·이미 소비한 echo의 `ODOM_PROBE_REFERENCE`는 그 packet만 거절한다. 이미 채택한 동일
session/epoch의 정상 표본은 원 `pi_sample_us`·`clock_error_us`와 기존 age 안에서만 보관한다.
거절 오류·counter·최초 원인·pending 소비는 유지하며, 원 표본의 시각·sequence·epoch·기한을
갱신하거나 같은 표본을 새 진행으로 재발행하지 않는다. 원 나이를 넘으면 조회는 계속 null이다.
동일 `ODOM_PROBE_REFERENCE`라도 음수 RTT·시계 역전은 기존 표본을 즉시 실효시킨다.
기존 검증 순서에서 판정한 세션·frame·품질·counter·순서·geometry 오류, 장치 session 교체와
연속성 gap의 epoch reset, 장치/link fault도 기존 즉시 실효를 유지한다. 앞단에서 거절한 packet의
뒤쪽 필드를 새로 검사해 다른 사유로 바꾸지 않는다. 이 제공자 수명 규칙은 새 wire·운영 숫자를
추가하지 않으며 [원 유효 표본 수명 시험](../../platform/services/gateway/tests/test_odometry_last_valid_lifetime.py)으로 확인한다.

| ID | payload (같은 little-endian·UUID 규칙) |
|---|---|
| ODOM_PROBE `0x0f04` | pi_session:uuid, device_session:uuid, pi_send_us:u64 |
| ODOM_REPORT `0x0f03` | pi_session:uuid, device_session:uuid, echoed_pi_send_us:u64, device_sample_us:u64, source_sample:u64, cumulative_left_ticks:i64, cumulative_right_ticks:i64, quality:u8, steering_mrad:i32, steering_error_mrad:u16 |

device_sample_us는 실제 encoder 표본의 장치 시각이다. 원 probe의 Pi send/receive 구간으로
시계 오차를 제한하고 장치 시각과 Pi 시각을 직접 빼지 않는다. source_sample은 같은 device
session에서 증가하며 cumulative encoder는 회신 송신 횟수나 명령 적분값이 아니다.
장치는 해당 probe를 수신한 뒤 encoder 표본을 생성하거나 래치하고, 그 원 관측 시각을
device_sample_us로 보낸다. probe 전에 만든 cache 표본을 새 관측처럼 회신하면 이 시계
구간의 전제가 성립하지 않는다. Pi가 그 전제를 모든 frame에서 독립적으로 증명하는 것은
아니므로 실제 MCU의 수신 dispatcher·센서 래치 수락에서 확인해야 한다.

wire의 pi_send_us·echoed_pi_send_us와 bytes는 유지한다. 이 값은 원 probe의 참조로 그대로
echo하며, Pi는 완성된 frame을 처음 serial 제출하기 직전의 시각을 기존 pending에 한 번만
별도로 고정한다. RTT 오차와 Pi 중점은 이 첫 제출부터 실제 read 완료까지의 구간을 사용한다.
중복 mark·부분 write·EIO로 첫 시각을 갱신하거나 미제출로 되돌리지 않는다. 원 echo 기준
pending 보존 한도도 유지하며, 송신 전 또는 회수 때 그 한도를 넘으면 ODOM_PROBE_EXPIRED다.
첫 제출 뒤 실제 왕복이 긴 경우의 ODOM_CLOCK_UNCERTAIN과 구분한다. 이 변경은 새 장치
framing·session·기한·운영 허용치를 추가하지 않는다.
quality bit encoder=1은 필수, steering measured=2/estimated=4는 동시에 켤 수 없다.
steering을 제공하면 양의 오차 범위도 제공한다. 센서 미지원의 0을 실제 측정 0으로 표시하지 않는다.

private 0600 설정은 vehicle/calibration·metres_per_tick·track_width_m·wheelbase_m와 실제 속도·
시계/표본 나이·주기·encoder bits·pose 오차의 승인값을 공급한다. 필드/strict 검사는
`odometry.py:OdometryConfig`가 원본이다. 시험 수치를 운영 default로 넣지 않는다.
첫 표본은 속도 추측에 쓰지 않으며 reset·긴 불연속은 새 odom epoch로 분리한다.
Pi `VehicleSample` 발행은 Jetson 연결을 기다리지 않는다. 명령 수락/적용·stationary와
odom의 실제 표본은 각자의 원 session/sequence를 유지한다.

실제 제품 parser/PTY 왕복·부정 표본과 정상 회복은 `tests/test_odometry.py`, 가상 차량은
`platform/apps/drive/tests/run_lattice_pair.py`에서 제공한다. 프레임의 바이트 vector와
독립 encoder는 아래 장치 요청에 연결한다. 실제 MCU 생산·차량 보정·물리 정지 완료가 아니다.

## 바로 실행할 예제와 시험

설치 후보에서 `python3 /usr/share/xenova/gateway-examples/provider_roundtrip.py`를 실행한다.
이는 별도 Gateway process·실제 UDS/Authority를 사용하고 앱/Xen/output만 fixture로 대체한다. 운영 endpoint·자산·broker·serial은 사용하지 않는다.
소스에서는 기존 환경의 Gateway/MQTT 경로를 PYTHONPATH에 지정해 `python3 platform/services/gateway/examples/provider_roundtrip.py`다.
실제 호출 CLI는 `python3 -m xenova_gateway.client context` 및 `submit/get/commit/cancel/finish --request-id UUID [--spec-file FILE]`다.
CLI exit 0=정식 view, 2=서버 거절, 3=통신/로컬 입력 오류다. exit 0도 앱/장치 완료를 뜻하지 않는다.
lab의 nonroot peer만 `--peer-uid`/client의 `uid`를 명시한다. 운영 권한을 낮추는 우회 옵션이 아니다.

`pytest -o addopts= -q platform/services/gateway/tests`가 C01~C14의 실제 socket/process·MQTT·PTY 시험을 포함한다.
isolated broker 시험은 `XENOVA_TEST_BROKER_ROOT`, `XENOVA_TEST_QEMU`, C 교차는 `XENOVA_TEST_STATE_C`가 필요하다.
미공급 환경의 skip은 실제 broker/C 교차 성공으로 세지 않는다. native codec은 `cmake -S platform/apps/common/xenova-mqtt-c -B OWNED_BUILD` 후 build/ctest다.

설계 참고는 [Linux unix(7)](https://man7.org/linux/man-pages/man7/unix.7.html),
[Python Socket HOWTO](https://docs.python.org/3/howto/sockets.html),
[systemd.exec](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html)다.
이 참고문서의 최신 버전을 target 지원 근거로 쓰지 않고, 고정 Python3.14.7·systemd259.5·WSL socket 시험과 구분한다.
