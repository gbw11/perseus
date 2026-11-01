# Gateway 제공자 계약 — 로컬 호출·상태 사본·장치 피드백

2026-11-01 제공자 구현이다. 이 문서의 wire 선택은 이번 변경에서 추가했다.
검증 후보·실행 결과·대역 경계는 [보고서](../verification/recovery-scenario-closure-20261030.md#gateway-provider)에 둔다.
외부 HTTP API, VM 복구 정책, 실제 planner·ESP32·차량 수락은 포함하지 않는다.

## G1 설치와 호출

`xenova-gateway-client` RPM은 `xenova_gateway/__init__.py`, `client.py`, `ipc_contract.py`와 표준 라이브러리 의존성만 설치한다.
서버·Paho·serial·Authority를 import하지 않는다. 서버는 기존 `xenova-gateway` RPM, `python3 -m xenova_gateway --profile warm`에서 실행한다.
기본 endpoint는 `/run/xenova-gateway/control.sock`, override는 `--ipc-socket` 또는 `XENOVA_GATEWAY_IPC_SOCKET`이다.
root 소유 디렉터리 0700·소켓 0600과 Linux `SO_PEERCRED` UID=0을 모두 확인한다. client도 경로·소유·peer UID를 확인한다.
같은 UID의 프로그램을 서로 식별하거나 root 적대자를 격리하는 기능은 아니다. 운영 Worker도 현재 root 배치에서 접근한다.
다른 namespace에서는 해당 socket 경로만 제공해야 하며 전체 Authority 디렉터리를 공유할 필요가 없다.

```python
from xenova_gateway.client import GatewayClient, GatewayRejected, GatewayUnavailable

client = GatewayClient()  # 통신 budget_s=1.0; override는 0<값<=10초
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

| 함수 | 입력·결과 |
|---|---|
| `context()` | 메모리의 현재 host/owner/generation/mode/phase, 출력 허가·저장 미확정, 대상별 binding 요약. Xen/Store 재조회 없음 |
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

| 코드·상황 | 호출자의 처리 |
|---|---|
| `UNAVAILABLE` | 접속 전 실패. 같은 원 key로 이후 재확인 |
| `OUTCOME_UNKNOWN` | 전송 뒤 timeout/EOF/해석 실패 또는 원 저장 미확정의 부재. 취소/미수락으로 단정하지 않음 |
| `NOT_FOUND` | 현재 정상 저장소에 원 key 없음. 오래된 승인/다른 host로 새 실행하지 않음 |
| `BUSY_NOT_ACCEPTED` | 해당 메시지는 mailbox에 넣지 못함. 기존 같은 key가 이미 존재할 가능성은 별도로 조회 |
| `INVALID_FRAME/INVALID_INPUT` | 문법·값 오류. 원 값으로 덮지 않음 |
| `TARGET_CHANGED/HOST_CHANGED/AUTHORITY_CHANGED/DEADLINE_EXCEEDED` | 현재 승인과 대상 재판단. 원 요청을 새 문맥으로 덮지 않음 |
| `STORAGE_UNCERTAIN` | 내구 수락/결과 미확정. 원 저장 수습 없이 정상 완료 주장 금지 |
| `HISTORY_FULL` | 보호 16개 한도. 조용한 이력 삭제·key 신규 취급 없음 |
| `TRANSITION_REJECTED/INTERNAL_ERROR` | 기존 상태·상세 reason 또는 내부 원인 보존. 자동 force/rollback 없음 |

원 보호와 전환은 기존 64KiB Authority 안에 각각 최대 16/32개다. 별도 journal·DB·무제한 이력을 만들지 않는다.
기록을 자동 축출하지 않으며 한도에서는 새 수락을 거절한다. 지원 이력 정리 없이 오래된 key를 재사용하지 않는다.
Gateway 재시작은 원 결과를 회수하지만 자동 출력·재무장을 하지 않는다. 새로운 optional 필드를 모르는 구형 내부 writer와 혼합하지 않는다.
외부 client는 wire v1을 사용하며 내부 Authority schema를 선택·해석하지 않는다.

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
소유한 현재 기본 C 알고리즘은 stateless로 유지한다. 신규 codec/적용 훅과 테스트 stateful adapter가 제공됐으며 실제 planner 상태 생산·계산 연결은 남는다.

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
accepted는 적용이 아니고 applied는 물리 정지가 아니다. physical_stop은 해당 STOP에 대응하는 지원 stationary 보고가 있어야 값이 생긴다.

parser/RX/TX는 같은 device owner에서 처리한다. 1회 RX 최대 4096바이트, pending command 최대 32, 적용 사건 queue 최대 64다.
만료 STOP을 pending DRIVE보다 우선하며 STOP 참조 공간을 유지한다. 오래 막힌 실제 write는 안전 성공으로 표시하지 않는다.
Pi의 현재 device view는 broker/Jetson 접속을 기다리지 않는다. 새 Jetson odom 전송 기능은 이 프로필이 자동 제공하지 않는다.

codec·고정 vector·PTY 가상 장치는 [제품 codec](../../platform/services/gateway/xenova_gateway/feedback.py),
[시험](../../platform/services/gateway/tests/test_feedback.py), [가상 장치](../../platform/services/gateway/examples/virtual_esp32.py)에 있다.
고정 hex vector는 [feedback-1 JSON](vectors/gateway-feedback-1.json)이며 제품 시험이 매번 encode 결과와 대조한다.
실제 ESP32 생산·watchdog/E-stop·차량 물리 정지는 [장치 요청](../requests/20261029-request-esp32.md)과 7번에서 공동 수락한다.

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
