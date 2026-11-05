# Xenova Telemetry Publisher Contract 2.1

이 문서는 Zephyr Publisher의 현재 발행 계약이다. 정식 필드 구조는
[schema](schemas/message.schema.json), 지표의 이름·단위·label은
[catalog](schemas/metric-catalog.json)에 정의한다. 구현·시험·ARM·이미지·실보드·제품
소비자의 수락은 각각 구분한다. 특정 후보 결과는 검증 보고서에 남긴다.

## 1. 범위와 책임

Diagnostics의 실제 관측을 직접 netfront/TCP MQTT로 발행한다. CPU·선정한 메모리,
기본 이름·실행 펌웨어·유효한 UTC를 제공하되, 앱 준비·출력 권한·복구 허가는 판단하지
않는다. `app_health_asserted=false`, v1 heartbeat/readiness와 v2 Publisher의 구분을 유지한다.

이 계약은 네트워크 수신률을 기존 패킷·오류 지표와 함께 발행한다. RX/TX 바이트 누적값은
[MQTT-03](../../../../../docs/contracts/mqtt-contract-v1.md#31-zephyr의-공통-네트워크-지표-mqtt-03)으로
발행한다. 송신률, CPU 원시 누계, 내부 수집 비용, 실행별 오류/fatal 보존 자료는 로컬에서 확인한다.

## 2. 버전과 지원 구성

현재 Publisher는 payload `2.1`과 catalog `xenova-telemetry-core-2.1`을 발행한다.
수신률·heap 계상 합계·앱 메모리 지표와 상세 stack의 `max_used_bytes`는 2.1의 선택 확장이다. 새 schema/catalog는
기존 2.1 표본도 수락한다. 이전 목록만 허용하는 폐쇄형 검사기는 목록을 갱신해야 한다.
토픽 namespace는 `xenova/v2`를 유지한다. 2.0 소비자가 2.1을 처리한다고 가정하지 않으며
자동 downgrade·v1 혼용·이중 발행을 하지 않는다. 독립 검사기는 명시적 버전으로 분기한다.

[2.0 원문](versions/2.0/CONTRACT.md)·schema/catalog와 기존 합성 fixture는 보존한다.
`--self-test`의 기존 Golden과 실제 C/native 발행 시험을 같은 근거로 취급하지 않는다.
Spring·DB·화면의 2.1 지원은 별도 소비자 연결 작업이다.

주대상은 고정 Zephyr 4.4의 xenvm UP/1-vCPU 구성이다. Network Starter, 공통 drive,
기존 autonomous/manual 빌드에 연결한다. SMP의 합산 CPU 계측을 새로 지원하지 않는다.
`CONFIG_XENOVA_DIAGNOSTICS_CPU=n`에서는 CPU capability가 unsupported이며 값은 null이다.

## 3. 버전·토폴로지·OS 중립성

### 3.1 별도 v2 namespace

```text
xenova/v2/vms/{vm_id}/telemetry/{inventory|heartbeat|metrics|events|snapshot}
```

- 신규 봉투 `schema_version="2.1"`, `type="telemetry.<stream>"`를 사용한다.
- MQTT **프로토콜**은 3.1.1을 사용한다. payload의 버전 2.1은 MQTT 프로토콜 버전이 아니다.
- v1 주행·Agent 토픽의 기존 역할을 유지한다. Publisher의 공통 RX/TX 누적값은 v1 MQTT-03에서만 발행한다.
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
| metrics | telemetry.metrics | 0 | false | loop progress 1초, heap/net/app memory 2초, timing·publisher 5초 | 의미·단위·상태가 있는 수치·분포 |
| events | telemetry.events | 1 | false | 부팅/오류/수집 이상 사건 | 발생 사실·원 rc·이력 유실. best-effort RAM 전송 |
| snapshot | telemetry.snapshot | 1 | false | 로컬 명시 요청, 기본 자동 스캔 없음 | 스레드 상세·최근 이력. 제한된 페이지 |

CPU와 앱 메모리 페이지는 같은 유한 전송 예산을 공정하게 나눠 쓴다. 앱 region 전송 순서는 고정 시작점이 아니며, 만료한 표본은 재생하지 않고 다음 새 표본에서 미전달 region부터 이어간다. nominal 주기는 모든 페이지의 도착 보장이 아니므로 소비자는 region별 원 관측 시각과 미수신을 보존한다.

위 주기는 첫 참조 구현의 운영 기본값이지 안전 timeout이 아니다. 설정값과 실제 적용 주기를 inventory에 싣고, 조정 시 시험 입력에 남긴다. 앱 heartbeat 또는 Gateway watchdog의 기존 주기를 이 값으로 바꾸지 않는다.

`telemetry.heartbeat`와 `app.heartbeat`는 다른 신호다. Telemetry thread만 살아 있어도 전자는 나올 수 있다. 후자의 기존 의미를 몰래 변경하지 않는다. 계산 진행은 `loop.completed_total`과 `loop.last_completed_age_us`가 원래 작업에서 갱신되었는지로 관찰한다.

## 5. 공통 봉투

완전한 구조는 `schemas/message.schema.json`이 기준이다.

| 필드 | 타입 | 필수 의미 |
|---|---|---|
| schema_version | string | `2.1` |
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

CPU는 `window.end_uptime_ms`, memory_regions는 `observed_uptime_ms`를 기준으로
추가 확인한다. cache를 새 collection에 복사해도 원 관측의 2초 상한은 연장되지 않는다.

수신 이후의 **진단 표시용** 기본 lease는 heartbeat 6초, loop progress 3초, heap/network 6초, timing/publisher 15초다. 보고 주기를 조정하면 소비자 lease도 별도 계약으로 함께 조정한다. 이는 VM 정상·안전 인계 근거가 아니다. 중복·역전·retained delivery가 lease를 갱신해서는 안 된다. **네트워크 내 지연은 송신·수신 단조 시계만으로 알 수 없다.** UTC 오차와 운송 지연 상한을 검증하지 않았다면 `receipt_lease_valid`와 `source_age_at_send`까지만 말하고, 실제 end-to-end freshness는 미확인으로 둔다. 안전 제어는 별도 계약을 따른다.

처음 보는 boot/publisher ID를 도착 순서만으로 현재 물리 실행으로 확정하지 않는다. 실행 권한·건강·복구 판단에 필요한 현재 문맥 검증은 별도 계약을 따른다. 다만 상단 RAM 카드는 §9.6에 따라 **마지막으로 받은 유효한 메모리 표본**을 최근 관측으로 표시할 수 있다. 이 표시에는 외부 host/execution/receipt 검증을 요구하지 않는다. 새 boot/publisher는 별도 관측 세션과 그래프로 나누고, 이미 퇴역한 것으로 아는 옛 세션의 늦은 메시지가 최근 관측을 덮거나 lease를 갱신하지 못하게 한다.

### 6.1 UTC 품질의 인정·갱신·만료

`synchronized`는 아래 검사를 통과한 UTC 대응이 전송 시점에도 유효하다는 뜻이다.
네트워크 연결이나 timer의 active 상태만으로 인정하지 않는다.

| 단계 | 현재 조건 |
|---|---|
| dom0 원천 | ARM64의 `arch_sys_counter` 프로필. 읽기 전용 `adjtimex` 성공, 반환 상태가 TIME_ERROR(5)가 아니고 STA_UNSYNC·STA_CLOCKERR 비트가 없음. `maxerror` 0~10,000,000µs |
| 실행 결합 | 기존 Store session에서 VM link와 현재 host_boot_id·execution_id·요청 nonce 대조. 다른 실행의 응답은 사용하지 않음 |
| guest 수락 | guest 요청→수신 RTT 0~5,000ms, 원천 오차 0~10,000ms, drift 0~1,000ppm, UTC 2020~2100년, 비어 있지 않은 source(최대31 bytes) |
| 진행 | worker가 200ms마다 최대25ms의 Store 처리 예산으로 확인. 새 요청 10초마다, 응답 대기 최대5초. 이 값은 VM 관리 deadline이 아님 |
| 수명 | 수신 guest uptime부터 30초 미만. guest 시각 역전·잘못된 실행·Store 오류·응답 timeout·범위 오류·허용 오차 밖 시각 점프는 무효. 새 정상 대응으로 재획득 |

불확실성은 다음 항목을 합산한다. 모든 올림은 음수가 아닌 값의 상향 반올림이다.

```text
host_source_error_ms = ceil(adjtimex.maxerror_us / 1000)
                     + ceil(host 원천 읽기 전체 경과_ns / 1,000,000)
drift_ppm = ceil(abs(adjtimex.tolerance) / 65536)
uncertainty_ms(sent) = host_source_error_ms + guest 전체 RTT_ms + 1ms
                     + ceil((sent - guest_response_received_ms) * drift_ppm / 1,000,000)
```

1ms는 양자화 여유다. 전체 RTT를 더하며 왕복 대칭이나 RTT/2를 가정하지 않는다.
host·guest 단조 시계의 epoch는 직접 빼지 않는다. 고정 xenvm과 dom0의 architectural
counter 진행률 및 kernel tolerance의 drift 상한 적용을 전제로 한 진단용 오차 예산이다.
실제 Xen descheduling·IRQ 제외 지연·시계 보정 아래의 보장 오차는 실보드 수락 대상이다.

응답 대기 중에는 기존 대응의 남은 lease를 사용하지만, 5초 응답 기한이 지나면 무효다.
공급 단절은 lease를 연장하지 않는다. 무효 상태에서는 `sent_at=null`,
`utc_quality=unavailable`, source·uncertainty도 null이다. 점프 거절 후 새 정상 대응을
받으면 기준을 다시 잡는다. `sent_at`은 MQTT 전송 시점 UTC이며 사건의 원 uptime을
덮어쓰지 않는다. UTC 공급·인터넷/NTP는 VM 시작·앱 계산·복구의 선행조건이 아니다.

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

### 8.1 이름·빌드·실행 산출물

실행 준비 시 검증한 immutable bundle의 zephyr.bin hash, manifest hash, build_id와
인스턴스 이름을 loaded_artifact에 기록한다. 제출 직전 guard에서도 자산을 검증한다.
provisioning은 해당 host/execution/incarnation의 기록만 사용한다. 실행 중 디스크 파일을
새로 해시하여 현재 실행 해시로 대체하지 않는다. 옛 실행에 loader 기록이 없으면 hash는 null이다.

build_id는 링크 전 소스(미커밋 포함)·설정·고정 compiler·Zephyr revision/patch의 digest다.
최종 zephyr.bin SHA-256은 완성 후 manifest를 검증하고 별도로 주입한다. 자기참조 hash가 아니다.
같은 A/B 번들에 VM별 신원·이름·비밀을 넣지 않는다. 이것은 검증된 로딩 기록의 연결이며 원격 증명이 아니다.

display_name은 실행 준비 당시 플랫폼 표시명, hostname은 게스트 network hostname API로
적용하고 읽어 확인한 값이다. Bootstrap은 canonical UTF-8과 제한된 길이를 사용한다.
지원되지 않는 Unicode ASCII escape 주입은 거절하며 값을 문자 그대로 잘못 보고하지 않는다.
이름은 이 실행의 receipt에 고정된다. 이름 변경 기능이 생기면 명시적 재주입/새 inventory
정책을 추가해야 하며 현재 임의 live rename을 수행하지 않는다.

### 8.2 UTC 대응의 수명

인정 조건·오차 계산·갱신·만료·재획득은 [§6.1](#61-utc-품질의-인정갱신만료)을 따른다.
inventory의 지원 선언과 현재 전송 봉투의 UTC 품질은 별개다.

## 9. Metrics와 타이밍 통계

### 9.1 분할된 작은 family

| Family | 내용 | 현재 Core와의 관계 |
|---|---|---|
| memory | kernel heap used/free/high-water/accounted | kernel system heap 범위 |
| cpu | 시스템·스레드별 구간 사용률 | guest-accounted UP 범위, 별도 window |
| memory_regions | RX/TX 고정 data pool used/free/capacity | payload 영역만 포함 |
| network | raw32 tx/rx/drop/pool/protocol, if up/carrier, RX byte rate | 기존 provider와 Diagnostics cache 재사용 |
| loop_progress | 완료·in-flight·age·deadline miss·skipped·간격·signed jitter | **실제 hook 등록 시만** 제공 |
| loop_timing | interval / abs jitter / lateness / elapsed 분포 각 1개 메시지 | Core 누적 통계 그대로 |
| publisher | 수집/전송/유실/크기/ACK 오류 | 새 publisher가 직접 생산 |

한 metrics 패킷은 한 family를 다룬다. 전체 samples를 매번 1초에 모두 싣지 않는다. 없는 family를 수신했다고 다른 family의 오래된 값까지 최신으로 갱신하지 않는다. 여러 metric을 같은 Core snapshot에서 읽었다면 sample_id로 묶을 수 있으나, 원자 동시 수집이라고 표현하지 않는다.

### 9.2 Metric catalog

정확한 이름·unit·kind·encoding·labels·원천은 catalog를 따른다. kernel heap과 RX/TX data pool은 정적 RAM 안의 서로 다른 allocator 범위이며, 전체 VM used/free RAM으로 합산하지 않는다.

`network.netfront.*`는 **raw_counter 32bit**다. snapshot 필드가 64bit라고 wrap을 복원한 누적값이 되지는 않는다. 여러 wrap을 배제할 시간/최대 발생률의 근거가 없으면 modulo 차분도 신뢰할 rate로 제시하지 않는다. 재부팅·reset은 반드시 별도 epoch에서 비교한다.

`rx_bytes_per_s`는 같은 ring 세대의 인접 유효 표본에서
`floor(Δbytes × 1000 / Δuptime_ms)`를 계산한 값이다. v1 누적값과 같은 Diagnostics 캐시를
쓰며 `interface`와 십진 문자열 `counter_epoch`를 붙인다. 첫 표본이나 읽기 실패 뒤에는
수신률이 미측정이다. ring 재초기화·역전은 `counter_discontinuity`로,
10초를 넘는 표본 간격·만료된 관측은 음수 `source_rc`가 붙은 `collection_failed`로 보낸다.
포화나 산술 범위 초과도 null과 원 `source_rc`를 유지한다. 실제 무트래픽 수신률은 `0`이다.
`counter_epoch="0"`은 epoch를 관측하지 못한 미측정 표본에만 쓴다.

`memory.kernel_heap.accounted_bytes`는 한 allocator 관측에서 `used_bytes + free_bytes`다.
고정 heap 예약 크기나 VM RAM 사용량이 아니며, allocator 메타데이터는 계상 범위 밖일 수 있다.
할당 RAM·버퍼 풀·스택은 각자의 범위로 표시한다. 전송은 `bytes`/`bytes/s`를 사용하고,
화면에서 heap·pool은 KiB, VM 할당량은 MiB, 수신률은 KiB/s로 환산할 수 있다.

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

### 9.3 CPU 구간과 스레드 수명

`system = 100 × Δbusy / Δtotal`, `thread = 100 × Δthread / Δtotal`이다.
Zephyr `k_thread_runtime_stats_cpu_get(0)`의 total_cycles는 non-idle, execution_cycles는
non-idle+idle이다. 잘린 스레드 목록을 시스템 분모로 합산하지 않는다. IRQ는 별도 계정이
아니며 해당 계측기가 귀속한 current thread 시간에 포함될 수 있다. Xen이 guest를 실행하지
않는 동안 architecture counter가 진행하면 그 시간도 guest 계상에 포함될 수 있다.
Xen pCPU/vCPU 사용률, 순수 계산 비용 또는 실시간 기한 보장이 아니다.

`float64` value는 유한 JSON number이며 0.001 percent 단위로 half-up 반올림한다.
곱셈 overflow를 피하는 정수 계산을 사용한다. 범위 오류를 0..100으로 clamp하지 않는다.
CPU 내부 원천 차분은 32비트다. 관측 공백이 `2^32 / frequency / 2` 이상이거나
reset/역전이 발견되면 기존 `counter_discontinuity`와 null을 내보내고 기준을 다시 잡는다.
첫 표본·분모 0은 no_sample이다. Publisher 재시작은 계측기의 reset이 아니다.

`payload.window`의 sample_seq는 guest boot 범위의 공통 수집 표본이다.
start/end는 두 CPU 원천 표본 사이의 구간이며 이번 `collection.start/end`와 다르다.
scope는 `guest_accounted_up`, part_index/count는 고정 표본을 나눈 페이지다.
페이지당 system 1개와 thread 최대4개, 총32개다. 동일 표본·구간을 유지하며
전송 중 새 값으로 바꾸지 않는다. 전체 source 행 수와 truncation을 표시한다.
한 페이지가 빠지면 전체 thread 관측 완료로 해석하지 않는다.

thread_id는 guest boot 내 수명 ID다. 이름·행 번호·주소를 ID로 쓰지 않는다.
thread_name은 최대31 UTF-8 bytes이고 name_truncated로 잘림을 표시한다.
생성 hook과 전체 monitor 목록으로 수명을 관리한다. 같은 주소·같은 이름의 새 스레드는
다른 ID이며 registry(64개)가 소진되면 명시적으로 잘림을 보고한다. raw pointer는 발행하지 않는다.
기존 상세 stack snapshot의 `snapshot_local` row_id는 이 ID로 재해석하지 않는다.

### 9.4 수집 소유와 현재 상태

Diagnostics의 단일 system-workqueue 작업이 200ms마다 CPU·pool·네트워크 상세를 수집한다.
로컬 조회와 Publisher는 cache를 읽으며 차분 기준을 변경하지 않는다. cache가 1초 이상
갱신되지 않으면 정상값의 시각만 갱신하지 않고 불연속/실패를 표시한다.
CPU 조회는 stack fill-pattern scan을 호출하지 않는다. stack은 기존 명시적 요청만 수행한다.

pool은 실제 `rx_bufs`/`tx_bufs`의 가용 block 수와 `CONFIG_NET_BUF_DATA_SIZE`로 계산한다.
descriptor·정렬 padding·관리 비용·정적 ring/grant 페이지는 이 용량에 포함하지 않는다.
일부 영역 실패는 다른 정상 영역의 값을 지우지 않는다.

`collection_failures_total`은 누계다. `publisher_state`는 최근 필수 수집 결과로 running/degraded를
전이하며 다음 정상 수집에서 복귀한다. 선택적 상세 stack 미요청·미지원은 degraded 사유가 아니다.
stopping은 종료 요청 상태이며 실제 worker join은 별도 API 결과로 확인한다.

### 9.5 앱 메모리와 펌웨어 예약 범위

`family="app_memory"`는 실제 drive 앱의 고정 버퍼를 읽는다. `lattice_memory.c`의 단일
provider가 world/planner와 연결된 MANUAL 소유자의 작은 metadata를 Diagnostics의 기존 200ms cache에 연결한다.
내부 `app_count`는 실제 원천이 있는 4개 또는 8개다. MANUAL 원천이 없는 기존 firmware·시험에서
그 region을 0으로 채워 추가하지 않는다. 이 내부 개수는 새 wire 필드나 호출자 선택값이 아니다.
지도·경로 본문을 복사하거나 탐색 배열을 훑지 않으며, 소유자 lock은 기다리지 않는다.
검색 노드 수는 검색 소유자가 완료한 slice 경계에서 공개한다. 이 수집은 앱 준비·진행·출력 허가를 만들지 않는다.

| `labels.region` | `used_bytes` / `capacity_bytes`의 범위 | `reserved_bytes`의 범위 |
|---|---|---|
| `drive_maps` | current·writer·reader가 보유한 완전 지도 payload 슬롯 / 전체 지도 payload 슬롯 | 지도 pool 구조체와 metadata |
| `drive_paths` | current·writer·reader가 보유한 경로 payload 슬롯 / 전체 경로 payload 슬롯 | 경로 슬롯 배열과 metadata |
| `drive_search_nodes` | 마지막 완료 slice의 실제 노드 / 선택 프로필의 `node_limit × sizeof(node)` | 전체 search 구조체. index·open heap·heuristic workspace도 포함 |
| `drive_odometry` | ring에 보존한 차량 표본 / ring 표본 용량 | odometry ring 구조체와 metadata |
| `manual_input` | 보관된 `shared.input`의 입력 길이 / 수신 payload 상한 | 해당 `xd_received` 구조체 |
| `manual_pending` | 실제 대기 소유가 있으면 `sizeof(drive_job)`, 없으면 0 / 그 슬롯 크기 | MANUAL pending 슬롯 |
| `manual_running` | callback이 실제 소유 중이면 `sizeof(drive_job)`, 없으면 0 / 그 슬롯 크기 | MANUAL running 슬롯 |
| `manual_result` | 완료 사본이 보존되면 `sizeof(drive_completion)`, 없으면 0 / 그 슬롯 크기 | MANUAL result 슬롯 |

MANUAL 범위는 위 네 저장소다. main의 completed·scratch, I/O working 사본, runtime 사본,
MQTT cache·기록 배열·thread stack을 합친 전체 앱 RAM이 아니다. 입력의 null 종단 문자는
payload capacity에 포함하지 않는다. pending/running/result의 슬롯 크기는 제어·입력 사본과
해당 구조체 metadata를 포함하므로 `buffer.content_bytes`와 다를 수 있다.

각 region에서 아래 여덟 지표를 전송한다. 첫 여섯 값은 `bytes`·`gauge`,
두 누계는 `count`·`raw_counter`이며 모두 `u64_decimal` 문자열이다. 원 카운터 폭은 catalog를 따른다.

| 지표 접미사 (`memory.app.*`) | 의미 |
|---|---|
| `reserved_bytes` | 이미 확보된 정적 저장 공간. 아래 payload 용량과 사용분을 포함 |
| `used_bytes`, `capacity_bytes`, `free_bytes` | 같은 region의 논리 점유·한도·여유. 유효 표본에서는 `used + free = capacity` |
| `high_water_bytes`, `min_free_bytes` | 현재 firmware boot 중 최고 점유와 `capacity - high_water`. 현재 사용량·현재 여유와 구분 |
| `capacity_failure_total` | 실제 슬롯/노드 한도 확보 실패 누계. 잠금 경합이나 신원 counter 소진을 포함하지 않음 |
| `contention_total` | 제품 소유자의 실제 업무 lock 획득 실패 누계. 진단 getter/probe 자신의 BUSY는 제외. 단일 소유 search에서는 0 |

`manual_pending`, `manual_running`, `manual_result`의 contention은 같은 MANUAL worker 교환 경계에서
발생한 offer/read 업무 경합 counter를 반복해서 보여준다. 서로 독립된 세 누계가 아니므로 합산하지 않는다.
`manual_input`은 기존 업무 `xd_io_snapshot()`의 `atomic_t` 32-bit counter를 비트 패턴 그대로 u64 decimal로
노출한다. wire가 u64 문자열이라는 이유로 64-bit 연속 누계로 해석하지 않는다. `2^32-1 → 0`처럼
감소하면 불연속으로 처리하고 차분 기준을 새로 잡는다. catalog의 `raw_bits_by_region`이 이 예외를 명시한다.
이 원천 폭 예외는 `manual_input`의 `contention_total`에만 적용하며 나머지 region/누계는 기존 폭을 유지한다.

반환·AUTO 재시작은 논리 점유를 줄일 수 있지만 정적 RAM을 OS에 반환했다는 뜻이 아니다.
최고 점유와 확보 실패는 같은 firmware boot 안에서 보존한다. 오래된 점유와 새 예약량을
합치지 않고 같은 region·표본의 값을 사용한다. 검색 index·heap·heuristic의 현재 채움 정도를
노드 점유에 더하지 않는다.

기존 metrics 토픽에서 CPU와 한 frozen 표본을 공유하며, 제공된 region마다 한 메시지를 시도한다.
새 source cycle의 목표 간격은 기존 2초다. byte 예산이 부족하면 모든 4개/8개 region이 그 간격 안에
도착한다고 보장하지 않는다. CPU/app의 시도 우선순위를 교대하고, 만료 뒤에도 다음 미전송 cursor를
새 원 표본에서 이어 간다. 원 시각을 갱신하거나 서로 다른 CPU window를 완전한 하나로 합치지 않는다.
CPU 또는 앱 메모리의 유효한 페이지가 남아 있는 동안 그 표본을 바꾸지 않는다. 별도 전체 관측 복사본은 두지 않는다.
`source_sample_seq`, `observed_uptime_ms`, `collection.sample_id`와 VM·boot·publisher 문맥을 보존한다.
이는 상세 snapshot의 `part` 프로토콜이 아니다. 소비자는 metric+region별로 갱신하고 누락을 0으로 채우지 않는다.
기존 byte token·I/O 예산과 원 표본의 2초 전송 만료를 유지하며, 재전송 시 시각을 새로 붙이지 않는다.

소유자 lock을 얻지 못하면 그 region의 값은 `null`, `status="collection_failed"`,
`reason="owner_busy"`, `source_rc=-EBUSY`다. 오래된 cache는 `reason="sample_expired"`와
원 실패를 보존한다. 실제 공간 부족 누계와 수집 실패를 합치지 않으며 다음 정상 수집에서 다시 유효값을 보낸다.
provider가 없는 firmware에서 이 family가 오지 않는 것을 사용량 0으로 해석하지 않는다.


#### MANUAL buffer metadata

기존 여덟 숫자 지표의 이름·자료형·단위는 유지한다. MANUAL 네 region은 같은 `payload`에
`buffer`를 반드시 포함하고, AUTO 네 region에는 이 필드를 넣지 않는다. 소유자 관측이 BUSY·만료 등으로
실패하면 여덟 값과 `buffer` 모두 null이다. 입력 자체가 잘못됐거나 만료됐어도 소유자 관측이 성공하면
숫자 지표는 `status="ok"`이며 원 입력/계산의 사유는 아래 metadata에 남는다.

| 필드 | 형식과 의미 |
|---|---|
| `phase` | `empty/stored/pending/running/retained`. 각 소유 region에 맞는 단계 또는 empty |
| `content_bytes` | u64 decimal. input은 보관 입력 길이, pending/running은 control+input span 길이, result는 보관 completion 크기 |
| `sequence` | empty이면 null. 나머지는 원 입력 envelope 또는 job의 u64 decimal sequence. 입력 seq `"0"`도 유효하며 null로 바꾸지 않음 |
| `source_uptime_ms` | 원 입력 수신 시각. empty·음수/미확인 원 시각은 null, 알려진 0은 `"0"`. 재수집·재전송 시각으로 바꾸지 않음 |
| `owner_lifetime` | worker 소유 수명 u64 decimal. 미확인/0이면 null. VM execution·CPU thread ID와 다른 범위 |
| `context_revision/connection_revision` | 소유자가 보존한 u64 decimal revision. 현재 권한 검사를 대신하지 않음 |
| `input_status` | `missing/unclassified/accepted/invalid/expired/context_changed`. 저장됨·분류 전·무효·만료를 구분 |
| `source_rc` | 0 또는 음수인 원 입력/계산 결과 코드. 숫자 지표의 관측 `source_rc`와 다른 경계이며, 원 시각 미확인을 이 값으로 덮지 않음 |
| `performed` | result만 boolean, 나머지는 null. callback 수행 사실이며 계산 성공·현재 출력 유효성이 아님 |
| `revoked` | 원 소유 작업의 철회 관측 boolean. 실제 callback 종료·메모리 해제를 뜻하지 않음 |
| `last_read_sequence` | result만 마지막 읽은 작업 sequence. 미독/0·다른 region은 null. 읽기·MQTT 수신·장치 적용을 동일시하지 않음 |

저장된 입력이 만료돼도 그 bytes를 보관하면 `manual_input.used_bytes`는 줄지 않는다.
pending을 running으로 넘긴 뒤에는 서로 다른 슬롯 점유로 표시하며, 취소 요청만으로 실행 중인 슬롯을
비웠다고 하지 않는다. result를 읽어도 retained 사본은 그대로 점유한다. 유효성/철회와 물리·논리 점유를
구분하고, 이 metadata로 READY·복구 허가·출력권을 만들어내지 않는다.

MANUAL peak는 수집 주기 사이의 짧은 소유 전이도 보존한다. 현재 점유는 release/join/restart에 따라
줄 수 있지만 boot의 peak·누계는 worker 재시작으로 지우지 않는다. 최신 pending으로 교체한 횟수,
invalid 입력, callback 오류를 공간 부족 누계로 합치지 않는다.

외부 봉투는 계속 2.1·같은 metrics 토픽·QoS0·retain=false다. 기존 AUTO 4-region 벡터와 2.0 원본은
보존한다. MANUAL을 처리하는 strict 소비자는 추가 region과 `buffer`를 이번 schema/oracle로 갱신해야 한다.
일반 호출자에게 내부 DTO 크기나 저장 형식 선택을 요구하지 않는다. 이번 새 fixture는 실제 C codec에
합성 소유 metadata를 넣은 경계 시험이며 실제 MANUAL owner·ARM·보드 수락의 대체 근거가 아니다.
U64 경계 fixture는 2933B이며, 모든 발행에는 기존 4096B 한도를 적용한다.

`family="memory"`에는 다음 선택 지표도 있다. 이들은 서로 더해서 전체 VM 사용량을 만들 수 없다.

| 지표 | 정확한 원천과 범위 |
|---|---|
| `memory.firmware.ram_region_bytes` | xenvm 빌드의 `CONFIG_SRAM_SIZE × 1024`. 실제 Xen 할당 RAM 관측이 아님 |
| `memory.firmware.mapped_bytes` | 최종 ELF의 `z_mapped_start..z_mapped_end`. 코드·데이터·고정 풀·스택·정렬을 포함한 firmware 매핑 범위 |
| `memory.firmware.static_data_bytes` | `_image_ram_start.._image_ram_end`. 위 mapped 범위의 부분집합이며 heap·풀·스택 예약을 이미 포함 |
| `memory.page_allocator.free_bytes` | 고정 Zephyr의 `k_mem_free_get()`이 반환한 MMU page allocator의 free physical frames |

`family="memory"`의 선택 필드 `source_sample_seq`와 `observed_uptime_ms`는 둘 다 있거나 둘 다 없다.
새 발행자는 이 쌍으로 firmware/page allocator의 Diagnostics cache 표본을 가리킨다.
같은 메시지의 직접 kernel heap 관측 시점은 `collection.start/end_uptime_ms`다.
같은 봉투라는 이유로 heap의 수집 시각을 cached page allocator의 새 관측 시각으로 사용하지 않는다.
과거 2.1 memory 메시지는 이 쌍이 없어도 읽되, 없던 cache 원천 시각을 추정해 채우지 않는다.

kernel heap의 내부 여유, 앱 pool의 내부 여유, page allocator의 free는 다른 관리 범위다.
스택·heap·pool 예약분도 firmware의 데이터 범위에 이미 들어 있다. 아직 전체 VM의 현재 사용량을
제공한다고 주장하지 않는다. xenvm이 아닌 구성에서 해당 원천이 없으면 `unsupported/null`이다.


### 9.6 Zephyr 관리 RAM 확보량 — 같은 payload의 파생 참조

상단 RAM 카드의 이름은 **Zephyr 관리 RAM 확보량**이다. 분모는 같은 memory 메시지의
`memory.firmware.ram_region_bytes`, 여유는 `memory.page_allocator.free_bytes`다.
고정 xenvm에서 CONFIG_SRAM_SIZE가 정의한 물리 page-frame 관리 범위 안의 비여유량을 계산한다.
이는 Xen VM의 전체 실제 할당량·RSS·동적 앱 payload 사용률이 아니다.

```text
total_bytes     = memory.firmware.ram_region_bytes
free_bytes      = memory.page_allocator.free_bytes
committed_bytes = total_bytes - free_bytes
percent         = ROUND_HALF_UP(100 * committed_bytes / total_bytes, 소수 셋째 자리)
```

참조 결과의 bytes 세 값은 정확한 u64 decimal 문자열, percent는 `"25.000"`처럼 소수 세 자리의
decimal 문자열이다. 내부 계산은 정수와 Decimal로 하고 이진 float나 JS Number를 먼저 거치지 않는다.
분모가 양수이고 전부 free인 유효한 경계는 `"0.000"`이다. 미관측·미지원·만료를 0으로 바꾸지 않는다.

같은 봉투의 total/mapped/static/page-free 네 값이 모두 관측 성공(status ok, rc0)이고,
원 `source_sample_seq/observed_uptime_ms` 쌍과 collection이 유효할 때만 계산한다.
`0 < total`, `free <= total`, `static <= mapped <= total`, `mapped + free <= total`을 검사한다.
불일치·null·원천 오류·출판 시 source-age 2초 초과에서는 파생 bytes/percent를 null로 두고 원 사유를 남긴다.
과거 2.1에 원 source 쌍이 없으면 heap의 새 collection 시각으로 대체하지 않는다.
서로 다른 메시지의 같은 이름 값, 다른 VM·boot·publisher·host/execution/receipt 문맥을 섞지 않는다.

정적 image·stack·kernel heap arena·고정 app pool은 이미 확보량 안에 들어 있다. 같은 arena 안의
heap/pool used가 바뀌어도 free physical page가 그대로이면 이 카드의 값은 그대로다.
heap free·pool free를 page free에 더하거나, app used·mapped/static을 committed에 다시 더하지 않는다.
이 카드는 예약된 RAM까지 포함한 관리 범위의 확보량이며 논리 payload 사용량은 별도 pool 카드다.

현재 플랫폼은 Zephyr create 자원 16MiB, instance/hardware profile, firmware CONFIG_SRAM_SIZE와 DTS,
생성하는 Xen cfg의 memory16을 함께 검증한다. update-resources는 정지 Linux 전용이다.
공개 get/list의 memory_mib는 등록 configuration이며 실제 Xen 메모리 관측 필드가 아니다.
외부에서 더 큰 Xen VM을 만들었다고 이 고정 firmware의 관리 범위까지 커지는 지원 계약은 없다.
따라서 실제 Xen 할당을 따로 확인하지 않은 카드에 `전체 VM 메모리 사용률`이라는 이름을 붙이지 않는다.

기존 도구의 순수 `ram_summary(message)`는 **단일 memory payload**만 받아 계산하며 I/O·Xen·Store 조회를 하지 않는다.
이 함수의 정상 결과는 출판 당시 원천 정합성이다. 상단 카드의 기본 의미는 마지막으로 받은 유효한
메모리 관측이며, 현재 물리 실행·VM 건강·복구 허가·출력 권한을 확인했다는 뜻이 아니다.

`ReferenceConsumer.ingest(message, received_ms)`와 `ram_view(vm_id, now_ms)`는 외부 `bind`나
`expected_context` 없이 이 최근 관측을 소비하는 기본 경로다. `expected_context`를 생략하거나 `None`으로
주면 독립 실행문맥 필터를 적용하지 않는다. 별도 신뢰 원천이 있는 소비자만
`ram_view(vm_id, now_ms, expected_context=...)`를 명시해 추가 대조할 수 있다. 이 선택 필터는
현재 RAM 표시의 선행조건도, 새 HTTP API·raw receipt 조회를 요구하는 계약도 아니다.
봉투에 있는 원 host/execution/receipt는 그대로 보존하며, 미확인 문맥을 새로 채우거나 현재로 승인하지 않는다.
명시적으로 `bind(vm_id, boot_id, publisher_instance_id)`를 선택한 소비자에게는 기존 boot/publisher 필터와
bind 변경 시 cache 분리를 유지한다. RAM 이외 stream의 기존 bind 요구도 이 변경으로 제거하지 않는다.

VM·boot·publisher별 관측 세션을 나누고 새 세션의 그래프를 이전 세션과 연결하지 않는다.
같은 boot/publisher에서도 새 source의 원 host/execution/receipt 문맥이 달라지면 그래프 구간을 나누고 원 문맥을 보존한다. 이는 추가 외부 조회·현재 실행 검증을 표시의 필수조건으로 요구한다는 뜻이 아니다.
세션 선택은 최근에 관측한 출처의 선택일 뿐 물리 실행 확인이 아니다. 알려진 퇴역 세션의 지연 수신으로
이전 세션을 다시 선택하지 않는다. boot A에서 B로 바뀌면 A 전체를 알려진 퇴역 boot로 취급하여,
A가 처음 보는 publisher를 붙여도 다시 선택하지 않는다. 같은 현재 boot 안의 publisher 정상 교체는 허용한다.
이 참조는 기존 순번 map을 재사용하며 새 제품 이력 저장소를 추가하지 않는다. 아직 관측하지 못한 출처의
물리적 최신성이나 무기한 모든 과거 boot의 차단을 보장하지 않는다. 최근 세션에 유효 표본이 없으면 이전 세션 숫자로 채우지 않는다.
원 `source_sample_seq/observed_uptime_ms`와 수신 시각을 함께 보존하고, 수신 후 경과에 송신 당시
원 source-age를 더한 값이 6초를 초과하면 bytes/percent를 unknown/null로 한다. 새 heap collection 시각으로
원 RAM 시각을 대체하지 않으며, 네트워크 지연까지 검증한 end-to-end freshness로 표현하지 않는다.
다른 family 수신이나 같은 source-pair를 포함한 새 heap 메시지는 RAM의 최초 수신/원천 나이를 갱신하지 않는다.
같은 source-pair라도 cache 만료로 ok에서 오류/null이 되면 새 실패와 원 source_rc를 보존하고 이전 정상 비율을 내지 않는다.
그 뒤 같은 옛 표본이 다시 도착해도 원 lease를 재발급하지 않으며, 다른 새 source 표본에서 정상 회복한다.
같은 source-pair의 정상 숫자가 상충하면 RAM_SAMPLE_CONFLICT로 비율을 보류한다.
같은 boot/publisher와 원 source 쌍에서 receipt 등 context만 바뀌어도 새 표본으로 보지 않으며,
원 payload·lease를 보존한 RAM_CONTEXT_CHANGED로 보류한다. 같은 message_id에 다른 RAM 내용이 오면
기존 ID_REUSE_CONFLICT 분류를 유지하고 해당 현재 source의 비율도 보류한다. 이미 발생한 최초 원천 실패를
뒤의 상충 사유로 덮지 않으며, 이 보류는 새로운 유효 source에서만 해제한다.
더 작은 source 순번 또는 역전된 원 시각의 표본은 현재 캐시를 덮거나 lease를 갱신하지 않는다.
같은 순번에 다른 원 시각은 새 표본이 아니라 상충이다. 실패·만료 view에서도 관측했던 source 쌍·시각·
각 원 status/rc를 남기고 파생 bytes/percent만 null로 둔다.

2026-11-04 사용자 결정으로 이전의 외부 현재 실행문맥 필수 조건을 위 최근 관측 기준으로 대체했다.
기존 실행 신원·복구·출력 권한의 검증 조건과 과거 실패 기록은 이 표시 변경으로 완화하지 않는다.

고정 SDK Zephyr revision `684c9e8f32e4373a21098559f748f06915f950c9`의
`kernel/include/mmu.h`, `kernel/mmu.c`는 CONFIG_SRAM_SIZE 기반 frame 집합과 lock 아래 free page 수를 사용한다.
같은 후보의 실제 ARM QEMU 128MiB 시험에서 free는132980736B였고, 기존 heap/pool 내부 점유를 바꿔도 그대로였다.
실제 8192B page mapping 뒤132972544B로 줄고 반환 후132980736B로 회복했다. 이는 allocator 범위 시험이며,
xenvm의16MiB 운영값 변경이나 Xen 실할당 관측이 아니다. native와 QEMU는 BOARD_XENVM 원천이 없어
실제 codec에서 firmware/page 값을 unsupported/null로 내보냈으며 이 참조도 비율을 만들지 않는다.
원 SDK source SHA·명령·로그·이 C 관측은 동일 후보 검증 기록의 managed-ram-sdk-analysis/codec 근거로 연결한다.

contract 디렉터리에서 실행하는 참조 CLI는 다음과 같다. 운영 보드나 현재 VM에 연결하지 않는다.

```sh
python3 tools/validate_contract.py --ram-summary fixtures-2.1/ram-committed.json
python3 tools/validate_contract.py --ram-summary fixtures-2.1/ram-source-expired.json
python3 tools/validate_contract.py --self-test
```

첫 명령의 결과 객체에서 표시할 값은 아래와 같다. 실제 출력에는 원 identity/source-pair/source_status도 포함된다.

```json
{"scope":"Zephyr 관리 RAM 확보량","status":"ok","reason":null,"total_bytes":"16384","committed_bytes":"4096","free_bytes":"12288","percent":"25.000"}
```

두 번째 명령은 status unknown/reason RAM_SOURCE_EXPIRED이며 bytes/percent가 모두 null이다.
CLI는 출력 가능한 ok/unknown에서 exit0, 범위·형식 invalid에서는 exit1이다. 상충하는 검증 옵션을 함께 주면
기존 argparse 오류로 거절한다. 이 참조 출력은 새 wire metric·제품 API·운영 설정이 아니다.
fixture의 16KiB·경계 숫자는 계산 시험값이며 실제 VM RAM 기본값으로 사용하지 않는다.

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

`warm-lattice-lab`은 `CONFIG_XENOVA_DIAGNOSTICS_THREADS=y`, 최대32행으로 빌드한다.
`xenova telemetry threads` 또는 기존 명시 요청 API에서만 스택을 스캔한다. 주기 CPU·pool
수집은 스택을 읽지 않는다. 스캔 한도 초과·실패·미지원에서는 두 사용/여유 필드를 `null`로 보내고
`scan_limit`·`unsupported`·원 `source_rc`를 보존한다. 고정 native POSIX 포트는 pthread 스택에서
실행하므로 Zephyr 예약 버퍼의 fill 상태를 실제 스택 사용량으로 보내지 않는다.

기본은 로컬 운영자가 상세 조회를 요청했을 때 발행한다. 이번 버전에는 MQTT 원격 명령 수신기를 추가하지 않는다. 앱 C API로 snapshot 생성·전송을 요청하는 경로와 로컬 shell은 만들 수 있으나 기존 `xenova` namespace를 활용하고 중복 명령을 늘리지 않는다.

| 항목 | 규칙 |
|---|---|
| threads | 이름/상태 문자열/raw flags/priority/stack high-water. raw thread pointer를 네트워크에 노출하지 않음 |
| thread identity | 이 상세 snapshot의 행 ID는 `snapshot_local` row_id. CPU의 수명 `thread_id`와 다르며 이름·행 번호로 결합하지 않음 |
| stack | `size_bytes`는 예약 크기, `unused_high_water_bytes`는 fill scan의 최소 여유. 선택 필드 `max_used_bytes = size - unused_high_water`는 스캔이 확인한 최대 사용량이며 현재 사용량이 아님 |
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
- LWT는 필수 기능에 넣지 않는다. 나중에 추가한다면 connection별 세션과 생성 시각의 한계를 명시하고 heartbeat/앱 정상 의미를 대신하지 않는다.

### 11.4 종료

기존 앱 quiesce·안전 출력·Xen 종료 예산이 먼저다. Publisher는 bounded flush만 시도하고 기한 초과는 로컬 오류/유실로 남긴다. 네트워크 없는 상태에서 마지막 오류를 전달하려고 종료를 무한 대기하지 않는다. graceful stop은 별도 근거이며 disconnect를 정상 종료 증명으로 간주하지 않는다.

## 12. 인증·ACL·배포

- 공통 broker의 전용 Telemetry username은 `telemetry-<canonical VM UUID>`이며 실제 vm_id에 대응시킨다.
  v1 drive username은 UUID 그대로다. 계정 규칙은 OS와 독립적이며 기존 Linux Agent v1을 바꾸지 않는다.
  게스트의 신뢰된 Store 채널이 계정 형식을 선택한다. payload가 채널이나 임의 username을 선택하지 않는다.
  username 버퍼는 접두사·UUID·NUL을 포함한 47 bytes다. VM UUID·boot/execution/receipt 식별자는 유지한다.
  과거 격리 listener의 UUID 형식 v2 입력은 새 펌웨어의 자동 fallback 대상이 아니다. 한 VM의 발행자는 자기 v2 다섯 토픽에만 쓴다. 다른 VM·주행 command·vehicle control 쓰기 금지.
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
- `fixtures/sequences/`: 기존 11개 세션/중복/순서/만료/페이지 사례. reference oracle의 외부 bind 단계는 실행 문맥을 독립 검증하는 소비 목적의 예제다. §9.6 RAM 최근 관측은 외부 bind 없이 소비하며, 기존 bind 예제를 모든 진단 표시의 필수조건으로 확대하지 않는다.
- `fixtures/manifest.json`: 각 메시지의 topic·QoS·publish retain·예상 결과.
- `.json` 예시는 읽기 좋게 들여썼다. 실제 크기 기준은 minified UTF-8 wire payload이며 검증 보고서는 그 값을 기록한다. 실제 전송한 payload 자체가 4096byte를 넘으면 whitespace 여부와 무관하게 거절한다.
- 모든 UUID·MAC·IP·빌드·timer·counter·시각은 합성 예제다. 실제 보드 로그로 인용하지 않는다.

## 16. 구현 진행 시 결정을 기다릴 필요가 없는 항목

이 문서에서 다음은 구현 기준으로 확정한다: v2 분리, 직접 Ethernet/TCP MQTT, 5 streams, decimal u64/i64, nullable UTC, current-vs-history 구분, Core 의미 보존, bounded publish, 비싼 snapshot 기본 off, subscriber 구현 비범위.

실환경에서 값을 확인해야 하는 항목은 source 재확인 후 기록한다: 실제 Zephyr/Kconfig/errno, 메모리/stack 예산, 신뢰 가능한 ID/인증정보 주입원, 활성 broker ACL, UTC source의 품질, 현재 firmware hash. 이를 모르는데 성공했다고 채우지 않는다. 존재하는 주입 계약을 재사용하고, 없으면 필요한 최소 플랫폼 인터페이스와 보안 조건을 명시한다.

**최종 목표:** Zephyr가 사실을 정확하고 재사용 가능한 형식으로 생산한다. 소비자는 그 사실을 받아 자신의 저장·판정·표시 기능을 구현한다. 기존 백엔드 제약 때문에 올바른 관측 의미를 희생하지 않는다.
