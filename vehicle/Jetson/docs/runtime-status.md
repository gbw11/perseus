# Runtime 진단 snapshot

`runtime.runtime_status.RuntimeStatus`는 설계14장7.1절의 exact `mapper-runtime-1`
payload만 소유한다. canonical ASCII JSON은2048B 이하이며 `fields`는 독립 사본이다.
MQTT envelope, HTTP endpoint, 사용자 RUN/STANDBY 요청은 이 codec의 계약이 아니다.
`from_bytes`는 길이를 먼저 제한하고 중복/미지 key, 숫자·비유한 상수, 잘못된
UUID/SHA/enum/u64를 거절한다. 시각과 순번은 full u64 decimal string이다.

`_Runtime.runtime_status(now)`는 호출자가 공급한 실제 J monotonic 시각에서 S가
이미 수락한 작은 원 진술만 투영한다. 파일·SQL·SHM·JPEG·doctor·네트워크를 사용하지
않고 `AsyncResourceLog`의 요청·DURABLE ACK·원 완료 시각을 변경하지 않는다.
JIO/source 설치 profile이 없으면 다른 vehicle/device를 발명하지 않고 `None`이다.

## 원 증거와 현재성

- worker 상태와 progress는 실제 lifecycle receiver가 수락한 마지막 HEARTBEAT다.
  마지막 진술 자체는 만료 후에도 보존한다. heartbeat issued/expiry/received의 기존
  네 값과 progress 순번/원 시각을 별도로 보관하며 새 snapshot 시각으로 갱신하지 않는다.
- 캡처 준비는 현재 A의 admitted RAW BIND/원 positive ACK, current STREAMING
  heartbeat, 같은 worker/binding의 CAPTURE_READY와500ms 미만 CAPTURE_PAIR를 요구한다.
  EOF/reap/STOP/회복의 old-owner fence가 제한한다. M의 clock/odom/publication/ToF
  부재나 M-only 교체는 건강한 A의 캡처 준비를 내리지 않는다.
- A의 ASSET_VALID→GRAPH_STARTED는 같은 현재 worker/binding과 원 순서를 요구한다.
  현재 M의 같은 binding NN_READY가 있으면 residency evidence를 PAIRED_OUTPUT으로
  표시한다. 이는 검산된 host asset/session 증거 수준이며 카메라 RAM readback이 아니다.
- parser VALID는 현재 M/RAW/heartbeat, 그 M의 NN_READY, 마지막 정상 parser 완료의
  원5초 수명을 요구한다. STALE_INPUT 완료는 정상 완료의 시각을 갱신하지 않는다.
  parser500ms 단일 작업 예산과 정상 완료5초 정체 감시는 별도이며 변경하지 않는다.
- S에 실제 OAK NN input/output 원 시각은 전달되지 않는다. `inference_state`는
  UNAVAILABLE, `last_input_j_us`와 `last_result_j_us`는 `null`이다. parser 완료를
  OAK inference 완료로 바꾸지 않는다.
- 현재 S에는 PublicationBinding별 metric predicate의 원 producer proof가 없다.
  `metric_ready=false`이며 이유 코드 **METRIC_UNASSESSED**를 등록한다. 후속 연결은
  실제 M의 binding·원 관측·clock/odom/calibration·원 expiry proof를 전달해야 하며
  M RUNNING, World publish 성공, parser VALID로 대신하지 않는다.

S의 내부 PREPARE/ALLOCATING/HANDLES/WORKERS는 wire enum이 아니다. 아직 원 local
준비가 없으면 STARTING, 선택 회복 중이면 RECOVERING, 정상 상주와 current worker
heartbeat가 있으면 RUNNING, 현재 worker 진술이 제한되면 DEGRADED로 투영한다.
정리 중 QUIESCING, 실제 정리·reap·종료가 확인된 뒤 STOPPED, 원 실패는 FAILED다.
RUNNING·캡처·parser 진단은 metric/World/Scene/모터의 사용 승인이 아니다.

## 소유권과 소비 경계

S-main은 최대1Hz로 `latest_runtime_status`의 불변 참조 하나만 교환한다. 새 thread나
I/O는 없으며 종료 receipt는 마지막 실제 시각으로 다시 만든 사본을 포함한다.
status 형식 오류는 최신 진단을 `None`으로 철회하고 `runtime_status_error`에 작은
오류 type만 남긴다. 필수 자원 ACK/제어 실패로 바꾸지 않는다.

consumer는 `snapshot_j_us`를 원 snapshot 시각으로 표시하고 오래된 사본을 현재
준비로 취급하지 않아야 한다. HTTP 요청/재접속이나 사본 읽기로 snapshot·worker·모델의
원 시각/expiry를 연장하지 않는다. 후속 S-http는 selector 기반 bounded owner이며,
목표 직접 thread8개(S4/A1/M2/D1)다. 현재 이 모듈은 HTTP/JPEG를 시작하지 않는다.

`RuntimeStatusTests`는 순수 값 계약, `ResidentStatusSnapshotTests`는 실제 lifecycle
codec/receiver와 소프트웨어 RAW ACK에서 시작한 owner별 투영을 검사한다. 이 시험은
실제 장치·모델 성능·HTTP·주행 준비나8개 thread 실측 수락을 주장하지 않는다.
