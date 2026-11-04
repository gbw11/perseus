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

## 지표 해석

| 항목 | 필요한 동작 |
|---|---|
| subscribe | 기본 네트워크는 `xenova/v1/vms/+/metrics`, 상세 지표는 `xenova/v2/vms/+/telemetry/metrics` |
| schema | v1 공통 봉투 `1.0`과 v2 상세 봉투 `2.1`을 각각 해석 |
| ID | v1 네트워크의 메시지별 UUID와 v2의 복합 메시지 ID를 각 계약대로 저장 |
| epoch | vm/guest boot/publisher/task epoch를 별도 저장. current binding은 외부 플랫폼 근거로 확인 |
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
- `Thread stack unused (high-water estimate)` — 현재 stack pointer라고 표시하지 않음.
- `Loop completion stopped; publisher alive` — 메시지가 온다는 이유로 정상색 표시하지 않음.
- `p99 ≤ bucket upper 255 µs, N=100` — `정확한 p99=255`로 표시하지 않음.
- `Inventory retained / current run unverified` — ONLINE 증거로 쓰지 않음.
- `Unknown / unsupported / collection failed` — 모두 0으로 바꾸지 않음.

이 문서는 서비스 구현 지시가 아니라 최소 해석 계약이다. 사용하지 않는 데이터를 버리는 것은 가능하지만, 처리한 것처럼 성공/정상으로 표시해서는 안 된다.
