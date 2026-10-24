# Xenova Telemetry Publisher Contract 2.0

**설계일:** 2026-10-23  
**상태:** 구현용 신규 발행 계약. 현재 펌웨어·브로커·백엔드에 적용됐다는 뜻이 아니다.  
**핵심 결정:** Zephyr를 첫 참조 발행자로 만든다. 기존 백엔드의 자료형·단위·토픽 제약 때문에 관측의 의미를 축소하지 않는다. 소비자의 저장·판정·표시는 각 담당자의 책임이며, 발행자는 관측 의미·실행 구분·손실·전송의 경계를 명확하게 제공한다.

> 이 문서의 기본값·새 토픽·필드·시간 예산은 **이번 설계 결정**이다. 기존 구현으로 확인된 사실은 §2와 `docs/SOURCES.md`에 구분했다. Golden Payload는 규격 검증을 위한 **합성 데이터**이며 실보드 측정 로그가 아니다.

## 1. 범위와 비목표

| 구분 | 이번 발행자 작업 |
|---|---|
| 직접 구현 | Diagnostics Core → 데이터 모델 → 직렬화 → 인증된 MQTT 발행, 제한된 큐·재연결·손실 관측, 독립 구독 검증 |
| 재사용 | 기존 Core, netfront/공유 XenStore/협조형 종료, Zephyr MQTT 네트워크 API, 기존 봉투의 VM·부팅·시퀀스 개념 |
| 기존 시스템 보존 | `xenova/v1`의 주행 명령·앱 heartbeat·복구 판정·기존 Agent 동작 |
| 별도 책임 | Spring DB/HTTP, FastAPI, 화면, 자동 복구 판단, Gateway 제어권, ESP32 실제 적용 |
| 구현 완료로 주장하지 않을 것 | 주행 계산 정확성, 미러링 완료, 안전한 인계, ECU 적용, 실제 정지, crash 영구 보존 |
| 이번 작업 제외 | 새로운 주행 알고리즘, 원격 명령/임의 shell, 원격 스택 스캔 RPC, 고급 tracing, vchan 중계 신규 개발 |

발행자는 알고 있는 사실만 말한다. 수집 성공은 앱 정상·차량 정상·인계 가능의 선언이 아니다. 구독자가 일부 stream만 사용할 자유는 있지만, 사용하는 필드의 실행·단위·유효성 의미를 바꿀 자유는 없다.

## 2. 실제 입력 자료와 확장 경계

사용자가 올린 `README.md`와 `RESULT.md`를 Core의 현재 계약으로 사용한다. 보고서상 작업 기준은 `chore/platform/next-work`, HEAD `87c6269…`이며 커밋·push는 하지 않았다. 따라서 오래된 원격 Starter와 로컬 Core 구현을 혼동하지 않는다.

| 현재 Core가 제공하는 것 | Publisher가 보존해야 할 의미 | 이번에 필요한 추가 구현 |
|---|---|---|
| `xdiag_boot(stage, rc)`, `xdiag_error(stage, rc)` | 부팅·오류 각각 최근 8개, 현재 guest 실행의 RAM 이력 | 사건별 stable sequence, export cursor·유실 수, event 직렬화 |
| `xdiag_snapshot_get(..., include_threads)` | 부분별 rc 확인, 서로 다른 provider의 수집 구간이며 동시 원자 snapshot 아님 | collection 구간·sample ID·section 상태 매핑 |
| thread name/state/priority/stack | 스택 fill-pattern high-water 관측. CPU 사용률/호출 stack 아님 | 필요 시 bounded snapshot 발행 |
| kernel system heap | `k_malloc` 계열 heap만. 전체 VM free RAM 아님 | 범위를 이름에 담은 scalar metric |
| `xnet_stats()` + `net_if` | tx/rx/drop/error는 원래 32비트 counter. link up은 IP/MQTT ready 아님 | raw 폭·reset 범위 명시, 실제 인터페이스 inventory |
| `xdiag_loop_begin/end` | `loop_seq`는 완료 수. in-flight/마지막 완료 age를 별도 제공 | 등록한 실제 작업의 ID·epoch·측정 경계 제공 |
| 누적 timing histogram | `p95_upper_us`, `p99_upper_us`는 버킷 상한 근사 | 원 histogram과 saturation을 보존; 정확한 percentile로 재명명 금지 |
| 로컬 시계 | uptime(ms)와 cycle-derived clock(us)의 epoch가 다름 | 서로 다른 clock domain을 섞지 않는 봉투 |
| 없는 기능 | CPU runtime, 주행 상태/동기화, 영구 crash 저장, MQTT는 Core 책임이 아님 | 미지원 또는 not_registered로 표현. 값을 추측하지 않음 |

기존 백엔드는 v1 `percent/bytes`, 실제 UTC `sent_at`, 전역 message ID 저장 등을 전제로 한다. 기존 C client도 작은 출력 버퍼와 프로세스 로컬 ID를 사용한다. 이를 새 발행자 의미에 맞게 확장하되, **v1 소비자가 모르는 payload를 기존 토픽에 섞지 않는다.**

## 3. 버전·토폴로지·OS 중립성

### 3.1 별도 v2 namespace

```text
xenova/v2/vms/{vm_id}/telemetry/{inventory|heartbeat|metrics|events|snapshot}
```

- 신규 봉투 `schema_version="2.0"`, `type="telemetry.<stream>"`를 사용한다.
- MQTT **프로토콜**은 3.1.1을 사용한다. payload의 버전 2.0은 MQTT 2.0이 아니다.
- 기존 v1 주행/Agent 토픽은 변경하지 않는다. 기본 구현에 v1/v2 이중 발행을 넣지 않는다.
- 소비자 미구현은 새 발행 계약을 축소하는 이유가 아니다. 구형 수신기의 수신·저장 성공으로도 보고하지 않는다.
- 패치 개정은 문서/검증 수정만. 필드·단위·타입·의미 변경은 schema와 fixture 개정을 동반한다. 2.0 validator는 폐쇄형으로 정의한다. 알 수 없는 필드를 조용히 버리는 방식으로 호환됐다고 표시하지 않는다.
- `agent_instance_id`의 역할은 새 `publisher_instance_id`로 명확히 표현한다. Linux에서도 process/Agent 실행마다 해당 ID를 바꾸면 같은 계약을 구현할 수 있다. 모든 기존 API 필드명을 일괄 개명하는 작업은 아니다.

### 3.2 기준 전송 경로

```text
Zephyr Core → Telemetry adapter → Zephyr MQTT client
           → Xen netfront → dom0 xenbr0 → Mosquitto → 독립 시험 구독기
```

이전 문서의 vchan 중계를 이번 Publisher의 선행조건으로 삼지 않는다. 이미 개발한 가상 Ethernet을 사용한다. MQTT/TCP 설정·socket·netfront 통합은 새 펌웨어에서 실제 빌드/보드 검증이 필요하다. Core에 MQTT를 import하거나 Core를 전송 라이브러리에 종속시키지 않는다.

## 4. 발행 종류와 전송 정책

| Stream | Type | QoS | Retain | 기본 발생/보고 | 책임 |
|---|---|---:|---:|---|---|
| inventory | telemetry.inventory | 1 | true | 연결 후 새 수집, 구성 변화 시 | OS/펌웨어/버전/인터페이스/지원 기능/측정 한계 |
| heartbeat | telemetry.heartbeat | 0 | false | 2초 | Publisher 보고 경로·수집기 상태. 앱 정상 선언 금지 |
| metrics | telemetry.metrics | 0 | false | loop progress 1초, heap/net 2초, timing·publisher 5초 | 의미·단위·상태가 있는 수치·분포 |
| events | telemetry.events | 1 | false | 부팅/오류/수집 이상 사건 | 발생 사실·원 rc·이력 유실. best-effort RAM 전송 |
| snapshot | telemetry.snapshot | 1 | false | 로컬 명시 요청, 기본 자동 스캔 없음 | 스레드 상세·최근 이력. 제한된 페이지 |

위 주기는 첫 참조 구현의 운영 기본값이지 안전 timeout이 아니다. 설정값과 실제 적용 주기를 inventory에 싣고, 조정 시 시험 입력에 남긴다. 앱 heartbeat 또는 Gateway watchdog의 기존 주기를 이 값으로 바꾸지 않는다.

`telemetry.heartbeat`와 `app.heartbeat`는 다른 신호다. Telemetry thread만 살아 있어도 전자는 나올 수 있다. 후자의 기존 의미를 몰래 변경하지 않는다. 계산 진행은 `loop.completed_total`과 `loop.last_completed_age_us`가 원래 작업에서 갱신되었는지로 관찰한다.

## 5. 공통 봉투

완전한 구조는 `schemas/message.schema.json`이 기준이다.

| 필드 | 타입 | 필수 의미 |
|---|---|---|
| schema_version | string | `2.0` |
| type | string | 토픽 끝 stream과 동일한 `telemetry.*` |
| vm_id | UUID string | 실제 플랫폼 영구 VM UUID. display name/domid로 대체 금지 |
| boot_id | UUID string | **이번 guest 부팅** 식별자. 모든 guest 내 Publisher/앱이 공유 가능한 부팅 문맥 |
| publisher_instance_id | UUID string | Telemetry 실행 세션. TCP 재연결은 유지, Publisher 재시작은 교체 |
| seq | u64 십진 문자열 | Publisher 세션·stream별 1부터 증가. 재연결에 reset하지 않음 |
| message_id | string ≤160 | 아래 복합 규칙. 다른 VM/부팅/세션/stream과 충돌하지 않음 |
| context | object | 외부에서 제공받은 host 실행 문맥 또는 명시적인 unbound |
| clock | object | 전송 시점 guest uptime, 선택적 UTC·품질·출처 |
| payload.collection | object | 실제 수집 구간과 성공/실패. 전송 시각과 구별 |
| payload | stream별 object | 구조/단위/값/상태의 계약 |

```text
message_id = vm_id + "/" + boot_id + "/" + publisher_instance_id
           + "/" + stream + "/" + seq
```

이 값은 DB의 36자 UUID 컬럼에 맞추지 않는다. 소비자는 충분한 길이 또는 동등한 복합키로 저장한다. message_id는 인증 수단이 아니다.

### 5.1 ID 생성·수명

- VM UUID는 자신의 XenStore 문맥 등 검증 가능한 플랫폼 주입원에서 얻는다. 읽기에 실패하면 임의 고정 UUID로 발행하지 않는다. 로컬 진단은 계속할 수 있으나 식별 실패를 기록한다.
- boot ID는 **MQTT client마다 새로 만드는 값이 아니다.** guest 부팅 때 한 번 확정해 공유한다.
- ID는 검증된 entropy 또는 host의 실행별 주입 토큰으로 생성한다. 테스트용 고정 PRNG를 여러 A/B·재부팅에서 그대로 사용해 같은 UUID를 반복 생성하지 않는다. 고유성을 보장할 방법이 없으면 publisher identity 오류로 발행을 중단한다.
- 실제 Xen execution ID를 임의로 만들지 않는다. context의 `binding="unbound"`에서는 host_boot_id/execution_id/receipt_id 모두 null이다.
- host에서 검증하여 주입한 경우 `binding="host_provisioned"`와 세 값을 함께 전달한다. 그 문구 자체는 암호학적 증명이 아니다. 소비자가 관리/복구에 사용하려면 현재 플랫폼과 receipt를 대조한다.
- `seq`는 실제 메시지를 확정할 때 증가한다. 동일 QoS1 전송 재시도는 **같은 바이트·ID**를 재사용한다. 동일 ID에 다른 내용은 오류다. seq gap은 손실/집계일 수 있으며 연속 수신을 강제하지 않는다.
- 수집·재연결로 같은 값을 다시 읽어도 **새 관측**이라면 새 sample ID/seq다. 과거값을 새로 수집한 것처럼 timestamp만 갱신하지 않는다.

### 5.2 A/B와 같은 논리 앱

권장 최신값 키는 `(vm_id, boot_id, publisher_instance_id, metric_name, labels)`다. 토픽 하나/app_id 하나에 최신값 하나만 두면 A/B나 다른 metric이 덮일 수 있다. 동일 seq가 A와 B에서 나오는 것은 정상이다. `task_epoch`는 같은 guest 실행에서 주기 작업을 다시 초기화할 때도 바뀐다.

## 6. 시각·신선도 계약

| 요소 | 규칙 |
|---|---|
| clock.sent_uptime_ms | 현재 guest boot의 uptime. UTC/다른 장치 시간과 빼지 않음 |
| clock.sent_at | 신뢰할 UTC가 없으면 null. 1970+uptime 같은 가짜 UTC 금지 |
| utc_quality | unavailable / synchronized. synchronized는 source와 uncertainty_ms도 필요 |
| collection.start/end_uptime_ms | `start ≤ end ≤ sent`. 실제 수집 구간이며 여러 provider의 동시성은 보장하지 않음 |
| collection.elapsed_us | Core 수집 소요시간. 직렬화/전송 시간·pure hook 비용과 다름 |
| loop durations | Core의 cycle-derived local clock으로 계산된 **경과**. 시작 epoch는 uptime/UTC와 다름 |
| received_at / receiver_monotonic | 수신기가 자신의 메타데이터로 추가. 발행 payload를 몰래 덮어쓰지 않음 |

전송 직전 source age는 `sent_uptime_ms - collection.end_uptime_ms`로 확인할 수 있다. 기본 발행 대기 상한은 heartbeat/metrics 2초이며, 초과한 live 표본은 버리고 재수집한다. inventory는 재연결 시 다시 읽는다. event/history는 원래 발생 시각과 늦은 보고라는 사실을 그대로 보존한다.

수신 이후의 **진단 표시용** 기본 lease는 heartbeat 6초, loop progress 3초, heap/network 6초, timing/publisher 15초다. 보고 주기를 조정하면 소비자 lease도 별도 계약으로 함께 조정한다. 이는 VM 정상·안전 인계 근거가 아니다. 중복·역전·retained delivery가 lease를 갱신해서는 안 된다. **네트워크 내 지연은 송신·수신 단조 시계만으로 알 수 없다.** UTC 오차와 운송 지연 상한을 검증하지 않았다면 `receipt_lease_valid`와 `source_age_at_send`까지만 말하고, 실제 end-to-end freshness는 미확인으로 둔다. 안전 제어는 별도 계약을 따른다.

처음 보는 boot/publisher ID를 도착 순서만으로 현재 실행으로 승격하지 않는다. 확인되지 않은 세션은 진단용 후보/과거 자료로 둘 수 있다. 현재 세션을 외부 실행 문맥으로 확정한 뒤, 퇴역 세션의 늦은 메시지가 현재 화면을 덮지 못하도록 한다.

## 7. 자료형·오류·미지원

### 7.1 숫자와 단위

- u64 카운터·seq·microsecond elapsed는 `"18446744073709551615"` 같은 십진 문자열. 부호 있는 jitter는 i64 십진 문자열. 0 앞자리·`+`·지수표기를 쓰지 않는다.
- 단순 우선순위·행 번호·페이지 번호 등 작은 정수는 JSON integer. 비율만 유한 JSON number. NaN/Infinity/중복 JSON key 금지.
- 단위는 `bytes`, `count`, `packets`, `us`, `ms`, `percent`, `bool`를 명확히 구분한다. 패킷 수를 bytes로, duration을 percent로 바꾸지 않는다.
- value에 다른 의미를 얹지 않는다. string으로 보냈다고 문자열 정렬해서 counter 순서를 비교하지 않는다.
- metric catalog에 없는 이름·unit·kind 조합은 계약 확장 전 발행하지 않는다. metric 의미를 labels에 임의의 JSON로 숨기지 않는다.

### 7.2 수집 상태

| status | value | 의미 |
|---|---|---|
| ok | 유효한 값, rc=0 | 이번 source에서 수집됨. 건강 판정 아님 |
| unsupported | null | 빌드/API/현재 adapter가 제공하지 않음 |
| not_registered | null | 실제 provider/관측할 task hook이 없음 |
| not_requested | null | 비싼 조회를 요청하지 않음 |
| no_sample | null | 기능은 있으나 아직 완료·간격 표본이 없음 |
| collection_failed | null | 실제 수집 실패. 원래 음수 rc와 이유 보존 |
| scan_limit | null | 스택 스캔 제한으로 사용량 미수집 |
| saturated | null 또는 분포별 명시적 null | 수치의 정확성을 보장할 수 없는 포화 |
| counter_discontinuity | null | 연속 누적값/차분의 의미를 증명할 수 없음 |

원래 rc가 있으면 숫자를 보존한다. capability 검사로 애초에 API를 호출하지 않았다면 source_rc=null을 허용하며 reason을 싣는다. OS마다 errno 번호가 같다고 가정하지 않는다. Golden 오류 숫자는 합성값이다.

**중요한 예외:** Core의 `truncated/-ENOSPC`는 반환된 thread 행까지 무효라는 뜻이 아니다. snapshot 전체가 잘렸음을 표시하면서 반환행은 유지한다. 개별 stack에 `scan_limit`가 있으면 그 stack unused만 null로 한다. 마지막 정상값을 유지하려면 소비자가 `last_good`에 따로 보관하고 현재값처럼 표시하지 않는다.

## 8. Inventory와 capability

inventory는 OS·kernel/firmware·publisher 버전, 실제 interfaces, 계측 지원, clock domain, 크기/주기 제한을 알린다. `hostname` 또는 display name을 얻지 못하면 null이다. Linux 필수값을 맞추려고 Zephyr에 가짜 hostname/PID를 만들지 않는다.

- 실제 image hash를 읽지 못하면 `artifact_sha256=null`. build ID와 artifact hash를 혼동하지 않는다.
- `measurement_profile`은 network_starter / diagnostic_exercise / driving_app / linux_agent 중 실제 용도를 명시한다. 산술 시험 작업을 driving_app이라고 표시하지 않는다.
- capability는 available / unsupported / not_registered다. available은 현재 수집 성공이나 업무 정상과 다르다.
- interfaces/capabilities 상한 초과 시 각각 omitted 개수를 명시한다. 큰 목록 때문에 전체 JSON을 잘라 전송하지 않는다.
- retained inventory는 마지막 알려진 구성일 뿐 생존 증거가 아니다. guest 삭제·보관과 retained 정리는 관리 운영 책임으로 인계한다.

## 9. Metrics와 타이밍 통계

### 9.1 분할된 작은 family

| Family | 내용 | 현재 Core와의 관계 |
|---|---|---|
| memory | kernel heap used/free/high-water, CPU 미지원 표시 | heap은 제공, CPU runtime은 미제공 |
| network | raw32 tx/rx/drop/pool/protocol, if up/carrier | 기존 provider 재사용 |
| loop_progress | 완료·in-flight·age·deadline miss·skipped·간격·signed jitter | **실제 hook 등록 시만** 제공 |
| loop_timing | interval / abs jitter / lateness / elapsed 분포 각 1개 메시지 | Core 누적 통계 그대로 |
| publisher | 수집/전송/유실/크기/ACK 오류 | 새 publisher가 직접 생산 |

한 metrics 패킷은 한 family를 다룬다. 전체 samples를 매번 1초에 모두 싣지 않는다. 없는 family를 수신했다고 다른 family의 오래된 값까지 최신으로 갱신하지 않는다. 여러 metric을 같은 Core snapshot에서 읽었다면 sample_id로 묶을 수 있으나, 원자 동시 수집이라고 표현하지 않는다.

### 9.2 Metric catalog

정확한 이름·unit·kind·encoding·labels·원천은 `schemas/metric-catalog.json`의 24개 정의를 따른다. 필수 label 이름과 의미를 재사용한다. 현재 스레드별 CPU 비율은 unsupported다. kernel heap free를 총 VM free RAM으로 바꾸지 않는다.

`network.netfront.*`는 **raw_counter 32bit**다. snapshot 필드가 64bit라고 wrap을 복원한 누적값이 되지는 않는다. 여러 wrap을 배제할 시간/최대 발생률의 근거가 없으면 modulo 차분도 신뢰할 rate로 제시하지 않는다. 재부팅·reset은 반드시 별도 epoch에서 비교한다.

### 9.3 루프 완료와 마감

- `loop.completed_total` = 성공적인 end에 의해 확정된 완료 횟수. 통신 주기·publish 횟수·begin 횟수 아님.
- `loop.in_flight=true`, 완료 수 불변, 완료 age 증가를 함께 제공하면 계산 정체의 근거를 만들 수 있다. Publisher가 임의로 카운터를 진행시키지 않는다.
- 아직 끝나지 않은 무한 대기는 완료 기준 deadline_miss에 안 잡힐 수 있다. 그 counter가 증가하지 않는다는 사실만으로 정상 판정 금지.
- `step_elapsed`에는 IRQ·선점·Xen descheduling이 포함될 수 있다. pure CPU time으로 해석하지 않는다.
- 실제 앱의 release schedule과 Core 예정 release가 같아야 한다. 기존 앱의 `now + period` 재동기화와 Core의 고정 origin을 설명 없이 혼합하지 않는다. 계측 연결 변경과 스케줄링 정책 변경은 별도 검토·시험한다.

### 9.4 분포

32개 누적 버킷: `0`, `1`, `2..3`, `4..7`, …, `[2^29,2^30-1]`, 마지막 `[2^30,∞)` 마이크로초.

- `scope=task_epoch_cumulative`. 5초마다 보내도 최근 5초 구간 통계라는 뜻이 아니다.
- `count`, `min_us`, `max_us`, `sum_us`와 histogram을 보존한다.
- percentile은 ceil(p*N) 번째 표본이 속한 버킷의 상한인 `p95_upper_us`, `p99_upper_us`다. 정확한 p95/p99로 재명명 금지.
- count=0: extrema/sum/percentile은 null. 0이라는 실제 표본이 있었다는 식으로 표시하지 않는다.
- 열린 마지막 버킷에 quantile이 속하면 상한은 null. 포화 플래그가 있으면 해당 불확실성을 보존하고 이 버전에서는 percentile을 null로 둔다.
- 누적 보고를 반복해서 더하거나, 구간별 p99를 평균해 전체 p99를 만들지 않는다. 안전한 차분이 필요한 소비자는 같은 task_epoch의 원 histogram과 포화/손실을 검증한다.
- 다른 VM/작업의 histogram을 무조건 합쳐 의미 없는 전체 실시간성 수치로 만들지 않는다.

## 10. 사건과 상세 snapshot

### 10.1 Events

사건 event ID는 `vm_id/boot_id/event_seq`다. envelope message ID와 다르다. 같은 사건을 재연결 뒤 다시 보고하면 envelope ID는 새로 만들 수 있어도 event ID와 발생 시각은 보존한다.

Core가 현재 stable event_seq를 노출하지 않으면 최소한의 adapter/core 보완으로 **발생 시점**에 부여한다. stage+rc+timestamp만으로 다른 사건을 같은 사건이라 추정하지 않는다. public README에 없는 C member 이름은 코드 확인 없이 단정하지 않는다.

- category: boot / error / publisher. severity는 진단용이며 차량 안전 등급이 아니다.
- stage 최대 **31 UTF-8 bytes**(Core 32bytes의 NUL 제외). 문자 수가 아니다.
- source_total, source_retained, missed_before_capture, publisher_dropped_total을 구별한다.
- history는 current guest boot RAM 범위다. 끊긴 동안/부팅 전/치명 장애 직전에 유실될 수 있다. 오류 목록이 비어 있다는 이유로 crash가 없었다고 말하지 않는다.
- 원본 fatal handler·UART/PV 로그를 대체하지 않는다. 무조건 fatal 경로에 MQTT publish를 호출하지 않는다.

### 10.2 Snapshot

기본은 로컬 운영자가 상세 조회를 요청했을 때 발행한다. 이번 버전에는 MQTT 원격 명령 수신기를 추가하지 않는다. 앱 C API로 snapshot 생성·전송을 요청하는 경로와 로컬 shell은 만들 수 있으나 기존 `xenova` namespace를 활용하고 중복 명령을 늘리지 않는다.

| 항목 | 규칙 |
|---|---|
| threads | 이름/상태 문자열/raw flags/priority/stack high-water. raw thread pointer를 네트워크에 노출하지 않음 |
| thread identity | 현재 Core에는 장수명 안정 ID가 보장되지 않으므로 `snapshot_local` row_id. 이름만으로 다른 snapshot과 동일 스레드라고 단정하지 않음 |
| stack | high-water의 미사용 바이트. 현재 SP·호출 stack·스케줄링 trace가 아님 |
| history | 현재 RAM에 남은 사건과 source/export 유실 수 |
| source_status | ok/truncated/unsupported/not_requested/collection_failed. 의미 보존 |

**페이지 규칙:** 한 번 freeze한 source snapshot을 최대8개 part로 나눈다. 각 part 최대4 thread rows 또는8 event rows, 전체 wire message ≤4096bytes. snapshot_id·collection·part count·source completeness는 모두 일치해야 한다. 부분별로 다시 스캔해서 한 snapshot이라고 묶지 않는다. 처음 part 수신부터 30초 안에 전부 오지 않으면 `incomplete_transport`로 표시한다. 전송 part가 모두 와도 source_status=truncated면 원본 전체 관측 완료가 아니다. 스캔/수집하지 못한 전체 행 수는 null로 두고, history의 coverage도 수집 자체가 실패했다면 null로 둔다. 빈 배열은 반환한 행이 없다는 뜻일 뿐, 원본에 오류나 스레드가 없다는 뜻이 아니다. 서로 다른 snapshot·세션의 page를 섞지 않는다.

스레드 스캔이 interrupt latency를 만들 수 있다는 Core 한계를 지킨다. 자동 상세 스캔은 꺼두며, 승인된 비주행/시험 창에서만 수행한다. 낮은 우선순위 thread라고 interrupt-disabled scan의 영향까지 사라지지 않는다. 이미 freeze된 snapshot 전송도 expiry가 지나면 폐기하고 incomplete로 남긴다.

## 11. 버퍼·동시성·재연결·종료

### 11.1 첫 참조 구현의 제한

| 자원 | 초기 계약/상한 |
|---|---|
| wire application payload | 모든 봉투 포함 UTF-8 **4096bytes**. MQTT framing/topic 길이는 추가로 계산 |
| latest metrics | family별 bounded slot. 이전 live 표본을 덮을 때 coalesced/lost 계수 기록 |
| event RAM queue | 기본32, 최대64. 포화 시 oldest drop 및 누적 손실 기록. 무한 대기 금지 |
| QoS1 in-flight | 기본1, 최대4. 실제 제약은 inventory/빌드 영수증에 기록 |
| 재연결 backoff | 1→2→4→8→10초 상한, sleep은 주행 thread에서 하지 않음 |
| 대역폭 | 초기 진단 기본 예산16KiB/s, burst8KiB. 값은 측정·승인 후 조정 가능하며 안전 기한 아님 |
| snapshot | 한 번에1개, 최대8parts/32rows, 수신 조립기 역시 bounded |
| MQTT keepalive | 초기30초. 앱/루프 장애 검출 deadline이 아님 |

기존 C client의 1280byte 출력 버퍼를 그대로 두고 4096byte 지원이라고 선언하지 않는다. 봉투·JSON token·MQTT TX/RX·payload 분할까지 검증한다. Telemetry 인스턴스의 버퍼를 확장하되 기존 50Hz 명령의 제한을 임의로 넓히지 않는다. JSON을 자르거나 부분 string을 정상 packet으로 보내지 않는다.

### 11.2 스레드 소유권

Core begin/end는 기존처럼 allocation·I/O·로그 없이 경량으로 유지한다. Publisher는 일반 thread에서 snapshot을 읽는다. 한 MQTT client의 init/poll/publish/subscribe/latest는 **한 소유 thread**만 호출한다. 별도 Telemetry 연결을 두면 주행 MQTT 객체와 버퍼를 공유하지 않는다. 공용 라이브러리의 정적 parser scratch까지 reentrant인지 확인한다. 서로 다른 client 객체라는 이유만으로 thread-safe라고 단정하지 않는다.

이번 단계에서 실제 주행 스레드 구조를 일괄 재설계하지 않는다. 보고 thread가 살아 있어도 계산 완료가 멈추었다면 그대로 발행한다. 보고 정상 신호를 만드는 대신 계산을 재시작하거나 loop counter를 대신 증가시키지 않는다.

### 11.3 재연결

- MQTT CleanSession=1, 주기 live 데이터는 offline backlog 없음. client 라이브러리 내부 queue도 직접 점검한다. CleanSession만으로 응용 큐가 자동 비워진다고 가정하지 않는다.
- heartbeat/metrics는 재연결 후 **새 수집**만 발행한다. 누락 구간은 seq gap·loss로 남긴다.
- inventory는 현재 상태를 다시 읽어 발행한다. retain은 생존 증거가 아니다.
- event는 bounded RAM queue에서 원 ID·발생시각을 보존해 역사로 다시 보고할 수 있다. 장치 reboot를 넘는 보존은 제공하지 않는다.
- 같은 연결의 QoS1 retry는 같은 wire bytes/ID. 새 연결에서 재봉투화한 사건은 동일 event ID, 새 message ID로 dedup 가능해야 한다.
- ACK는 broker 단계의 프로토콜 사실이다. 특히 MQTT 3.1.1의 권한 거부는 항상 음수 ACK로 표시되지 않는다. **ACL·실제 전달 수락은 독립 구독기의 수신으로 검증**한다.
- LWT는 2.0 필수 기능에 넣지 않는다. 나중에 추가한다면 connection별 세션과 생성 시각의 한계를 명시하고 heartbeat/앱 정상 의미를 대신하지 않는다.

### 11.4 종료

기존 앱 quiesce·안전 출력·Xen 종료 예산이 먼저다. Publisher는 bounded flush만 시도하고 기한 초과는 로컬 오류/유실로 남긴다. 네트워크 없는 상태에서 마지막 오류를 전달하려고 종료를 무한 대기하지 않는다. graceful stop은 별도 근거이며 disconnect를 정상 종료 증명으로 간주하지 않는다.

## 12. 인증·ACL·배포

- 전용 Telemetry username을 실제 vm_id에 대응시킨다. 한 VM의 발행자는 자기 v2 다섯 토픽에만 쓴다. 다른 VM·주행 command·vehicle control 쓰기 금지.
- MQTT Client ID는 `xt2-<하이픈을 제거한 full VM UUID>`를 기본으로 한다. 한 VM의 system Telemetry 발행자 하나라는 운영 계약이다. 기존 주행 연결과 Client ID 충돌하지 않아야 한다. 새 publisher가 이전 실행과 중복 동작하지 않도록 host 실행·guest singleton을 확인한다.
- password/인증서는 공유 불변 펌웨어·소스·Golden·로그에 넣지 않는다. 인스턴스별 제한된 주입원에서 받는다. 안전한 제공 경로가 없으면 insecure mode로 자동 fallback하지 않고 BLOCKED로 보고한다.
- 인증된 네트워크 사용을 기본으로 하며, 신뢰되지 않는 네트워크로 나가면 TLS와 서버 검증이 필요하다. 내부 xenbr0 평문 시험을 수행하면 그 보안 한계를 시험 영수증에 기록한다. 평문 패스워드를 안전하다고 표시하지 않는다.
- ACL 예시는 `docs/ACL.example`이며 실제 파일 덮어쓰기 지시가 아니다. subscriber는 읽기 권한만 받는다.
- 최종 이미지/기본 A/B 배포/실보드 affinity 변경은 별도 승인한다. 이번 문서나 fixture 실행은 어떤 원격·보드 변경도 하지 않는다.

## 13. 소비자 최소 의무와 자유

소비자가 선택하는 것: 저장소·차트·경보·장애 판정 정책·검색 UI·어떤 stream을 사용할지.

소비자가 바꾸면 안 되는 것: 세션 범위, 단위, null/오류 의미, retained의 역사성, counter 폭/reset, percentile 근사성, 메모리 범위, 수집시각/전송시각, 앱 정상/차량 적용으로의 과대 해석.

구형 백엔드에 대한 인계 목록은 `docs/CONSUMER_HANDOFF.md`에 분리했다. 이 작업으로 백엔드 코드를 대신 수정하지 않는다. JSON Schema는 구조를 검사하며, `tools/validate_contract.py`는 교차 필드와 순서/손실 사례를 검사한다. 이 도구는 운영 복구 엔진이 아니다.

## 14. Publisher 수락 기준

| 단계 | 통과 근거 | 완료로 확대하지 않을 것 |
|---|---|---|
| 계약 | JSON Schema, metric catalog, 정상/거절/순서 사례 모두 검증 | 펌웨어 구현 완료 |
| host/native_sim | 실제 producer adapter가 낸 payload를 독립 parser로 검사, 단절·용량·오류 주입 | RPi4 timing·Xen runtime 수락 |
| 실제 broker | 실제 ACL로 A/B 수신, 타 VM 쓰기 거절, reconnect·dup·truncation·timestamp 확인 | Spring 저장·화면 정상 |
| 실보드 | netfront/TCP/MQTT→독립 구독, Core 로컬값 대조, 실제 timer/heap/thread/오류/복구 문맥 | 실제 주행·안전 인계 수락 |
| 실제 앱 연결 | 계산 완료 hook·입력 처리·publish 결과, reporter만 생존하는 정체 시험 | 논리 계산 정확성·ECU 적용·미러링 구현 |
| 성능 | 계측/발행 on-off, 동일 firmware/input 조건, 스레드 스캔 없이 jitter·누수 측정 | hard realtime/캐시·메모리 격리 보장 |

필수 시험은 C-01~C-15로 Codex 인계서에 정리했다. 현재 포함된 Golden 결과는 **계약 파일 자체의 offline 검증**까지만 의미한다.

## 15. Golden Payload 사용

- `fixtures/valid/`: 30개 합성 wire message.
- `fixtures/invalid/`: 30개 의도적 거절 사례. 원래 관측값의 유효한 `collection_failed` 메시지와 **규약 위반 메시지**를 혼동하지 않는다.
- `fixtures/sequences/`: 11개 세션/중복/순서/만료/페이지 사례. reference oracle의 외부 bind 단계는 실제 제품에서 플랫폼 문맥 검증에 해당한다.
- `fixtures/manifest.json`: 각 메시지의 topic·QoS·publish retain·예상 결과.
- `.json` 예시는 읽기 좋게 들여썼다. 실제 크기 기준은 minified UTF-8 wire payload이며 검증 보고서는 그 값을 기록한다. 실제 전송한 payload 자체가 4096byte를 넘으면 whitespace 여부와 무관하게 거절한다.
- 모든 UUID·MAC·IP·빌드·timer·counter·시각은 합성 예제다. 실제 보드 로그로 인용하지 않는다.

## 16. 구현 진행 시 결정을 기다릴 필요가 없는 항목

이 문서에서 다음은 구현 기준으로 확정한다: v2 분리, 직접 Ethernet/TCP MQTT, 5 streams, decimal u64/i64, nullable UTC, current-vs-history 구분, Core 의미 보존, bounded publish, 비싼 snapshot 기본 off, subscriber 구현 비범위.

실환경에서 값을 확인해야 하는 항목은 source 재확인 후 기록한다: 실제 Zephyr/Kconfig/errno, 메모리/stack 예산, 신뢰 가능한 ID/인증정보 주입원, 활성 broker ACL, UTC source의 품질, 현재 firmware hash. 이를 모르는데 성공했다고 채우지 않는다. 존재하는 주입 계약을 재사용하고, 없으면 필요한 최소 플랫폼 인터페이스와 보안 조건을 명시한다.

**최종 목표:** Zephyr가 사실을 정확하고 재사용 가능한 형식으로 생산한다. 소비자는 그 사실을 받아 자신의 저장·판정·표시 기능을 구현한다. 기존 백엔드 제약 때문에 올바른 관측 의미를 희생하지 않는다.
