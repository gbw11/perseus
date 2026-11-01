# Xenova Gateway: 제어 문맥과 출력 경계

Gateway는 모드·owner·출력 허가를 구분하고, 현재 허용된 앱 명령을 ESP32 시리얼로
제출한다. VM 재시작은 기존 Recovery Engine, 최종 물리 정지는 장치 watchdog/E-stop의
책임이다. PUBACK·준비 보고·시리얼 쓰기 수락은 장치의 적용 ACK가 아니다.

## verified 프로필 운영

기본 프로필은 공통 A/B용 `verified`다. 먼저 같은 번들·서로 다른 UUID/인증정보와
Worker의 실행별 provisioning 및 바인딩을 준비한다. 자세한 역할과 입력 조건은
[공통 펌웨어 안내](../../apps/drive/README.md), 메시지 정의는
[MQTT 계약](../../../docs/contracts/mqtt-contract-v1.md#공통-ab-펌웨어-프로필)에 있다.

확인한 **최초 설치**에서만 root로 아래를 한 번 실행한다. 실제 `/srv/xenova` 마운트를
확인하고 UUID를 실제 등록 값으로 바꾼다. 이미 원본/표지가 있으면 덮어쓰지 않는다.

```bash
python3 -m xenova_gateway \
  --vm-a aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa \
  --vm-b bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb \
  --initialize-control-state
```

최초 원본은 SAFE_STOP·disarmed다. `--initial-active-vm`은 이 초기화 명령에서만
선호 UUID를 바꾸는 선택 옵션이다. 평상시에는 원본의 owner/모드를 쓴다.

```bash
python3 -m xenova_gateway --sink mock \
  --vm-a aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa \
  --vm-b bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb
```

broker 주소·계정은 기존 `--host/--port/--username`과 `XENOVA_GATEWAY_BROKER_PASSWORD`를
사용한다. 제품 인증정보를 명령행·Git·공개 로그에 넣지 않는다. 선택 경로는
`--authority-dir`(`/srv/xenova/drive-control`), `--binding-file`
(`/run/xenova/drive-bindings.json`)이다. `--start-mode`는 legacy 전용이다.
실제 출력은 검증된 장치/복구 경로와 아래 차량 한계 파일을 준비한 뒤
`--sink serial --serial-port /dev/ttyUSB0 --vehicle-limits /etc/xenova/vehicle-limits.json`로 선택한다.
Gateway 자체의 Yocto recipe·자동 시작 연결은 아직 없으므로 파일 존재를 이미지 포함으로
간주하지 않는다. Worker/helper/common MQTT는 각 기존 recipe에서 설치한다.

정지/Standby에서도 앱 준비를 확인한다. 적합한 다른 VM이 있으면 선택할 수 있지만,
재시작·신원 미확인·전환 중·transport 상실·시리얼 오류 때는 출력을 금지한다.
신선한 현재 준비 3초 → 현재 control 메시지 ID에 결합한 명시적 요청 → 3초 카운트다운
→ 새 generation의 저장/발행/적용 확인 순서로 재무장한다. AUTO_READY도 같은 조건으로
AUTO에 복귀한다. 계산 준비 회복만으로 자동 출발하거나 선호 A로 자동 원복하지 않는다.

`drive.resume.requested` payload는 `expected_generation`과 선택 `requested_by`,
공통 envelope의 `causation_id`는 Gateway가 현재 ACK를 확인한 control의 message_id다.
Gateway 재연결/재시작 뒤에는 새 게시 ID를 확인해야 한다. 같은 요청 ID는 원본에
저장해 중복 승인하지 않는다. 요청 소비자가 오프라인 요청을 새 승인으로 재발행하면
안 된다. 기존 Spring/UI의 이 계약 연결은 아직 필요하다.

로컬 운영 도구는 현재 control을 읽어 요청 문맥을 붙인다. 아래는 **요청 제출**이며
수락·카운트다운·최종 적용은 각각 확인한다. `--verified` 생략은 legacy 도구 동작이다.

```bash
# 해당 broker 계정의 암호는 XENOVA_RESUME_BROKER_PASSWORD로 별도 전달한다.
python3 platform/services/gateway/tools/send_resume.py --console --verified --host 127.0.0.1
```

제어 전환의 고정 적용 기한 1초는 재시도·옛 명령 수신으로 갱신하지 않는다. 실패는
CONTROL_PUBLISH_UNCONFIRMED / CONTROL_APPLY_UNCONFIRMED 등으로 남기고 STOP이다.
현재 준비를 확인한 운영자는 새 요청으로 재무장할 수 있다. 저장 상태·retained 충돌은
CONTROL_AUTHORITY_CONFLICT이며 원본/복원 근거를 확인해야 한다. 임의 초기화하지 않는다.

## 권위 원본 진단과 재확인

다음 로컬 도구는 root 전용이며 broker·시리얼·VM을 조작하지 않는다. 실제 데이터 마운트를
검증하고 원본을 조회한다. 디렉터리·marker·원본을 생성하거나 초기화하지 않는다.

```bash
python3 -m xenova_gateway.authority_ops inspect
```

`FIRST_INSTALL`은 디렉터리 부재/빈 디렉터리라는 관측이다. 그 자체가 최초 설치 승인이나
출력 허가는 아니다. `VALID`는 private marker와 현재 원본의 schema·일관성을 읽었다는 뜻이다.
`BLOCKED`는 손상·권한·마운트·동시 변경 등으로 근거를 확정하지 못한 상태다. 남은 임시 파일도
최초 빈 상태로 취급하지 않으며 FIFO·symlink·공개 파일을 원본/marker로 받지 않는다.
`--candidate /root/backup.json`은 선택적인 백업 내용 비교다. 백업이 정상 JSON이거나 같은
generation이라는 사실만으로 최신성을 인정하지 않으며 원본을 백업으로 교체하지 않는다.

현재 원본이 정상이고, 운영자가 기존 Gateway 실행을 종료한 뒤 동일 원본의 내구성을
다시 확인하려면 아래 명령을 사용한다. SHA는 앞선 진단의 실제 `record_sha256`으로 바꾼다.

```bash
python3 -m xenova_gateway.authority_ops revalidate --execute \
  --expected-sha256 '진단에서 확인한 64자리 record_sha256'
```

도구는 Gateway와 같은 단일 lease를 확보한 뒤 원본 해시를 다시 대조한다. 달라졌거나
Gateway가 실행 중이면 거절한다. generation·owner·mode·원 요청·결과·pending·기한은
보존하고 `rearm_required=true`로 내구 재기록한다. 저장 실패는 미확정이며 성공으로
반환하지 않는다. 이 도구는 출력 자원을 열지 않으며 다음 Gateway 실행도 현재 신원·
준비·명시적 재무장을 다시 확인한다. 원본 손상/유실의 백업 복원은 지원하지 않는다.
marker까지 포함한 최신성 근거 없이 파일 삭제·과거 원본 복사·generation 초기화로 수습하지 않는다.

## 차량 한계와 출력 실패

`--vehicle-limits` 또는 `XENOVA_GATEWAY_VEHICLE_LIMITS`는 root 소유·0600의 JSON 파일이다.
serial 출력에서는 필수이며, mock에서는 통제된 시험을 위해 선택할 수 있다. 검증은
장치를 열기 전에 수행한다. 실제 차량 값은 차량 담당자가 측정·확정하며 코드에 기본값을 넣지 않는다.

| 필드 | 단위·조건 |
|---|---|
| `schema_version` | `1.0-drive-output-limits` |
| `min_v_mps`, `max_v_mps` | m/s. 하한 < 상한, 중립 0 포함 |
| `min_steer_rad`, `max_steer_rad` | rad. 하한 < 상한, 중립 0 포함 |
| `max_accel_mps2` | 양수 m/s². 연속 제출 목표 속도의 변화 한계 |
| `max_steer_rate_rad_s` | 양수 rad/s. 연속 제출 목표 조향의 변화 한계 |

모든 수치는 유한해야 하고 시리얼 i16 표현 범위에도 들어야 한다. 요청값과 양자화된
출력값을 모두 검사한다. 변화율은 dom0의 같은 단조 시계에서 마지막으로 완전히 제출한
목표를 기준으로 하며 경과 시간은 기존 명령 신선도 100ms까지만 인정한다. 긴 공백으로
큰 목표 변화가 허용되지 않는다. 시작 속도/조향 목표는 중립이고 STOP 제출은 속도 목표만
0으로 바꾼다. 이 값은 실제 차량 속도·조향 측정이 아니다.

한계 위반·불완전 쓰기·출력 실패는 후속 DRIVE를 차단한다. verified/warm은 같은 처리
차례에서 실패 journal의 fsync보다 먼저 STOP도 시도한다. STOP에는 완만한 변화율 제한을 적용하지 않는다. STOP도 실패할 수
있으며 최종 정지는 기존 장치 watchdog/E-stop이 담당한다. 출력 경로를 수습해 Gateway를
다시 시작하고 명시적으로 재무장하기 전 DRIVE를 재개하지 않는다. 장치 ACK·실제 정지
확인은 별도 미연결 경계다. MQTT 공개 메시지나 필드는 이 검사 때문에 추가하지 않는다.

## warm의 사전 준비와 조건부 커밋

warm actor의 `submit_transition`은 로컬 연결부다. 운영 HTTP/MQTT 전환 API가 아니다.
같은 원 요청 UUID와 예상 owner/generation·대상 VM·모드·절대 deadline을 보존한다.
deadline은 같은 dom0 host boot의 단조 밀리초다. 외부 장치 시각을 전달하지 않는다.

```python
reply = runner.submit_transition(
    'prepare', request_id, mode='AUTO', target_vm=target_vm,
    expected_owner=current_owner, expected_generation=current_generation,
    deadline_ms=original_deadline_ms,
)
```

반환 queue는 제출 통로다. 큐 등록을 내구 수락으로 표시하지 않는다. actor가 처리한
`ok=true, record.phase=PREPARING`을 받아야 원본 저장을 확인한 것이다. `refresh`는 현재
근거를 원 요청에 채택하고 `commit`은 조건부 권위 전환, `cancel`은 미커밋 준비 취소,
`get`은 원 기록 조회다. `get`과 동일 요청 재전달은 실행·기한 갱신을 하지 않는다.
응답을 잃으면 같은 ID/인자를 보존하고 원 기록을 확인한다. caller의 기다림 종료는
actor 취소가 아니다. 처리 queue는 최대 8건, 판정 회차당 한 건이며 포화는 제출 실패다.

Gateway는 기존 Authority JSON에 원 요청·고정 spec·준비 결과·커밋 generation을 함께
저장한다. 준비 중 정상 owner의 권한은 유지한다. 커밋에서 이전 명령 자격을 끝내고,
대상의 새 generation 계산·신선한 명령을 확인한 뒤 출력한다. `COMMITTED`는 권위 변경이며
앱 적용/장치 적용 완료가 아니다. 같은 요청의 반복 commit은 원 결과만 반환한다.
커밋 후 취소 응답도 `COMMITTED`를 보존한다. 이후 정지는 기존 정지 의도로 요청한다.
저장 효과 미확정이면 출력 금지이며 옛 generation으로 되돌리지 않는다.

프로세스 재시작은 미커밋 준비를 ABANDONED로 보존하고 기존 출력은 재무장 전 금지한다.
옛 승인·응답을 새 전환에 쓰지 않으며 커밋 결과와 원 key는 유지한다. 내구 기록은 최대
32건·전체 JSON 64KiB이며 자동 삭제하지 않는다. 한도에서는 새 계획 요청을 거절하고
기존 기록/정상 운행을 보존한다. 기록을 지워 차단을 해제하는 운영 절차는 제공하지 않는다.
fsync 비용은 actor 지연에 포함되며 큐/틱 설정으로 worst-case를 보장하지 않는다.

현재 계획 인계의 실제 연결은 root 바인딩에 `app_state_models`로 명시한 stateless 앱이다.
root rejoin 설정은 고정한 실제 firmware/adapter의 선언과 일치해야 한다. 선언 누락이나
stateful 앱은 일반 QUALIFIED를 요청별 상태 적용 확인으로 바꿔 쓰지 않고 계획 인계를
거절한다. stateful의 로컬 어댑터 검증은 구현돼 있으나 실제 사본 전송·요청별 적용 보고는
후속 생산자 연결이다. 현재 stateless 자격은 지속 관측을 원 요청에 채택한 것이며,
게스트가 준비 요청을 ACK했다고 보고하지 않는다.

계획 AUTO는 이미 AUTO인 상태의 동일 모드 인계에 사용한다. MANUAL·AUTO_READY에서
AUTO로 복귀할 때는 위의 명시적 재개·준비 유지·카운트다운 경로를 사용하며 계획
커밋으로 우회할 수 없다. 이미 확정된 요청의 replay는 현재 모드가 달라도 원 결과를
그대로 반환하고 새 제어를 발급하지 않는다.

계획 MANUAL은 [세션 입력 계약](../../../docs/contracts/mqtt-contract-v1.md#71-명시적-세션-입력-manual-session-1)을
요구한다. 같은 MANUAL 승계와 AUTO→MANUAL의 이동 조건은 별도다. 이 경로를 커밋하면
세션 계약 선택을 영구 원본에 남기며 이후 원 세션의 확인이 없을 때 keys-only로 하향하지
않는다. 현재 Spring은 새 표본 생산을 하지 않으므로 그 상태에서 성공으로 표시하지 않는다.
준비·커밋·MANUAL 소비의 소스/로컬 수락과 실제 장치·제품 종단 수락은 구분한다.

## 수신·출력 진단

### 2026-11-01 부가 진단의 실행 경계

실행 진입점은 Gateway logger에 `GatewayDiagnostics` 하나를 소유한다. INFO/WARNING은
128개 queue에 표준 QueueHandler의 사본으로 전달한다. 일반 메시지는 4096문자에서
자른 뒤 제어문자를 escape한다(최대 16384문자). 포화/종료 뒤 제출은 기다리지 않고
drop 계수를 올린다. mutable 인자나 traceback frame을 queue에 남기지 않는다.
기존 StreamHandler는 별도 잠금의 사본을 쓰고 ENOSPC 등 sink 실패를 같은 stderr에
다시 출력하지 않는다. 권위/원 전환/receipt/필수 사건/시리얼 명령은 이 queue에 없다.

`snapshot()`의 submitted/dropped/truncated/failed, 첫·최근 sink의 cause_type/errno,
pending/worker_alive/closing은 내부 점검용이다. 새로운 HTTP/제어 API가 아니다.
`close()`는 기존 handler를 복구하고 최대 0.25초 join한다. 막힌 worker는 강제 중단하거나
재시작하지 않으며 worker_alive=true는 종료 미확정이다. handler가 회복한 뒤 실제 종료를
확인한다. 임의 외부 handler/OS I/O나 Python 종료 전체의 지연 상한을 보장하지 않는다.

ERROR/CRITICAL와 exc_info는 queue 포화·자르기·반복 억제 대상에서 제외하고 기존 sink로
직접 전달한다. 최근 critical 원문/stack은 객체의 last_critical에도 보존한다. fatal 출력은
진입점의 자원 종료 뒤 수행한다. SerialSink는 첫 write 오류의 cause_type/errno와 원
SinkError의 cause를 남긴 후 즉시 반환하여 actor가 출력 철회/STOP을 먼저 시도한다.
장치 적용 ACK나 물리 정지 완료라는 뜻은 아니다.

정상 오프라인은 예외를 만들지 않고 aux_offline으로 센다. 실제 publish 오류는
transport(type/errno/event/message)와 내부 결함의 첫 원인·최근 16개 사본을 구별한다.
동일 type/message/위치의 연속 반복만 stack 재형식화를 생략한다. nested cause는 생략하지
않으며 원 frame을 보관하지 않는다. 사본 한도 초과·반복은 별도 계수다. 상세 사본은 private
진단이고 원 payload·credential을 일반 warning에 넣지 않는다. 드문 full stack은 크기를
임의로 잘라 원인을 없애지 않으므로 모든 결함의 메모리 바이트 상한을 주장하지 않는다.

MEASURE 형식과 INFO 기본 수준은 유지한다. 비활성 INFO에서는 필드 join/형식화를
하지 않는다. `tools/measure_failover.py`가 MEASURE를 소비하므로 수준·누락 계수와
포화 여부를 함께 기록한다. 포화 측정에서 누락된 행을 정상 측정값으로 채우지 않는다.
큐/invalid 반복 경고는 누적 횟수 1,2,4,8…에 요약하며 rejected_commands/dropped_queue_full을
계속 센다. 실행 주기·필수 출력·오류 코드·공개 거절 사유는 바꾸지 않는다.

수신 판정은 매 회 최대 64건 또는 1ms 처리 뒤 진행한다. 명령은 허용된 실행/현재
세대/VM/앱의 최신 네 칸으로 분리한다. 큐 상한이나 tick 5ms는 전체 최악 지연 보장이 아니다.
필수 원본 fsync·serial I/O·호스트 스케줄링 비용은 따로 측정해야 한다.

control은 연결된 상태에서만 제출하고 ticket으로 PUBACK을 관측한다. 같은 제어는
같은 generation/문맥으로 최대 세 번 제출하며 앱 준비가 적용 증거다. 연결 단절 뒤
Paho가 이미 받은 QoS1 bytes가 취소됐다고 가정하지 않는다. 구독/현재 실행/세대/준비
검사가 계속 적용된다. 부가 이벤트 발행 오류가 주행 판정 루프를 종료시키지 않는다.

`submissions`의 제한된 기록은 원 명령의 VM·execution·boot·instance·app·message_id·seq,
수신 단조 시각, generation, 인코딩 hex/counter, serial_result/accepted_bytes를 제공한다.
시리얼 실패는 OUTPUT_PATH_FAILED와 출력 금지로 반영된다. 장치 적용은 미연결이므로
`device_applied=null`이다. `vehicle.status.applied_generation`을 만들어내지 않는다.
STOP 재전달과 기존 watchdog/E-stop은 유지한다.

## Legacy 분리 앱

옛 분리 앱 시험은 `--profile legacy`로 명시적으로 선택한다. 공통 A/B 옵션과 함께
쓸 수 없고 신뢰된 실행·새 준비 수락을 대신하지 않는다. 아래 토픽·모드·시험 도구는
이 프로필의 설명이다. dom0에서는 두 프로필 모두 같은 전역 소유 잠금을 사용한다.

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
