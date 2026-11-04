# 소비자 인계 — 저장·판정·화면 구현은 각 담당자 책임

## Spring 연결

플랫폼은 v1 공통 RX/TX 누적값과 v2 2.1 상세 지표를 발행한다. 로컬 수락은 실제 C 발행기에서
격리 Mosquitto와 독립 구독기까지다. Spring 저장·REST/WebSocket·화면은 아래 조건으로 연결한다.

- `AgentMessageHandler`의 기존 v1 `/metrics` 구독을 사용한다. 등록된 VM의 종류로 Zephyr 표본을
  `zephyr_telemetry`에 연결하고, `AgentDataService`에서 메트릭 전용 저장 경로를 사용한다.
  Linux 프로세스 스냅샷은 해당 Linux 프로세스 표본으로 갱신한다.
- Zephyr MQTT-03의 `sent_at=null`은 수신 시각으로 조회하고, 속도 계산에는 실제
  `observed_uptime_ms` 차이를 쓴다. RX/TX 누적 문자열은 정확히 저장한 뒤 차분한다.
  기존 `vm_metric_samples`의 JSON에 보존할 수 있다. 새 메트릭 테이블은 필요하지 않다.
- `MetricFilter`의 REST·WebSocket 출처 필터에 `zephyr_telemetry`를 포함한다. 네트워크 표본을
  VM·boot·인터페이스·카운터 세대별로 묶는다. 초기·리셋·실패는 미측정으로, 유효한 무트래픽은 0으로 표시한다.

[v1 wire 형식과 상태](../../../../../../docs/contracts/mqtt-contract-v1.md#31-zephyr의-공통-네트워크-지표-mqtt-03)

## 앱 메모리와 스택 연결

스택 snapshot의 MQTT 발행과 계약 검증은 유지한다. 사용자 결정으로 스택 화면 적용은 보류한다.
아래 스택 표시 조건은 향후 연결 기준이며 이번 화면 구현 지시가 아니다.

새 후보는 기존 v2.1 metrics 토픽에 `family="app_memory"`를 제공한다.
커널 힙이 0이어도 지도·경로·탐색용 정적 버퍼는 사용할 수 있다. 앱 메모리 화면은
`memory.app.*`를 소비하고 커널 힙·VM 할당 RAM과 별도 범위로 표시한다.
[정식 의미와 보존 범위](../CONTRACT.md#95-앱-메모리와-펌웨어-예약-범위)를 따른다.

| 화면 범위 | 사용할 지표·식별자 | 표시·갱신 조건 |
|---|---|---|
| 지도·경로·탐색·odom | `memory.app.used_bytes/free_bytes/capacity_bytes`, `labels.region` | 네 region을 구분. 슬롯/노드/ring 점유이며 전체 RAM 사용률로 재명명하지 않음 |
| MANUAL input/pending/running/result | 같은 `memory.app.*`와 각 메시지의 `payload.buffer` | 점유·보관과 입력 유효성/원 작업 상태를 구분. result를 읽어도 비워진 것으로 표시하지 않음 |
| 예약과 최고 점유 | `reserved_bytes/high_water_bytes/min_free_bytes` | 예약은 used를 포함. 현재 점유와 boot 중 최고 점유를 별도 표시 |
| 확보 실패·경합 | `capacity_failure_total/contention_total` | boot 범위 원 누계. 공간 부족과 lock busy를 합치지 않음 |
| 스택 스냅샷 | `stack.size_bytes/unused_high_water_bytes/max_used_bytes` | 같은 VM·boot·publisher·snapshot의 완전한 부분들만 표시. 최대 사용량은 현재 사용량이 아님 |
| 펌웨어·page allocator | `memory.firmware.*`, `memory.page_allocator.free_bytes` | mapped 안에 static_data와 스택·풀·힙 예약이 포함됨. 합산해 전체 VM used를 만들지 않음 |

기존 `region`은 `drive_maps`, `drive_paths`, `drive_search_nodes`, `drive_odometry`이며,
MANUAL 원천이 연결된 후보는 `manual_input`, `manual_pending`, `manual_running`, `manual_result`를 추가한다.
앱 메모리는 CPU와 공유하는 한 frozen 원 표본에서 region마다 메시지를 시도한다. 고정 byte 예산에서
모든 region의 2초 수신을 약속하지 않으며, 원 2초 source TTL과 만료 뒤 미전송 cursor를 유지한다.
상세 snapshot의 part와 다르므로 한 region 수신을 전체 region의 갱신으로 취급하지 않는다. VM·boot·publisher와
`source_sample_seq/observed_uptime_ms`를 보존하고, 이전 실행·이전 firmware의 값을 새 실행에 붙이지 않는다.


MANUAL region은 `buffer`가 필수이고 AUTO에는 없다. owner BUSY·표본 만료에서는 숫자 여덟 값과
buffer가 모두 null이다. 정상 관측된 무효/만료 입력은 숫자 `status="ok"`와 보관 점유를 유지하며
`buffer.input_status/source_rc`로 원 입력 사유를 보존한다. `status="ok"`를 입력 정상·READY로 해석하지 않는다.
입력 원문·control body·credential은 buffer에 포함하지 않는다.

buffer의 phase/content_bytes/원 sequence·source_uptime_ms/owner_lifetime/context_revision/
connection_revision/input_status/source_rc/performed/revoked/last_read_sequence를 schema대로 보존한다.
empty의 sequence/time은 null, 실제 입력 seq·수신 시각 0은 `"0"`, 음수 원 수신 시각은 null이다.
result의 performed는 계산 성공이 아니며 last_read_sequence는 전송·장치 ACK가 아니다.
MANUAL 수신 schema/DTO가 새 region·buffer를 거절하거나 버리지 않는지 확인하되, 지원하지 않은 화면을
정상 0이나 현재 출력 허가로 채우지 않는다. [필드별 원본](../CONTRACT.md#manual-buffer-metadata)을 따른다.

MANUAL pending/running/result의 `contention_total`은 동일한 실제 worker 교환 업무의 offer/read counter다.
세 region 값을 합쳐 경합 건수를 만들지 않는다. 진단 getter의 BUSY는 이 counter에 더하지 않는다.
manual_input의 값은 기존 업무 `xd_io_snapshot()`에서 온 32-bit wrapping counter를 u64 decimal로 옮긴 것이다.
`raw_bits_by_region.manual_input=32`를 따르고 감소 시 불연속 처리한다. 64-bit 연속 누계로 가정하거나
수신기에서 임의로 wrap을 보정하지 않는다.

측정 범위는 I/O의 지정된 input cache와 MANUAL worker의 pending/running/result 네 저장소다.
별도 working·main completed·runtime 사본이나 thread stack까지 포함한 MANUAL 전체 RAM이 아니다.
필요한 화면에서는 `저장 입력`, `대기 슬롯`, `계산 중 슬롯`, `보관 완료 사본`처럼 범위를 드러낸다.
부모 revision이나 현재 VM 구성으로 과거 buffer metadata를 다시 쓰지 않는다.

`family="memory"`의 `source_sample_seq`와 `observed_uptime_ms`는 함께 오는 선택 필드다.
이 쌍은 cached firmware/page allocator 원천을 가리키고, 직접 heap의 관측 구간은
`collection.start/end_uptime_ms`다. 이전 2.1 메시지에 쌍이 없으면 원천 시각을 새로 만들어
채우지 않는다. 새 heap 관측이나 다른 family 수신으로 오래된 page allocator 값을 갱신하지 않는다.


### 상단 RAM 카드

명칭은 **Zephyr 관리 RAM 확보량**으로 한다. 같은 memory 봉투의
`firmware.ram_region_bytes - page_allocator.free_bytes`가 확보 bytes이고, 분모는 그 ram_region_bytes다.
카드 값은 고정 firmware의 물리 page-frame 관리 범위이며 실제 Xen 할당·VM 전체 실사용률이 아니다.
mapped/static·stack·heap arena·app pool 예약이 이미 포함되므로 다시 더하지 않는다.
heap/pool 내부 used/free가 바뀌어도 page free가 같으면 상단 값이 같은 것이 정상이다.

새 지표·topic·제품 API는 없다. `tools/validate_contract.py:ram_summary`와 실행 가능한 참조 CLI를
그대로 시험할 수 있다. 입력은 원 memory 메시지 하나뿐이며 서로 다른 표본을 합치지 않는다.
각 원천 status/rc, source 쌍·시각·range를 확인하고, u64 byte 문자열과 ROUND_HALF_UP 세 자리 percent를 보존한다.
null/unsupported/미확인/만료/불일치는 0이나 정상 비율로 채우지 않는다.
[계산·시간·원인·CLI 원본](../CONTRACT.md#96-zephyr-관리-ram-확보량--같은-payload의-파생-참조)을 따른다.

```sh
python3 tools/validate_contract.py --ram-summary fixtures-2.1/ram-committed.json
```

표시할 결과는 total16384/committed4096/free12288 bytes, percent `"25.000"`이다. 이 숫자는
합성 참조 fixture이며 운영 RAM 설정이 아니다. 전체 결과에는 identity/source/status가 포함된다.
상단 카드는 **마지막으로 받은 유효한 메모리 표본과 원 관측 시각**을 최근 관측으로 표시한다.
외부 current host/execution/receipt 확인은 표시 필수조건이 아니다. 기본 참조 호출은
`ReferenceConsumer.ingest(message, received_ms)` 다음 `ram_view(vm_id, now_ms)`이며 외부 `bind`가 필요 없다.
`expected_context` 생략/`None`은 추가 필터 없음이고, 독립 검증 원천이 있는 소비자만 명시해 대조한다.
이를 위해 새 HTTP 경로나 raw journal/receipt 파싱을 선행 작업으로 만들지 않는다.
명시적으로 `bind`를 선택하면 boot/publisher 필터와 변경 시 cache 분리를 유지한다. 이 기본 표시 경로의
완화는 RAM 최근 관측에 한정하며 다른 stream의 기존 bind 조건은 그대로다.

VM·guest boot·publisher별 그래프를 나누고 새 세션에 옛 그래프를 이어 붙이지 않는다.
이미 퇴역한 옛 세션의 지연 메시지는 최근 관측을 덮지 못한다. 새 세션에 아직 표본이 없으면 이전 세션으로
채우지 않는다. 이 선택은 물리 실행의 확인이 아니며 최근 MQTT 관측을 건강·복구·출력 허가로 사용하지 않는다.
수신 후 경과와 송신 당시 원 source-age를 합쳐 6초를 초과하면 값을 만료시킨다. 같은 RAM source-pair가
새 heap 봉투에 들어와도 최초 lease와 원 시각을 갱신하지 않는다. 같은 표본의 cache 만료/null은 새 실패로
보존해 원 source_rc를 보여주고 이전 정상색을 유지하지 않는다. 실패·상충한 옛 source는 다시 정상으로
채우지 않으며, 새 source의 정상 표본에서 회복한다.
MQTT 본문이나 retained inventory를 스스로 현재 binding으로 승인하지 않는다. 네트워크 내 지연을
검증하지 않았다면 이 6초를 end-to-end freshness 보장으로 표현하지 않는다.
boot A→B를 관측한 뒤 A가 처음 보는 publisher로 다시 도착해도 알려진 퇴역 boot로 거절한다.
같은 boot/publisher에서도 새 source의 원 host/execution/receipt 문맥이 달라지면 그래프 구간을 나누고 원 문맥을 보존한다. 이는 추가 외부 조회·현재 실행 검증을 표시의 필수조건으로 요구한다는 뜻이 아니다. 같은 현재 boot의 publisher 교체는 허용하되 그래프 구간은 나눈다. 이는 소비자가 관측·보존한 범위의 분리이며 실제 물리 실행의 최신성이나 무기한 모든 과거 세션의 기억을 뜻하지 않는다.

같은 source에서 context만 바뀌거나 동일 message_id의 RAM 값이 상충하면 원 표본과 lease를 보존하고 비율을 보류한다. 원 source 순번·시각의 역전은 최근 표본을 덮지 않는다. 실패·만료 view에도 원 source 쌍·시각·status/rc를 남기며, 다른 새 유효 표본에서 회복한다.

2026-11-04 결정은 이전의 외부 현재 context 필수 조건을 대체한다. 옛 수신 예제에 새 계산식을 적용한
수치가 나와도 새 firmware 수신·현재 운영 카드의 근거가 되는 것은 아니다.

아래 두 줄은 제품 codec이 내보내는 서로 다른 표본의 sample 형식이다. `0`은 수집에 성공한 실제 논리 점유 0이며,
같은 이름의 `null`·`owner_busy`와 구분한다. 전체 봉투와 크기·시간 검사는 정식 schema를 사용한다.

```jsonl
{"name":"memory.app.used_bytes","unit":"bytes","kind":"gauge","encoding":"u64_decimal","value":"0","status":"ok","reason":null,"source_rc":0,"labels":{"region":"drive_maps"}}
{"name":"memory.app.used_bytes","unit":"bytes","kind":"gauge","encoding":"u64_decimal","value":null,"status":"collection_failed","reason":"owner_busy","source_rc":-16,"labels":{"region":"drive_maps"}}
```

`xenova telemetry threads`는 로컬 명시 요청이다. 수신한 stack의 `max_used_bytes`가 없는 과거
2.1 snapshot은 같은 유효 행의 `size_bytes - unused_high_water_bytes`로 계산할 수 있다.
한도 초과·미지원·오류에서는 두 사용/여유 값이 null이다. 고정 native POSIX에서는 실제 pthread
스택을 관측하지 않으므로 `unsupported`이며, ARM fill scan의 실측과 구분한다.
CPU `thread_id`는 수명 ID이고 stack `row_id`는 snapshot 안의 행 번호다.
이미 만든 스택 표를 재사용하되 이름이나 행 순서로 CPU 표와 결합하지 않는다.

현재 작업트리의 실제 소비 지점은 backend `ZephyrTelemetryMessageHandler.process/values/number`,
`MetricValue`, `MetricFilter`와 화면 `domain/memoryPools.ts:buildMemoryPoolSeries`,
`pages/ResourcesPage.tsx`다. 기본 MQTT 수신기를 다시 만들 필요는 없다.
`memoryPools.ts`가 현재 고르는 `memory.region.*`와 새 `memory.app.*`를 분리해 연결하고,
십진 문자열의 정확도·원 status/reason/source_rc·관측 문맥이 저장/조회에서 유지되는지 확인한다.
별도 서브 후보에 이미 구현한 null/boolean 처리와 스택 표는 후보를 대조해 재사용한다.

기존 AUTO의 정상 0·BUSY·네 region 근거는 `tests/emit.c`,
`platform/tests/test_xenova_telemetry.py`, `platform/apps/drive/tests/test_engine.py`와
`platform/apps/drive/tests/memory-native`의 실제 커널 수락에 연결된다.
이번 MANUAL C codec 경계는 정상·BUSY/null·무효/만료 입력 보관·seq/time0·음수 원 시각·retained 읽기와
U64 경계 17개 및 무효 변형 24개 fixture로 제공한다. 기존 AUTO 6개 assertion도 그대로 검사한다.
codec fixture는 합성 소유 metadata이고, 실제 input/worker API의 native·ARM·보드 수락은 최종 후보 보고서와
별도로 대조한다. 문서나 schema 통과만으로 소비자 제품 연결·설치 완료라고 하지 않는다.
`tests/run_native_acceptance.py`는 두 실제 Publisher→격리 Mosquitto→독립 구독기 경로다.
새 MANUAL publisher fixture는 실제 worker의 계산·완료 사본 보관과 전송을 시험하지만,
입력 transport를 시작하지 않는 경계에서는 `manual_input`이 missing/0인 것이 실제 관측이다.
이를 실제 수동 입력 수신 성공으로 표시하지 않는다. 입력 수신은 실제 I/O 원천 fixture와 별도로 연결한다.
기존 AUTO pool·ARM stack 증가 수락과 새 MANUAL 수락은 같은 최종 후보의 검증 보고서에서 대조한다.
이들은 Spring/DB·화면 배포 결과가 아니다.
실보드 새 후보 확인은 별도 검증 보고서로 연결하며, 이 문서만으로 보드 적용 완료를 주장하지 않는다.

소비자 수락에서는 유효 0, 공간 부족 누계, `owner_busy/null`, `scan_limit/null`, 반환 후 정상 회복을
한 화면에서 구별한다. 일부 region·snapshot part 유실, publisher/boot 교체, 오래된 수신 뒤에도
미측정을 0이나 정상색으로 채우지 않는지 확인한다. 구현 후보·배포 후보·실행 명령·관측 결과를 회신한다.

## 지표 해석

| 항목 | 필요한 동작 |
|---|---|
| subscribe | 기본 네트워크는 `xenova/v1/vms/+/metrics`, 상세 지표는 `xenova/v2/vms/+/telemetry/metrics` |
| schema | v1 공통 봉투 `1.0`과 v2 상세 봉투 `2.1`을 각각 해석 |
| ID | v1 네트워크의 메시지별 UUID와 v2의 복합 메시지 ID를 각 계약대로 저장 |
| epoch | vm/guest boot/publisher/task epoch를 별도 저장. RAM 최근 관측은 세션별로 표시하며 외부 current binding 검증이 필수는 아님. 실행 권한·건강 판단용 current binding은 별도 플랫폼 근거로 확인 |
| 값 | u64/i64 decimal string을 손실 없이 처리. 필요하면 DB decimal/string. JS Number로 무조건 변환 금지 |
| 단위 | catalog의 bytes/bytes/s/count/packets/us/ms/percent/bool. 값별 label·kind·폭·reset 범위 보존 |
| 네트워크 바이트 | RX/TX 누적값은 v1 MQTT-03. 같은 VM·boot·iface·`counter_epoch` 안에서만 차분. 2.1의 `rx_bytes_per_s`는 bytes/s를 1024로 나눠 KiB/s로 표시 |
| 시각 | UTC null 가능. receivedAt 별도 저장. 동일 guest의 uptime끼리만 직접 비교 |
| 유효성 | null·unsupported·not_registered·no_sample·collection_failed·truncated 보존 |
| thread | Linux pid/rss로 강제 매핑하지 않음. snapshot_local row는 다른 snapshot의 같은 이름과 자동 결합하지 않음 |
| CPU | `cpu.system.percent`, `cpu.thread.percent`는 소수 JSON number. `window`의 guest-accounted UP 분모·구간·표본·페이지를 보존하고 pCPU 점유율로 표시하지 않음 |
| thread CPU | guest boot 안의 `thread_id`는 스레드 수명 식별자. 이름과 포인터 주소를 신원으로 쓰지 않음 |
| pool | `memory.region.*`의 `region`, `source_sample_seq`, `observed_uptime_ms`를 보존. kernel heap과 합쳐 전체 RAM 사용량을 만들지 않음 |
| heap | `memory.kernel_heap.accounted_bytes`는 같은 allocator 표본의 used+free. 힙·풀은 KiB, VM 할당 RAM은 MiB로 각각 표시 |
| 출처 | inventory hostname/display_name과 firmware build_id/artifact_sha256을 분리. artifact는 현재 실행에서 로딩한 `zephyr.bin`이며 암호학적 원격 증명이 아님 |
| UTC 품질 | unavailable의 null을 보존. source·uncertainty는 유효기간 안의 host 품질과 교환 구간에 대한 값이며 실행 기한 계산에 사용하지 않음 |
| histogram | cumulative/task_epoch·upper-bound percentile·saturation·표본 수를 저장. p99 평균 금지 |
| event | message ID와 원 event ID 별도 dedup. 발생시각과 보고시각 구분 |
| snapshot | 같은 ID/세션/collection의 part 전부 수신 시 전송완료, source truncated는 별개 |
| 최신값 | metric+labels별로 갱신. network 새 메시지가 오래된 heap을 fresh로 바꾸지 않음 |
| 판정 | publisher heartbeat를 app/VM/vehicle 정상으로 사용하지 않음 |

## 수신 결과의 단계

1. MQTT publish call 반환: local 제출 결과.
2. PUBACK: QoS 1 메시지의 broker 프로토콜 처리 단계. v1 네트워크와 v2 metrics는 QoS 0이다.
3. 독립 subscriber 수신·schema·의미 검사: 참조 발행자의 전달 수락.
4. Spring 저장·HTTP 조회·화면 반영: 별도 소비자 수락.
5. 복구·모드·제어권·ECU 출력: 별도 시스템 계약. Telemetry 수신만으로 통과 아님.

## 권장 화면 문구

- `Kernel system heap: used/free` — `VM 전체 메모리`라고 표시하지 않음.
- `Thread stack: reserved / maximum used / minimum free` — 현재 stack pointer나 현재 사용량이라고 표시하지 않음.
- `App map/path/search/odom pool: occupied / capacity` — 정적 예약과 내부 점유를 구분하고 VM 전체 사용률로 합산하지 않음.
- `MANUAL stored input / pending / running / retained result: occupied / capacity` — 관측 유효성과 입력 유효성·출력권을 구분. retained read는 메모리 해제가 아님.
- `Loop completion stopped; publisher alive` — 메시지가 온다는 이유로 정상색 표시하지 않음.
- `p99 ≤ bucket upper 255 µs, N=100` — `정확한 p99=255`로 표시하지 않음.
- `Inventory retained / current run unverified` — ONLINE 증거로 쓰지 않음.
- `Unknown / unsupported / collection failed` — 모두 0으로 바꾸지 않음.

담당별 구현·설치·회신은 기존 HTTP·Spring·화면 요청서에서 관리한다. 사용하지 않는 데이터를 버릴 수는 있지만, 처리한 것처럼 성공/정상으로 표시해서는 안 된다.
