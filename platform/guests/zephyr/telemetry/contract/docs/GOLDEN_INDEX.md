# Golden Payload 색인

모든 값은 합성 규격 예제다. 실제 장치 측정 증거가 아니다.

## 정상적으로 해석 가능한 메시지

| 파일 | 설명 | compact bytes |
|---|---|---:|
| `01_inventory_unsynced.json` | 실제 wall clock이 없다는 사실을 null로 전달하는 구성 예시; frequency 등 값은 합성 | 1974 |
| `02_inventory_bound_time.json` | host-provisioned 문맥과 UTC 품질 예시; 수신자는 receipt를 별도로 확인해야 함 | 2108 |
| `03_publisher_heartbeat.json` | Publisher 생존일 뿐 앱 정상 선언이 아님 | 969 |
| `04_heap_and_unsupported_cpu.json` | kernel heap 범위와 미지원 CPU 사용률을 구분 | 1473 |
| `05_collection_error.json` | 수집 실패를 0으로 위장하지 않음 | 963 |
| `06_network_raw32.json` | 원본 netfront 32비트 카운터와 링크 상태; IP/앱 준비 주장 없음 | 1655 |
| `07_loop_progress.json` | 루프 완료 수·부호 있는 지터·경과를 분리 | 2344 |
| `08_loop_stalled.json` | heartbeat가 나와도 loop 완료 수는 그대로이고 완료 age는 증가; 자동 정상 판정 없음 | 2347 |
| `09_first_completion_missing.json` | 완료 전 age는 null이며 0이 아님 | 1255 |
| `10_timing_histogram.json` | p99=255는 버킷 상한, 실제 최대는200; 합성 표본100개 | 1285 |
| `11_no_timing_samples.json` | 표본0일 때 요약값 모두 null | 1282 |
| `12_open_histogram_bucket.json` | 열린 마지막 버킷이면 유한 분위수 상한을 만들지 않음 | 1299 |
| `13_boot_event.json` | 사건 발생 uptime과 보고 uptime을 구별; 역사 자료 | 1165 |
| `14_error_event.json` | 오류 원코드를 보존; EIO는 합성 사례 | 1160 |
| `15_threads_with_scan_limit.json` | 스레드 row 자체는 유효하지만 특정 스택 스캔은 제한에 걸림 | 1353 |
| `16_threads_truncated.json` | Core row 한도 초과, 반환행2개 유효하고 전체12개였음을 밝힘 | 1363 |
| `17_history_loss.json` | 관측 전에 덮인 이력과 전송 큐 유실을 숨기지 않음 | 1311 |
| `18_second_vm.json` | 같은 metric seq라도 VM이 다르면 별개 메시지 | 1473 |
| `19_new_guest_boot.json` | guest reboot로 boot/publisher ID 교체, seq 재시작 | 1468 |
| `20_publisher_restart_only.json` | guest 부팅은 그대로, 발행자 재시작만 분리 | 1473 |
| `21_uint64_precision.json` | JS 안전정수 한계를 넘는 counter를 십진 문자열로 보존 | 953 |
| `22_publisher_degraded.json` | 발행자가 살아 있으나 collector 성공 이력이 없음 | 967 |
| `23_threads_part0.json` | 한 번 수집한 snapshot을 전송 페이지만 분할, 완전성은 모든 part 수신 후 판정 | 1743 |
| `24_threads_part1.json` | 한 번 수집한 snapshot을 전송 페이지만 분할, 완전성은 모든 part 수신 후 판정 | 1152 |
| `25_loop_not_registered.json` | 진단 코어만 존재할 때 실제 앱 루프가 있는 척하지 않음 | 1038 |
| `26_raw_counter_before_wrap.json` | 32비트 원본 counter 상한 | 1664 |
| `27_raw_counter_after_wrap.json` | wrap 이후 원본값. 확대 누적값으로 위장하지 않음 | 1655 |
| `28_saturated_summary.json` | 포화 시 합계·분위수 보장하지 않고 null 처리 | 1288 |
| `29_thread_scan_not_requested.json` | 비싼 스캔 미요청: 전체 스레드 수를0으로 만들지 않음 | 972 |
| `30_history_collection_failed.json` | 수집 실패: 과거 오류가0개였다고 주장하지 않음 | 933 |

## 거절되어야 하는 규격 위반

| 파일 | 이유 | 기대 오류 |
|---|---|---|
| `01_v1_schema.json` | v1 위장 금지 | `SCHEMA_INVALID` |
| `02_topic_wrong_vm.json` | 타 VM 토픽 | `TOPIC_MISMATCH` |
| `03_message_id_counter_only.json` | 전역 충돌하는 정수ID | `MESSAGE_ID` |
| `04_numeric_seq.json` | 64비트 seq는 문자열 | `SCHEMA_INVALID` |
| `05_u64_overflow.json` | u64 범위 초과 | `INTEGER_RANGE` |
| `06_failure_zero.json` | 실패를0으로 표시 | `MEASUREMENT_STATUS` |
| `07_unavailable_fake_utc.json` | 가짜1970 UTC | `CLOCK_QUALITY` |
| `08_synced_without_source.json` | 출처 없는 동기화 선언 | `CLOCK_QUALITY` |
| `09_unbound_with_execution.json` | 미결합인데 임의execution 주장 | `CONTEXT_BINDING` |
| `10_collection_after_send.json` | 발행 후에 수집 완료 | `CLOCK_ORDER` |
| `11_wrong_unit.json` | heap을count로 위장 | `METRIC_CONTRACT` |
| `12_counter_double.json` | counter를double로 강제변환 | `MEASUREMENT_TYPE` |
| `13_exact_p99_mislabel.json` | 정확한p99인척 200 전달 | `HISTOGRAM_QUANTILE` |
| `14_hist_count_mismatch.json` | 버킷 합과count 불일치 | `HISTOGRAM_COUNT` |
| `15_no_samples_zero_summary.json` | 표본없음을0으로 위장 | `HISTOGRAM_EMPTY` |
| `16_open_bucket_finite_p99.json` | 열린 버킷에 유한 상한 | `HISTOGRAM_QUANTILE` |
| `17_metrics_retained.json` | 주기값을retained로 발행 | `RETAIN_POLICY` |
| `18_heartbeat_qos1.json` | 오래된 생존 재전송 방지 정책 위반 | `QOS_POLICY` |
| `19_app_health_from_reporter.json` | 통신 생존을앱정상으로 선언 | `SCHEMA_INVALID` |
| `20_stack_bad_unused.json` | unused가size보다 큼 | `STACK_RANGE` |
| `21_truncation_silent.json` | truncated를ok로 숨김 | `SNAPSHOT_COMPLETENESS` |
| `22_bad_part_index.json` | index가count이상 | `SNAPSHOT_PART` |
| `23_raw_counter_widened.json` | 32비트원본을의미없이확대 | `RAW_COUNTER_RANGE` |
| `24_stage_multibyte_limit.json` | stage31byte한도,글자수가아님 | `STAGE_BYTES` |
| `25_packet_oversize.json` | wire4096byte한도 | `PAYLOAD_TOO_LARGE` |
| `26_unknown_metric.json` | 정의없이새수치의미추가 | `METRIC_CONTRACT` |
| `27_metrics_missing_labels.json` | task문맥누락 | `METRIC_LABELS` |
| `28_history_cannot_claim_lossless.json` | 원본보다더많은보존수 | `HISTORY_COVERAGE` |
| `29_unrequested_zero_count.json` | 전체수미관측을0으로위장 | `SNAPSHOT_COMPLETENESS` |
| `30_failed_history_zeroed.json` | 조회실패를빈정상이력으로위장 | `HISTORY_COVERAGE` |

## 순서/세션 사례

| 사례 | 목적 |
|---|---|
| `01_duplicate_no_lease_refresh` | 중복 수신이 lease를 연장해서는 안 됨 |
| `02_vm_independence` | 동일 seq와 metric이 A/B에서 충돌하지 않음 |
| `03_reboot_requires_binding` | 새 세션 도착만으로 current binding을 바꾸지 않음 |
| `04_publisher_restart` | guest 부팅은 같아도 publisher 세션 변경을 따로 검증 |
| `05_retained_not_liveness` | retained inventory 수신은 current 생존 증거 아님 |
| `06_stream_ordering` | 같은 스트림 역전 표본은 최신값을 덮지 못함 |
| `07_snapshot_out_of_order` | page 순서는 허용, 전부 수신할 때만 전송완료 |
| `08_truncated_not_complete_source` | 모든 page 수신과 원본 전부 관측은 다른 것 |
| `09_other_metrics_not_refresh` | network 갱신이 오래된 heap 값을 정상으로 갱신하면 안 됨 |
| `10_heartbeat_not_loop_progress` | Publisher heartbeat가 계속 와도 실제 loop 완료수는 그대로일 수 있음 |
| `11_unavailable_snapshot` | 실패한조회완료는원본관측완료가아님 |
