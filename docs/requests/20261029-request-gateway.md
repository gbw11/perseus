# Gateway 직접 구현·공동 연동

담당: 박신영 / 플랫폼·Gateway. 상태: **G1 실제 로컬 호출·G2 상태 연결·G3 피드백 제공자 구현 / 상대 제품·장치·운영 수락 전**.
Gateway 자체를 타 담당자에게 위임하지 않는다. 아래가 2026-11-02 현재 작업이며 당시 원문은 이력에 보존한다.

## 현재 작업과 제공물

| 기존 ID·상태 | 수정 위치·이유 | 정상 수락·실패 보호 |
|---|---|---|
| DEC-11·16 / 공통 권위·계획 인계 로컬 제공 | `platform/services/gateway/xenova_gateway/planning.py`, `authority.py:commit_transition`, `warm.py`, `verified_runner.py`. 준비·권위 커밋·새 문맥 계산을 구분 | 원 요청·기한·generation 보존, 정상 A 유지·B 준비·조건부 커밋. 중단·늦은 응답·준비 실패의 무권한 승격 0. 실제 stateful 계산은 DEC-19 행 |
| D03-G01·G03·G04 / 국소 제공, G02 결정 대기 | `authority_ops.py`, `authority.py`. 정상 원본 재확인과 손상 백업의 최신성 복원을 구분 | 같은 lease/hash·원 이력 보존, 명시 재무장. marker 삭제·옛 권위 초기화/자동 복원 0 |
| DEC-12·18·ARCH-T16·18 / 실제 생산자 공동 수락 전 | Spring/UI 입력 생산자와 MQTT·C 수신 adapter. 실제 session/sample/action·원 나이를 이어야 함 | 같은 MANUAL 승계 유지, AUTO→MANUAL의 현재 중립 뒤 새 조작. 캐시 재발행·generation 교체만으로 새 행동 승인 0 |
| DEC-13·ARCH-T25·26·38 / G3 제공자 로컬, 실제 장치 전 | `feedback.py`, `odometry.py`, `output_owner.py`, `verified_runner.py`. feedback-1 호환과 명시 feedback-2·원 encoder/장치 시계·Pi 로컬 상태 | 실제 PTY/pyserial의 부분·붙은 frame, CRC·옛 ACK·장치 재부팅·기한·센서 부재. write-only 유지, 타이머/목표 적분의 물리 관측 위장 0 |
| DEC-19·ARCH-T21~23·32·35 / 실제 planner/path 제공자 연결 | `state_transfer.py`, `drive_link.py`, `drive/planner/engine.c`, `lattice_app.c`. 기존 JSON adapter와 별도 full path binary·실제 적용/target 계산 | 제품 C·실제 MQTT/native A/B. 당시 수락 사본 재사용·권한 종료 뒤 갱신 거절, 새 instance의 실제 준비·정당한 재합류. 실차 수락 별도 |
| D01/D03·DEC-11·16 / G1 제공자 로컬 완료, Worker 연결 전 | `ipc.py`, `client.py`, `provider.py`, 기존 actor/planning/Authority. 실제 별도 프로세스 submit/get/commit/cancel/finish | 원 key·기한·승인 의미, 내구 수락·지속 exclusion·정상 종결, slow reader/partial/포화·저장 EIO·재시작·peer 검사 |

플랫폼의 [최신 로컬 검증](../verification/recovery-scenario-closure-20261030.md#gateway-provider)과
[Gateway README](../../platform/services/gateway/README.md)가 제공 근거다.
부가 진단은 제한된 queue로 분리됐고 최초/희귀 cause·필수 Authority 저장·STOP 순서는 보존한다.
직접 ERROR/CRITICAL 경로의 실제 지연·Zephyr 총 로그 비용은 기존 근거 범위를 넘겨 완료로 표시하지 않는다.

후속 이력 수명은 `history.py`·`client.new_request_id`와 같은 Authority 저장 경계에서 제공한다.
활성/미확정 보호를 자동 해제하지 않고 종결·원 보존·참조 해소 뒤 새 수락과 함께 정리한다.
계약과 실제 반복/주행/ARM·설치 수준은
[현재 검증](../verification/recovery-scenario-closure-20261030.md#history-driving-app)을 따른다.

## 6-B 포장과 설치 전제

새 `xenova-gateway` recipe는 현재 Python 진입점·공용 MQTT·고정 Paho/pyserial·하위 모듈을 연결한다.
unit은 기본 disable, 설정은 비활성 example만 제공한다. package 설치는 Authority 초기화·재무장·출력 허가가 아니다.
현재 파일·RPM·의존성·실행 경로와 검증 수준은 [배포 준비 원본](../platform/deployment-readiness.md)에 모은다.
운영 설정·credential·차량 수치는 package hook에서 만들지 않는다.

## 공동 결정과 담당 회신

Worker–Gateway 전송은 [실제 client/UDS 계약](../contracts/gateway-provider-v1.md)으로 제공한다.
Worker는 [지정한 Engine/rejoin 연결 위치](20261029-request-recovery-worker.md#d03--제공된-gateway-client를-복구-단계에-연결)에 소비를 구현한다.
실제 planner의 상태 생산·기하 검증·target 계산 연결은 플랫폼 소유 코드에서 구현했다.
Jetson 담당의 실제 mapper와 ESP32 실제 표본/차량, Worker의 원 복구 단계 소비는 각 요청서에서 수락한다.
실제 차량 한계·ACK 기한·firmware/센서는 [ESP32 요청](20261029-request-esp32.md)에서 공동 수락한다.
백업 최신성 복원은 기존 D03-G02 대기이며 이번 포장·IPC로 닫지 않는다.

각 ID별 후보(commit+diff), 변경 기호, 생산/소비 계약, 정상·실패·회복 명령/결과, 미측정 비용과 환경,
설정 공급자·설치/재무장/장치 수락 상태를 회신한다. [7번 입력표](../platform/deployment-readiness.md#stage7)는 이번에 실행하지 않는다.

## 변경 이력

아래 당시 요청과 측정은 보존하며, 현재 작업은 위 본문을 따른다.

<details>
<summary>6번 정리 전 Gateway 원문</summary>

# Gateway 직접 구현·공동 연동 목록

담당: 박신영 / 플랫폼·Gateway. **상태: 최소 연결 구현·로컬 검증 근거 보존 / C 입력 수신 보완 로컬 검증 / 후속 확장·설치·장치 수락 전.**
Gateway 자체는 타 담당자에게 넘기는 영역이 아닙니다. 현재 `48dc3cc`, `b35a675`, `0f26bf9`의 구현 이력을 기준으로 직접 작업하며, ESP32·입력 생산자·Worker와의 공동 연결만 해당 담당 요청서와 대조합니다.
기존 D03-G01~G04와 미완료 상태를 유지합니다. 이 문서는 추가 작업의 시작점이며 정식 메시지 필드·시간값의 원문이 아닙니다.

## 현재 할 일과 읽는 순서

1. 아래 범위표에서 최소 연결과 담당자 확장을 구분합니다.
2. [주행 설계](../platform/drive-pipeline-architecture.md)의 9.4·9.6·12·13·19·20절을 읽습니다.
3. [MQTT 계약](../contracts/mqtt-contract-v1.md)의 현재 제어·준비·출처 조건과 비교합니다. 새 프로필의 미확정 필드를 추측해 구현하지 않습니다.
4. [기존 Authority 수습 요청](2026-10-27-authority-recovery-ctrl.md)의 D03-G01~G04와 [원 Gateway 요청 이력](2026-10-20-manual-app-gateway-ctrl.md)을 읽습니다.

## 최소 수정과 후속 직접 구현

| 구분·연결 ID | 대상 파일·함수 | 입력·출력 및 요구 | 수락 조건 |
|---|---|---|---|
| 승인된 최소 연결, 로컬 완료 | `platform/services/gateway/xenova_gateway/warm.py:WarmDriveMachine`, `verified.py`, `verified_runner.py`, `config.py`, 공용 `drive_binding.py`·`client.py` | warm-drive-1 선택, 앱별 바인딩·대기/활성화/실제 진행과 seq 전 신원 guard. Authority·generation·출력 보호 유지 | `tests/test_warm.py`, 공용 `tests/test_client.py`, 동일 native 후보의 A/B 실제 격리 broker 시험. [후속 검증](../verification/zephyr-drive-foundation-20261029.md#후속-앱별-수명과-warm-준비-연결) |
| DEC-11·16, 공통 계약 구현 | `planning.py`, `authority.py:commit_transition`, warm actor/runner | 원 요청에 준비 근거를 채택하고 owner/generation·대상과 원 결과를 한 번 커밋. 정상 A 사전 준비 중 유지, 새 문맥 계산 뒤 출력. 현재 실제 연결은 명시적 stateless 앱 | 원 ID/기한 재발급 0, 취소/커밋 순서, 저장 미확정·프로세스 재시작, A/B 대칭 계획 인계. 상태 있는 앱의 실제 전송·연속 장치 적용 수락은 별도 |
| DEC-19·ARCH-T21~23·32·35, 후속 | 상태 수락·전환 연결부. 구체 함수는 계약 확정 후 지정 | 당시 허용 출처에서 수락한 완전 사본의 현재 호환성 확인. 경로를 대신 계산하거나 옛 명령을 새 generation으로 포장하지 않음 | 정상 사본은 유한하게 채택, 불일치·부분·권한 종료 후 지연 갱신 거절. 남은 출력 예산으로 연속 승계/정지 후 재개 구분 |
| D03-G01·G03·G04, 국소 구현 / G02 대기 | `authority_ops.py`, `authority.py`, 기존 요청의 복원 지점 | 읽기 전용 진단, 같은 lease·원본 hash 대조의 정상 원본 재확인. 백업 최신성 입증은 별도 | 원 ID/기한/generation 보존·재무장 유지·파일 오류/중단 회귀. marker 삭제·generation 초기화 금지 |
| B3·BO3·DEC-02, 국소 구현 / 실측 수락 전 | `output_guard.py`, `__main__.py:open_sink`, `verified_runner.py:_submit` | root 소유 차량 설정으로 요청/양자화 값·제출 목표 변화율 제한. serial은 설정 필수, 위반·쓰기 실패에 DRIVE 차단·같은 회차 STOP 시도 | 정상 목표 제출, 범위/변화율/부분 쓰기/시계 이상 거절. 실제 값·장치 적용은 차량 담당자와 수락 |
| DEC-13·ARCH-T25·26·38, 후속 | `sink.py`, `frame.py`, 실제 장치 피드백 연결부 | 시리얼 쓰기·장치 수락·적용·실제 정지를 구분하고 오류를 출력 보호에 연결 | 제출만의 적용/정지 완료 0. 장치 미응답은 유한 종결, watchdog/E-stop 보존 |

## 제공물·선행조건·금지 효과

- 플랫폼 제공물: 실제 계산 수명·원 문맥·기한·join/잔존 근거와 현재 execution/Bootstrap. 새 연결의 완료 여부는 같은 후보의 시험 근거로 확인해야 합니다.
- 모드·owner·출력 허가는 Gateway가 소유합니다. Worker/앱 준비만으로 권한을 발급하지 않습니다.
- 사용자 MANUAL 선택, 명시적 AUTO 복귀·준비 유지·카운트다운을 유지합니다. 제어 적용 종결 1초와 명령 신선도 100ms를 같은 예산으로 보지 않습니다.
- 상태 사본 전송·장치 적용의 남은 필드는 DEC-19·13의 생산자/소비자와 확정합니다. 공통 준비·커밋과 세션 입력 의미는 설계 12.2절·MQTT 7.1절을 따릅니다. Tailscale·broker ACL·운영 서비스·장치는 변경하지 않습니다.

## C 입력 수신 보완 — DEC-12·ARCH-T16

플랫폼 직접 소유이며 Spring/UI 담당자에게 C 수신 결함을 대신 수정하도록 넘기지 않는다.
[정합성 검토 R02·R03](../verification/zephyr-drive-foundation-review-20261030.md)의 실제
`xm_zclient.c:on_publish/latest_slot` + C 어댑터 반례를 기준으로 한다.

- 대상: `platform/apps/common/xenova-mqtt-c/ports/zephyr/xm_zclient.c`, drive-runtime의
  `io.c`·입력 snapshot, `drive/zephyr/src/workers.c`·`manual_session.h`·`adapter.c`.
- 최소안: 봉투·구독·순서 검증을 통과한 원 표본을 v1 수신 소유자에서 관측한다.
  최신 입력과 별개로 전환별 중립 근거와 최초 수신 시각을 제한된 사본으로 보존하고,
  원 session/sample/action/generation 및 현재 manual 앱 instance/lifetime에 연결해 전달한다.
  계산·준비 순번을 수신 작업자가 생성하거나 입력 이력/무제한 큐를 추가하지 않는다.
- 수락: 실제로 수락된 중립/새 조작이 계산 전 최신 슬롯에서 합쳐져도 다음 실제 계산은
  해당 중립 근거를 사용할 수 있다. 새 봉투의 같은 원 sample은 200ms를 갱신하지 않는다.
  다른 generation/session/app instance·취소·재초기화 뒤의 옛 근거는 거절하고 정당한 새
  문맥은 정상 수락한다. Gateway 출력 허가와 별도인 앱 계산·기능 준비도 확인한다.
- 현재는 수신 전용 원 표본 guard·중립 사본과 앱 instance 확인을 반영했다.
  실제 MQTT decoder/cache·Zephyr I/O thread·C adapter 회귀와 앱 교체 반례로 확인한다.
  중립을 여러 표본 동안 유지한 시험만으로 수락을 닫지 않으며, 실제 UI/WS 생산자·장치 수락은 별도다.

## 5.5번 Authority 저장 비용 보완

AuthorityStore.save는 크기를 검사한 JSON을 독립 사본으로 한 번 파싱해 실제 저장과
성공 뒤 RAM 채택에 사용한다. 같은 입력의 중복 직렬화만 줄이며 file/parent fsync·저장
미확정 잠금·기대 generation/원 전환·재무장·출력 보호를 유지한다. 새로운 wire 필드는 없다.
[로컬 결과](../verification/recovery-scenario-closure-20261030.md#stage55)는 실제 장치 적용 수락이 아니다.
Gateway의 문서상 `/opt/xenova/gateway` 소스 배치와 Python import 부분 대조는 정규
Yocto recipe/unit·운영 설치 검증을 대신하지 않는다. 해당 배포 제공과 장치 수락은 남아 있다.

## 진행 기록

- 2026-10-29: 최소 소비 연결을 구현·로컬 검증했습니다. Gateway 담당은 커밋 이력과 사용자 확인에 따라 박신영입니다. 계획 인계·Authority 수습은 후속 직접 구현이고 장치 적용은 ESP32 담당자와 공동 수락합니다. 앱 하나의 receipt 미확정이 정상 다른 앱의 바인딩·출력 근거를 철회하지 않는 반례도 연결했습니다.

- 2026-10-30: 계획 AUTO의 재개 우회를 국소 차단하고 정상 재개/대칭 인계를 회귀했다. C 입력 최신 슬롯의 중립/원 sample 반례를 재현한 뒤 수신 단계 guard와 앱 instance에 묶인 근거 전달로 보완했다. 기존 이력·담당자 대기 상태는 유지한다.

</details>
