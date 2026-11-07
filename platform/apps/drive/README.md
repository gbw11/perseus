# A/B 공통 Zephyr 주행 펌웨어

같은 `drive/zephyr` 번들을 두 VM에 사용하고 VM마다 UUID·MAC·인증정보·CPU 배치를 유지한다.
기본 프로필은 기존 `auto_step()`·`manual_step()`을 유지한다. 명시적인 `warm-lattice-lab`은
실제 bounded lattice·충돌 검사·tracker와 독립 MANUAL을 연결한 합성 차량 시험 프로필이다.
AUTO/MANUAL 계산 모드와 Active/Standby VM 선택은 서로 다른 축이다.
`warm-lattice-lab`은 기존 여섯 network context와 별도 DHCP 수신 handler를 함께 수용하도록
`NET_MAX_CONN=7`을 사용하고 CMake에서 이 최소 관계를 검사한다. 연결 수는 앱 준비를 뜻하지 않는다.
고정 SDK native/ARM·소유 시험 B의 1.0.9 근거와 기본 이미지 1.0.8의 구분은
[보드 후속 검증](../../../docs/verification/recovery-scenario-closure-20261030.md#budget-board-observations-20261106)에 둔다.
clock 수신 경계 1.0.10/1.0.11, 반복 제한 명령 1.0.12, 유한 차량 입력 진단 1.0.13의
시험 이력을 보존한다. snapshot busy 보완은 기본형 1.0.14·선택 진단형 1.0.15로 ARM 빌드·
불변 포장했다. 준비·발신 단계 계측은 같은 보호를 유지한 기본형 1.0.16·진단형 1.0.17로
별도 빌드·포장했다. 계산 결과의 counter/uptime 경계 보완은 기본형 1.0.18·진단형 1.0.19로
ARM 빌드·포장했다. 초기 설치·catalog의 기본 선택 변경은 아니며, 빌드·포장과 실보드 정상 진행
수락은 [후속 기록](../../../docs/verification/recovery-scenario-closure-20261030.md#clock-retry-submit-20261106)에서 구분한다.
입력 transport 진단 후보 1.0.28은 `lattice_input.c`의 `f52651d1…` 소스로 별도 ARM 빌드·
불변 포장했다. 기존 1.0.22/1.0.23의 `a76a6b55…` 제공물과 구분하며 기본 선택은 바꾸지 않는다.
실제 CPU·heap/pool·작업 완료 근거와 기능 준비의 관계는
[건강 판정 계약](../../../docs/contracts/health-evidence-v1.md)을 따른다. 공통 펌웨어는 기존
release deadline과 필수 계산 실패를 NOT_READY·0 출력 후보로 연결하며, 보조 진단만 없으면
정상 계산을 차단하지 않는다. `xenova drive health`는 원 계산의 최신 local 평가를 조회한다.

## 책임과 확인 근거

| 사실 | 확인하는 곳 | 대신 확인할 수 없는 것 |
|---|---|---|
| MQTT 연결·구독 | v1 socket / Gateway의 CONNACK·SUBACK | 앱 준비·제어 적용 |
| 현재 허용된 실행 | Manager Execution 관측 + 적용된 Bootstrap receipt의 root 전용 바인딩 | 게스트 본문에 적은 execution ID만으로 승인 |
| 실제 계산 완료 | 앱 어댑터의 `progress_seq` | worker 송신 횟수 |
| 제어 적용·기능 준비 | verified의 `app.readiness`, warm의 `app.lifecycle` | 출력 권한 |
| 출력 허가 | Gateway 영구 원본·현재 제어 적용·명시적 재무장 | heartbeat 복귀·부팅 성공 |
| 시리얼 제출 | 실제 쓰기 수락 바이트 수 | 최종 장치 적용 |

토픽·필드·자료형·준비 보고 규칙의 원문은 [MQTT 계약](../../../docs/contracts/mqtt-contract-v1.md#62-앱-준비-보고-appreadiness)이다.
장치 ACK는 Gateway의 명시적 feedback-1/feedback-2에서만 소비한다.
write-only에서는 적용을 알 수 없다. `vehicle.status.applied_generation`을 추측해 채우지 않는다.

## 지도 기반 제품 프로필

`world/world.c`가 정식 nanopb 입력·불변 map·exact odom anchor를 관리하고,
`planner/lattice.c`가 실제 탐색·swept footprint·RPP 계산을 수행한다.
`planner/engine.c`는 map/path의 writer·reader lease와 원 계산의 채택·취소를 관리한다.
`zephyr/src/lattice_input.c`의 Jetson map 연결과 Pi state 연결은 별도 소유자이며,
작은 Pi 차량 입력은 기존 제어 수신자로 받는다. AUTO 계산과 MANUAL 계산도 서로 기다리지 않는다.

도로 우선 운용은 [drive-world의 feature bit 0](../../../docs/contracts/drive-world/README.md#도로-우선-입력--feature-bit-0)을
사용한다. mapper가 제공한 도로 cell을 선호하고, 기존 바깥 경계 안의 여유 사용을 별도 비용
우선순위로 최소화한다. 허용 영역이라는 이유만으로 빈 곡선의 안쪽을 단축하지 않는다.
`mark_road` 생산 helper와 C/Python strict reader, 불변 path/인계의 `world_features`를 함께 제공한다.
폭을 현재 pose나 그림에서 추정하지 않는다. 과거 feature=0은 같은 의미의 품질 수락이 아니다.

전방 목표도 원 4.5m 범위에서 현재 회전 grid와 기존 차체 끝 여유 안의 reference 지점으로
고른다. 좌표를 잘라 새 도로점을 만들거나 지도 밖으로 외삽하지 않는다. reference 꼭짓점과
현재 위치가 같은 0길이 구간은 건너뛰고, 유효한 전방 목표가 없으면 기존 제한을 유지한다.
자동 목표가 현재 장애물과 겹치면 `lp_start_local`이 같은 collision index·작업량·원 기한으로
종단 차체를 검사하고, 원 전방 범위 안에서 resolution 간격으로 더 가까운 reference 목표를
고른다. 다음 rolling map에서는 장애물 너머의 목표를 다시 선택할 수 있다. 명시 목표를 받는
`lp_start`와 지속 중인 `merge_goal`·통과 근거는 이 조정으로 바꾸지 않는다.

일반 도로 추종의 편차만으로 우회 의도를 만들지 않는다. 원 도로 위 차체 통로의 막힌 cell을
기존 충돌 색인·원 탐색 예산 안에서 확인한 경우에만 새 우회 방향을 부여한다. 지도 밖은
이 분류의 장애물 증거가 아니며, 실제 경로·최종 명령에서는 계속 통행 불가로 검사한다.
이동 경로의 종단은 도로 곡률에 맞는 실제 primitive 상태로 끝날 수 있다. 곡선 목표에도
조향 0의 직선 꼬리를 붙이지 않는다.

`lp_path.goal`은 현재 실행 경로의 목표이고 `merge_goal`은 지속할 우회 의도의 재합류 목표다.
남은 경로가 lookahead·반응/제동 여유에 가까워지면 앞선 국소 경로를 계산한다. 그동안 원 우회
방향·재합류 목표는 보존하며, 실제 도로 기준 위치·방향·조향과 관측 오차를 확인해야 우회를
종결한다. 계획 종단 방향 오차와 실제 추종·관측 오차도 기존 전체 허용각 안에서 함께 확인한다.
새 경로가 실패하면 기존 경로의 유효 구간만 사용하고 현재 명령의 반응·제동 검사는 유지한다.

재합류 목표의 최근접 투영이 반환돼도 실제 같은 도로에 속하는지 확인한 뒤 전방 거리로 쓴다.
목표 구간을 지났다는 근거는 같은 maneuver·reference revision에 결합해 불변 path와 인계
사본에 보존한다. 이후 목표가 이동 지도에서 잘려도 현재 도로에 실제 복귀하면 정상 종결할 수
있다. 같은 합류 목표·유효 문맥에서 우회 방향이 바뀌어도 관측한 통과 근거는 보존한다.
방향 변경의 maneuver revision 증가와 현재 실제 복귀 검사는 유지한다.
근거 부재·도로 revision 변경·조회 실패를 통과나 복귀 완료로 바꾸지 않는다.

`lattice_app.c`는 완전 path 적용 후 현재 target의 실제 tracker 계산을 수행한다.
root binding의 AUTO 모델은 `stateful`, MANUAL은 `stateless`다. Standby의 자격·사본 적용은
선택된 앱의 실제 progress나 출력 허가가 아니다. AUTO instance 교체 때 기존 계산과 planner를
취소하고 실제 join·참조 반환을 확인한다. MANUAL과 입력 소유자의 수명은 유지한다.

wire·좌표·시각·크기·생성 도구는 [drive-world-1](../../../docs/contracts/drive-world/README.md),
설계·외부 원리 대응은 [주행 설계](../../../docs/platform/drive-pipeline-architecture.md#s22),
실제 통과/실패·빌드·통합 수준은 [현재 보고](../../../docs/verification/recovery-scenario-closure-20261030.md#history-driving-app)를 따른다.
프로필 수치·시험 지도·가상 장치는 운영값이나 실제 Jetson/ESP32 완성품이 아니다.

아래의 기본 어댑터 설명은 기존 프로필의 제공 경계다. 신규 지도형 계산의 실제 제공 위치는 위와 같다.

## 계산과 앱 교체 경계

```text
실행별 Bootstrap → netfront/Store/협조 종료 runtime
                 ├─ main: 고정 20ms release → 입력 사본 제출 / 결과·현재 문맥 확인
                 ├─ AUTO 계산 스레드 → AUTO 어댑터 → 원 입력·실제 완료·결과
                 ├─ MANUAL 계산 스레드 → MANUAL 어댑터 → 원 입력·실제 완료·결과
                 ├─ v1 owner: control·수동 입력·인지 구독, 최신 명령 + 보고 칸 4개
                 └─ 별도 v2 Publisher: Diagnostics Core의 실제 관측값
```

`zephyr/src/adapter.h`의 `drive_adapter`가 계산·실제 완료 순번·입력 유효성·상태 준비·
유한한 출력값 검증을 맡는다. 기존 두 계산은 상태 없는 어댑터로 선언한다. 상태 있는
앱으로 교체할 때는 `requires_state_sync=true`, `state_ready=false`로 시작하고 앱이
warm-up·동기화를 확인해야 한다. 함수가 반환됐다는 사실만으로 READY가 되지 않는다.
상태 있는 앱은 `preparation.h`의 완전한 준비 근거와 현재 수신 문맥도 제공한다.
`state_ready=true`만으로는 READY가 되지 않는다. 준비 작업자는 자기 작업 기록을
갱신하고 완성 사본을 불변 객체로 게시한다. 어댑터가 참조하는 사본·문맥은 실제 worker
join까지 유효해야 하며 작업 중인 기록을 직접 공유하거나 취소 직후 재사용하지 않는다.
계산 오류는 진행 순번을 올리지 않으며, 잘못된 출력은 0으로 안전 처리하고 NOT_READY다.
입력 부족·만료는 계산 실패와 별개다. 안전 정지 입력(`keys: []`, 인지 `stop`)도
형식·신선도가 유효하면 기능 준비와 구분할 수 있다. 입력을 가짜로 만들어 준비시키지 않는다.

`workers.c`는 계산별로 실행 중 사본과 최신 대기 사본, 결과 칸을 소유한다. AUTO callback이
늦어도 MANUAL이나 main이 그 완료를 기다리지 않는다. 대기 중인 여러 release는 최신 작업
하나로 교체하며 놓친 계산을 몰아서 실행하지 않는다. 결과에는 원 입력의 수신 시각·seq,
control/connection revision, 실제 시작·완료가 남는다. 현재 문맥과 다른 결과는 사용하지 않는다.

앱별 `progress_seq`는 각 어댑터의 실제 진행이다. 공통 `loop_seq`·지터·elapsed는 **현재 선택된
계산의 실제 시작·완료**를 관측하며, verified의 SAFE_STOP에서는 실제 MANUAL 계산을 관측한다. main의
poll·작업 제출·송신·WAITING 상태가 순번을 증가시키지 않는다. warm의 SAFE_STOP은 계산을
제출하지 않으므로 공통 loop_seq도 증가하지 않는다. 이전 직렬 두 계산 합계와
동일한 성능 표본으로 비교하지 않는다. 기존 standalone 앱의 직렬 hook은 그대로 유지한다.

### 미준비 0 명령의 반복 발행

`WARM && LATTICE` 조합에서만 `controller.c`의 `drive_command_attempt_due()`를 사용한다.
제한 사유가 있는 속도·조향 모두 0인 결과의 첫 명령은 즉시 시도한다. 같은 제한이 계속되면
앱별 기존 `app.lifecycle.check_seq` 검사당 한 번만 명령 JSON 생성·mailbox 제출을 시도한다.
사유·control/connection revision·generation·mode·앱 instance·worker lifetime이 바뀌면 즉시
다시 시도한다. 원 감독은 정기 검사 외에도 상태 변경 때 check를 증가시키므로 고정 10Hz
송신 보장이 아니다. 새 타이머·명령 기한·전역 잠금은 추가하지 않는다.

gate가 보관하는 것은 **제출 시도 이력**이다. 기존 `void xd_command()`의 mailbox 수락,
실제 송신·장치 적용을 성공으로 기록하지 않는다. 첫 offer가 거절되거나 수락 뒤 후보가
만료·폐기돼도 다음 실제 감독 check에서 재시도한다. 제한이 없는 결과는 정상 중립 0을
포함해 매 완료마다 원 경로를 사용하고 gate를 해제한다. nonzero·NaN/Inf·빈/너무 긴 사유·
불완전 문맥은 cache하지 않고 기존 검증 경로로 전달한다. 기존 warm 비지도형·verified·
legacy 프로필의 명령 경로는 바꾸지 않는다.

선택된 앱의 실제 검사·계산 작업과 완료/progress·health·heartbeat·lifecycle은 그대로다.
입력 부족에 대한 안전 정지 결과 완료는 planner·tracker 성공이나 READY를 뜻하지 않는다.
Gateway는 현재 준비·출력 허가를 별도로 확인하고 STOP을 소유한다. 게스트 control에는
`output_permitted`가 없으므로 이를 추측해 출력 없는 활성화 계산을 중단하지 않는다.

고정 metadata는 앱당 152B, AUTO/MANUAL 합계 304B이며 ARM ELF의 `command_gate`도 304B다.
[controller 회귀](tests/controller.c)를 포함한 ASan/UBSan CTest 6개와
[실제 main·JSON encoder 집중 회귀](tests/test_command_attempt_main.py) 3개가 통과했다.
같은 60회 완료 fixture에서 제한 0 시도는 40→10, 정상 중립·이동은 각각 10→10이었고,
heartbeat 2회·lifecycle 24회와 첫 정지·정상 회복 뒤 첫 재제한 정지의 즉시성을 보존했다.
RTOS 시각·계산/전송 소유자·lattice는 이 집중 시험의 외부 대역이다. 이 계수를 실보드
처리율·50Hz 기한 보장·실제 장치 정지 수락으로 확대하지 않는다.

계산 스레드는 취소 요청을 확인한 뒤 callback이 반환해야 종료할 수 있다. 반환하지 않는
callback은 원 종료 기한에서 실패·자원 잔존으로 남기며 thread abort나 stack 재사용으로
숨기지 않는다. 계산 소유자 join 실패는 기존 shared runtime 정리 전에 전달한다. 우선순위와
8KiB씩의 계산 stack은 현재 알고리즘용 초기 구성이다. 실제 Xen의 실행 지연·전체 call chain
stack peak·미래 planner의 자원 수락을 뜻하지 않는다. 같은 주소 공간의 메모리 손상 격리는 없다.

기본 `verified-drive-1`은 두 앱의 실제 계산으로 준비를 보고한다. 명시적인
`warm-operations` / `warm-experiment`는 `warm-drive-1` 계약을 사용한다. 비선택 앱과
Standby는 계산을 제출하지 않고 입력·설정·소유 상태의 제한된 검사로 대기 자격을 보고한다.
실제 계산·활성화·출력 허가는 별도다. 입력이 없으면 자격을 만들지 않는다.
필드·enum·시각·거절 조건의 원문은 [MQTT 계약 6.3절](../../../docs/contracts/mqtt-contract-v1.md#63-대기활성화-보고-applifecycle)이다.

`drive_adapter.qualify`는 불변 설정·입력의 제한된 검사이며 계산이나 I/O를 실행하지 않는다.
상태 있는 앱은 준비 조건을 이 연결부에 구현해야 한다. qualifier가 없거나 상태 준비가
끝나지 않으면 STATE_PREPARING이다. 기존 프로필의 두 앱은 현재 입력으로 준비 가능한 상태 없는
구현이며, 그 기반 시험을 신규 지도형 프로필 전체의 수락으로 확대하지 않는다.

상태 준비의 공통 검사는 원 요청·수신 execution/instance·frame/설정/maneuver 문맥,
완전한 상태 revision과 재구축/인계 출처를 보존한다. 인계 사본은 수락 당시의 원 VM·
execution/instance·generation도 보존한다. 이 필드만으로 출처가 인증되는 것은 아니며
향후 실제 상태 수신부가 먼저 신뢰된 출처·완전성·기능적 호환성을 확인해야 한다.
사본 적용 완료 뒤 정상 pose/입력 갱신은 어댑터가 제한된 비용으로 재검증한다.
준비 완료 기한을 지도/경로 사본의 일괄 TTL로 쓰지 않는다. 신규 지도형 프로필의 실제 상태
전송·참조 교환·planner는 위 모듈에 연결한다. Gateway는 root가 명시한 모델과 G2 적용 근거를
확인하며 상태 있는 앱을 일반 QUALIFIED로 대신 승인하지 않는다.

선택형 [세션 입력](../../../docs/contracts/mqtt-contract-v1.md#71-명시적-세션-입력-manual-session-1)은
원 sample/action·세션 유효성·원 나이를 C/Python에서 검사한다. 새 control의
`manual_policy`/`manual_session`을 받은 계산 소유자가 전환별 중립/새 조작 순서를
관리한다. 정상 중립은 READY·0 출력일 수 있으며 과거 키 재발행은 새 조작이 아니다.
v1 수신 소유자는 원 sample의 중복·역전을 latest 교체 전에 거절하므로 새 봉투가
원 수신 시각을 갱신하지 않는다. 수락한 중립 근거는 최신 입력과 별도로 보존해 실제
계산에 전달하고, 현재 manual 앱 instance·generation·session이 맞을 때만 사용한다.
이 사본은 계산 진행·준비 완료·출력 허가를 만들지 않는다. 보완 근거와 로컬/제품 수락의
구분은 [정합성 검토](../../../docs/verification/zephyr-drive-foundation-review-20261030.md)의 R02·R03을 따른다.
같은 MANUAL 승계에는 현재 유효한 누른 키를 처리할 수 있다. 새 session metadata가
없는 keys-only 입력은 이 선택 계약의 증거가 아니다. 현재 Spring 생산 경로의 연동은
담당자 작업으로 남아 있다. 기존 keys-only 사용 경로는 별도로 유지한다.

warm에서 root가 같은 실행의 Bootstrap 앱 instance를 바꾸면 해당 앱의 결과 사용과
새 제출을 먼저 철회한다. 무대기 join 관측으로 실제 종료한 뒤에만 어댑터·thread/stack을
재사용한다. 원 500ms 앱 정리 허용량을 재발급하지 않고 잔존이면 재사용을 막는다.
이는 시험 설정 `CONFIG_XENOVA_DRIVE_APP_STOP_MS`이며 VM 종료 예산이나 자동 재시작
정책이 아니다. AUTO 정리 중 MANUAL·통신·감독은 계속 진행한다. 같은 root 요청의 재시도는
같은 instance를 사용한다. 생성 실패나 잔존을 새 준비로 숨기지 않는다.
생성 실패 뒤 같은 root 요청으로 자동 재시도하지 않으며, 운영자가 새 instance 변경을
명시적으로 요청한 경우에만 새 시도를 수락한다. 종료가 확인되지 않은 stack은 계속 보존한다.

Bootstrap 갱신은 별도 `drive-context` owner가 기존 Store receiver로 읽는다. 계산·v1 packet
owner는 메모리 사본만 읽는다. 현재 설정은 100ms refresh·호출 예산 50ms·사본 300ms이며
오류/만료에서 새 출처를 승인하지 않는다. guest boot·execution·transport 신원이나 계정이
바뀐 응답은 거절한다. 종료 시 기존 guest cleanup deadline으로 이 owner도 join하며,
미종료 상태에서 netfront/Store를 해제하지 않는다.

고정 release origin, 놓친 계산을 몰아서 실행하지 않는 정책, 원 입력 수신 시각,
200ms 수동 입력·300ms 인지 만료, 명령 100ms 신선도를 유지한다. F01의 창 확정/송신
확정 분리, 최신 후보/in-flight 분리, 사건과 보고의 공정성, `TCP_NODELAY=1`,
부분 초기화·공통 종료 deadline·worker join·미회수 grant 재사용 금지도 유지한다.

## 모드·VM·출력 허가

Gateway 기본 `verified`와 명시적 `--profile warm`은 영구 제어 원본과 신뢰된 실행 바인딩을 필수로 사용한다.
구형 분리 앱은 명시적 `--profile legacy`이며 공통 A/B의 준비·신원 수락으로 간주하지 않는다.

* AUTO↔MANUAL은 같은 VM 실행에서 계산 모드와 generation만 바꾼다.
* 인지 입력이 부적합하고 같은 VM의 수동 준비가 3초 유지됐다면 MANUAL로 전환한다.
  MANUAL 중 자율 준비가 3초 유지되면 AUTO_READY다. 이때 출력은 수동이며,
  현재 문맥의 명시적 복귀 요청·3초 카운트다운·새 제어 적용을 거쳐 AUTO가 된다.
* A/B 승계는 현재 모드에 준비된 다른 실행을 고른다. 없으면 SAFE_STOP이다.
  SAFE_STOP에서도 출력 없이 준비 보고를 받고 다른 VM을 선택할 수 있다.
  이 경우 MANUAL 선택은 disarmed이며 재무장 전 주행을 허가하지 않는다.
* 새 제어는 저장 → 비동기 발행/PUBACK → 앱 적용 확인 순서다. 전환 적용 기한은
  발급 때 고정한 **1초**다. 100ms 명령 신선도, 3초 준비 유지·재개, VM 복구 예산과
  다른 값이며 정상 제어 왕복을 확인하는 제한 예산이다. 재전달로 연장하지 않는다.
  PUBACK·적용 미확인 시 FAILED와 이유를 보존하고 STOP을 유지한다. 자동 세대 폭증이나
  앱 준비 실패만을 이유로 한 VM 재시작은 하지 않는다. 신선한 준비와 명시적 재무장으로
  새 전환을 시작하거나, 저장/retained 충돌이면 원본을 확인하고 운영자가 조정한다.
* 복구한 A가 준비돼도 B의 Active를 빼앗지 않는다. 이후 B 장애 시 준비된 A도
  같은 규칙으로 승계한다. 앱 전체 메모리 복제는 지원 범위가 아니다.

## 영구 원본과 설치

[Gateway 운영 절차](../../services/gateway/README.md#verified-프로필-운영)를 따른다.
원본은 `/srv/xenova/drive-control/control.json`, 초기화 표지는 같은 디렉터리의
`initialized`다. 디렉터리 0700·파일 0600이며 실제 데이터 마운트가 필수다.
기존 플랫폼의 ext4·초기화 표지·최초 설치 완료 검사를 재사용한다. 단순 bind mount를
데이터 준비 완료로 취급하지 않는다. 저장 결과가 불명확해지면 현재 프로세스의 후속
쓰기를 차단하여, 이미 replace된 새 세대를 옛 메모리 사본으로 되돌리지 않는다.
retained control은 배포 사본이다. 원본이 없거나 손상됐을 때 generation 1을 추측하지 않는다.
최초 설치를 확인한 운영자만 `--initialize-control-state`를 한 번 실행한다.
재설치·상태 복원 시에는 같은 보드의 원본과 표지를 함께 보존하고 더 최신인 retained와
충돌하지 않는지 확인한다. 세대는 uint32이며 소진 시 초기화·wrap 없이 차단한다.

Gateway/dom0 재시작은 마지막 모드·선호 owner를 복원하지만 출력은 금지한다.
현재 실행·제어·준비 유지·문맥에 결합한 새 요청·카운트다운을 다시 확인한다.
단일 dom0의 모든 Gateway 진입은 `/run/lock/xenova-drive-gateway.lock`을 먼저 획득한다.
다른 root 도구나 외부 장치 제어까지 원자적으로 통제한다는 뜻은 아니다.

## 실행 바인딩과 자동 복구

기존 Recovery Worker 안의 `RejoinCoordinator`를 선택적으로 활성화한다.
예시는 `platform/services/dom0-manager/deploy/drive-rejoin.example.json`이다.
등록된 두 UUID와 각 VM의 **별도 v1/v2** private credentials 파일 경로를 운영자가 설정한다.
공개 이미지·Git에 인증정보를 넣지 않는다. 설정 파일은 root 전용 0600으로 저장하고
기존 recovery-worker 환경 파일에 `XENOVA_RECOVERY_DRIVE_CONFIG=<설정 파일>`을 지정한다.
새 daemon, 상위 HTTP API 또는 별도 VM 재시작기는 추가하지 않는다.

```text
기존 Engine / recover_once → 원 attempt journal의 platform COMPLETED
 → 새 execution 관측 → v2 Bootstrap receipt → 같은 boot의 별도 v1 receipt
 → root 바인딩 → 현재 제어 적용·진행·입력/상태 준비 → Standby
```

플랫폼 COMPLETED는 새 Xen 실행 관측이라는 기존 뜻이다. rejoin의 PROVISIONING,
PROVISIONED, PREPARING, READY_OBSERVED 및 warm의 QUALIFIED_OBSERVED는 별도 상태다. 이 관측은 출력 권한이나
지속 보장도 아니다. 보고가 만료되면 PREPARING으로 돌아간다.

`xenova_drive_binding.observe()`는 기존 Execution의 UUID/name/domid/xen_unique_id 확인과
짧은 admission 잠금을 재사용한다. 전체 관측 예산 2초, VM별 별도 작업 슬롯으로
다른 VM의 긴 물리 실행 잠금 때문에 정상 VM의 갱신이 막히지 않게 한다. Gateway는
메시지마다 xl/Manager를 호출하지 않고 root 파일을 100ms마다 읽는 메모리 사본을 쓴다.
관측은 VM별 1초 간격, 같은 host_boot_id의 3초 lease다. stopped/absent/unavailable/
provisioning을 구별하며 실패 시 과거 신원으로 새 세션을 승인하지 않는다.
이는 관측 시점의 보장이다. 외부 root의 변경과 원자적이라고 주장하지 않는다.

적용 receipt의 uncertain은 자동 재주입하지 않는다. 원 실행·Store·receipt를 확인한다.
Store C 호출이 외부에서 정체하면 작업이 남을 수 있으며 이를 join/적용 완료로 보고하지
않는다. 바인딩 만료는 출력을 막는다. Worker 종료 상한과 실제 Store 장애는 실보드에서
추가 확인해야 한다. 응답을 잃은 복구는 같은 attempt·원 deadline의 journal을 먼저
확인한다. 원 실행 기한 뒤 기존 verify 예산까지 확인이 안 되면 final
RECONCILE_REQUIRED로 운영자에게 넘기며 새 force나 재시작 시도를 발급하지 않는다.

## 발행 출처와 소비자 이행

### warm 구성과 앱별 재초기화

warm 펌웨어에는 Gateway `--profile warm`, Worker 설정의
`"readiness_profile": "warm-drive-1"`, 최초 drive provisioning의
`--drive-profile warm-drive-1`이 함께 필요하다. 기존 적용 receipt의 프로필을 몰래 바꾸지
않는다. v2 receipt·guest boot·drive transport와 앱별 두 instance는 별개다.
기본 example 설정은 verified이며 warm을 자동으로 켜지 않는다.

앱별 재초기화는 기존 root provisioning 도구의 명시적 요청이다. 아래 값은 현재 실행과
root 전용 receipt에서 확인한다. 인증정보 파일의 내용은 출력하거나 명령행에 넣지 않는다.

```sh
python3 /usr/lib/xenova/provision_xenova_telemetry.py \
  --channel drive --vm-id "$VM_UUID" --credentials-file "$DRIVE_CREDENTIALS" \
  --receipt "$DRIVE_RECEIPT" --expected-host-boot-id "$HOST_BOOT_ID" \
  --expected-execution-id "$EXECUTION_ID" --reinitialize-app autonomous \
  --expected-app-instance "$OLD_APP_INSTANCE" --app-request-id "$REQUEST_UUID"
```

이는 계획 조회다. 확인한 같은 명령에 `--execute`를 추가하면 receipt 잠금 아래 원 instance를
대조하고 대상 앱의 새 instance를 한 번만 발급·저장한 뒤 Store에 적용한다. 응답을 잃으면
**같은 REQUEST_UUID·OLD_APP_INSTANCE·receipt·execution**으로 재전달한다. 다른 key로
불확실한 결과를 우회하지 않는다. uncertain은 기존 `--reconcile`의 관측 경로로 확인하며
재확인은 새 Store 쓰기나 앱 재시작을 수행하지 않는다.

`configured=true`는 receipt/Store 단계의 완료다. 게스트의 실제 join·새 앱 instance 보고와
현재 제어 준비를 별도로 확인해야 한다. root 바인딩 갱신과 Gateway의 현재 instance·신선한
app.lifecycle로 확인하며, 옛 신원 보고나 heartbeat 복귀를 완료 근거로 삼지 않는다.
다른 앱·UUID·실행·인증정보·CPU·Authority generation은 유지한다. 자동 복구 정책·상태 있는
앱의 warm-up/상태 사본·운영 HTTP 연결·실제 차량 수락은 이 수동 경로에 포함되지 않는다.
앱 하나의 Store 갱신이 미확정이면 원래 적용된 앱별 신원을 대조해 대상만 차단한다.
검증 가능한 변경 전 근거가 있을 때 영향 없는 앱의 바인딩은 유지한다. 최초 공급이나
기록 손상까지 정상으로 추측하는 예외가 아니며, 대상 앱은 재확인 전 승격하지 않는다.

### 공통 출처와 외부 소비자

verified 앱은 `xenova/v1/vms/{vm_id}/apps/{app_id}/events`에 발행한다.
username은 VM UUID이며 자기 두 앱 경로만 쓰도록 ACL을 제한한다. Gateway·Worker는
토픽/봉투의 VM·앱과 신뢰된 현재 boot/instance를 함께 검사한다. Legacy events와
retained READY는 우회 준비 근거가 아니다. v2 namespace·의미는 그대로다.
`platform/deploy/mosquitto/etc/acl`은 이 경로와 backend의 console/resume 권한을 정의한다.
현재 Mosquitto bbappend는 daemon 자동 활성화를 끄고 있으며 이 ACL·계정을 자동
배포하지 않는다. 운영 broker 설정/계정 반영은 별도 배포 검토가 필요하다.

현재 제품 Spring의 `ManualInputRelay`는 MANUAL일 때 눌린 키만 반복한다.
verified 프로필의 정지/Standby 준비에 필요한 실제 입력 또는 신선한 중립 입력 공급은
아직 연결되지 않았다. 출력 권한을 주어 입력을 얻거나 캐시로 READY를 만들지 않는다.
Spring/화면 담당자는 VM별 토픽 구독, `expected_generation` 및 기존 envelope의
`causation_id`를 이용한 재개 요청, 출력 권한과 표시 모드의 구분을 연결해야 한다.
이 연결 전에는 제품 HTTP/UI 종단 수락으로 표시하지 않는다.

## 빌드·시험·이미지 편입

기본 `--profile operations`는 원자료 recorder와 요청형 상세 stack scan을 제외한다.
CPU 계상·heap/pool·기한·오류와 실제 계산 진행, Publisher·협조 종료는 유지한다.
원자료 시험은 `--profile experiment`를 명시한다. profile과 실제 `.config` 해시는 빌드
영수증에 들어가며 두 구성의 firmware/source digest를 구별한다. recorder가 없는 구성의
`capture/records/transmissions`는 `-ENOTSUP`이며 빈 원자료 성공으로 반환하지 않는다.

warm은 각각 `--profile warm-operations`, `--profile warm-experiment`로 선택한다.
이 네 가지 빌드 프로필은 같은 소스의 다른 구성이고, A/B에는 선택한 **하나의 동일 번들**을
사용한다. warm 프로필의 새 계약·runtime을 기존 verified 설치본에 자동 혼용하지 않는다.

```bash
cmake -S platform/apps/drive/tests -B "$HOST_BUILD" -GNinja
cmake --build "$HOST_BUILD"
ctest --test-dir "$HOST_BUILD" --output-on-failure
(cd platform/services/gateway && python3 -m pytest)
(cd platform/services/dom0-manager && python3 -m pytest)
python3 platform/scripts/build-xenova-drive-app.py --app drive \
  --profile operations \
  --zephyr-source "$ZEPHYR_SOURCE" --tools "$ZEPHYR_TOOLS" \
  --output "$NEW_OUTPUT" --execute
```

고정 소스·SDK, 입력 해시·ELF/DTS·스택 감사·bundle receipt를 보관한다.
A/B별 다른 바이너리를 만들지 않고 같은 최종 번들 해시를 각 독립 자산에 연결한다.
Factory UUID·자산·CPU 정책과 인증정보는 기존 설치 계약으로 별도 유지한다.
소스/ARM 번들 성공과 seed·이미지 포함 확인은 다르다. 최종 이미지 조립은 별도 단계다.

`tests/run_native_pair.py`는 같은 native_sim 파일 두 개, 실제 격리 broker/ACL,
Gateway·독립 v1/v2 구독기·실제 serial encoder와 file sink로 대칭 시나리오를 검사한다.
준비·바인딩은 명시적인 native 시험 fixture다. 실제 Engine→Manager→provisioning
연결은 `dom0-manager/tests/test_drive_rejoin.py`에서 따로 검사한다. 시리얼 제출 기록은
VM/boot/instance/message_id/실행/app/세대와 프레임을 연결한다. 펌웨어 message_id는
세션 내부 카운터이므로 전역 message_id 하나만으로 명령을 대조하지 않는다.

native/ARM/실보드/이미지/차량 성능 수락을 구별한다. 특정 후보·실패·해시는
[검증 기록](../../../docs/verification/common-drive-integration-20261026.md)에 둔다.
`run_lattice_pair.py`의 PTY 가상 차량도 probe를 해석한 뒤 실제 단조 시각으로 물리·encoder 상태를
갱신하고 그 측정의 시각·값을 함께 보낸다. 한 read의 여러 probe에 같은 시각/새 순번을 붙이지 않으며,
coarse clock에서 새 시각을 구별하지 못하면 가짜 표본을 만들지 않는다. [sampling 회귀](tests/test_virtual_vehicle_sampling.py)는
실제 PTY에서 이 경계를 검사한다. 가상 장치의 계약 수락을 실제 ESP32·차량 측정으로 확대하지 않는다.
독립 실행·원자료 회수·구성 분리의 로컬/ARM 수락과 남은 범위는
[기반 검증 기록](../../../docs/verification/zephyr-drive-foundation-20261029.md)을 따른다.

### 입력 clock 진단의 명시 시험 빌드

`CONFIG_XENOVA_DRIVE_INPUT_DIAGNOSTICS`는 기본 off다. 고정 SDK의 deferred logger와
비차단 allocation을 요구한다. clock 최대16건·map 최대8건, 차량 입력은 서로 다른 거절/채택
단계의 최초1건씩 총16건, 실제 pending map 처리 단계는 단계별2건·총8건의 숫자 metadata만 시도한다.
기존 logger/shell 버퍼와 worker를 사용한다. log filter·overflow가 있어도 시도 quota는
먼저 소비하며, 누락을 성공이나 오류0으로 해석하지 않는다. MQTT2.1·drive-world wire는 바꾸지 않는다.

`XD_CLOCK_DIAG`의 `g0/g3`는 guest µs, `p1/p2`는 Pi µs다. `g3`는 callback 진입,
`dec`는 lock 해제 뒤 진단 시각이다. `crc/drc`는 각각 그 시계 읽기의 반환값이며 CRC checksum이 아니다.
`phase=1`의 신원/원 probe 거절에서는 네 시각이 같은 유효 교환이라고 추정하지 않는다.

| marker | phase | 의미 |
|---|---|---|
| CLOCK | 1/6/7 | 원 probe·신원·시간순서 거절 / entry clock 실패 / context 조회 실패 |
| CLOCK | 2/3/4/5 | 기존 오차 구간 거절 / context lock busy / 현재 Gateway 문맥 변경 / clock 명세 채택 |
| MAP | 1/2/3/4 | clock gate / trust 교체 / de_world 처리 / callback 성공 |

MAP의 `rc`는 해당 단계의 원 errno 또는 도메인 enum이고 `ret`는 기존 callback 반환값이다.
callback 성공은 실제 계산·READY·출력 허가를 뜻하지 않는다. CLOCK의 offset/lease는 원 `g3`를 쓰고,
현재 사용 시 `dl_clock_get()` guard를 다시 확인한다. 진단 옵션은 이 판정이나 한도를 변경하지 않는다.
실제 callback·nanopb 경계 회귀는 [test_input_clock.py](tests/test_input_clock.py)에 있으며,
실제 logger 포화·콘솔 수집·보드 계산 수락과 구분한다.

`XD_VEHICLE_DIAG`의 `sv/nv`는 decode된 표본과 mapped now의 존재를 구분한다. false일 때
숫자 0을 실제 측정값으로 쓰지 않는다. `err`는 mapping 폭을 더하기 전 원 clock error(µs)다.
`XD_RECEIVE_DIAG`는 실제 pending 처리의 원 rc·world 사유·published 누계를 분리한다.
map 슬롯의 high-water 증가는 decode 전 예약만으로도 생기므로 게시·유효 path·READY 증거를
대신하지 않는다. [실제 C 입력 회귀](tests/test_input_vehicle_diag.py)가 이 구분을 고정한다.

`XD_PREP_DIAG`는 planner·준비 검사의 서로 다른 단계마다 최초 1건, 부팅당 최대16건이다.
검색 시작/종결과 clock·tracker·문맥·절대 만료·발신 대기의 원 반환을 구분한다. `p`는
Pi 시각1/guest 시각2/proof4/path·revision8/sequence16/만료32/search 사유64의 존재 비트다.
비트가 없는 숫자 0은 관측값이 아니다. 시각은 각 clock의 µs이며 두 clock의 raw 값을 직접 빼지 않는다.
`XD_APPLIED_DIAG`는 enqueue와 link owner가 공유하는 부팅당 최대8건이다. phase1/2/3/4는
각각 enqueue 거절(`-ESHUTDOWN` 또는 `-EBUSY`)/대기 등록 성공/publish 실패/publish 성공이다.
[실제 입력 소유자](zephyr/src/lattice_input.c)의 반환을 기록하며, `rc=0`도 Gateway 수신·앱 적용·READY를
뜻하지 않는다. 새 로그는 기존 잠금을 놓은 뒤 시도하고 추가 clock·Xen·Store 조회를 하지 않는다.
새 카운터는 ARM 심볼 기준12B이며 config off에는 포함하지 않는다. 전체 ELF layout 차이와
카운터 크기는 별개이고, 진단형의 실제 UART drain 비용·최대 스택 점유는 아직 측정하지 않았다.

같은 opt-in의 별도 shell 명령 `xenova_clock`은 probe의 실제 `pb_encode`·`mqtt_publish`,
state 소유자의 setup·poll·poll 간격과 다른 binary publish의 유한 metadata를 조회한다.
기존 `xenova drive` 응답에는 이 자료가 없으며, `dl_report()`는 종료·join 뒤 호출된다.
두 명령은 각각 별도로 시험해야 한다. 원 probe의 `g0`·sequence·판정·기한·poll 주기는 유지하며,
추가 진단 시계 실패는 존재 비트·최초 진단 errno에 남기고 제품 실패 사유를 바꾸지 않는다.

보관은 완료 행 최초2개·tail4개·유효 reference의 가장 큰 width2개, 총8개 슬롯이다.
미응답 probe는 누계만 증가시켜 기존 응답·발신 실패 근거를 덮지 않는다. META 누계는 부팅 수명이며
각각 atomic 관측이고, 각 행은 한 번의 소유권 시도로 복사한 뒤 출력 전에 반환한다.
전체8행이 같은 순간의 snapshot이라는 보장은 없다. `empty`·`busy`·존재 비트가 없는 값은
유효0과 다르며, width가 미관측이면 `null`이다. 같은 record가 여러 슬롯에 남을 수 있으므로
별도 probe로 중복 합산하지 않는다. 원 outgoing VM·execution·app instance만 기록하며,
현재 질의의 intent 문맥을 과거 행에 소급하지 않는다. 이 응답은 clock 채택·READY·출력 허가가 아니다.

[실제 C 회귀](tests/test_clock_transport_diagnostics.py)는 원 encode/publish 호출·errno,
불일치 응답·진단 실패 후 회복·동시 슬롯 접근·유한 보관과 flag-off 동작을 확인한다.
고정 ARM에서 행360B·8슬롯2944B·active/pending 각360B이고 shell 직접 frame은560B다.
같은 소스의 전체 입력 진단 off/on ELF 차이는 text +10,201B·data -1,992B·bss +4,103B이며,
기존 진단도 포함한 layout 차이라 새 계측만의 비용이나 전체 스택 최고값을 뜻하지 않는다.
실제 `shell_print`/serial backend는 느린 콘솔에서 대기하거나 CPU를 쓸 수 있다.
시험은 소유자 정리와 CPU4 복원 뒤 별도 console lease로 질의하며, 느린 sink의 무간섭을 보장하지 않는다.
1.0.28 이후의 실보드 질의와 TX 회수 보완1.0.30·부하용1.0.31의 제공 수준은
[최신 검증 기록](../../../docs/verification/recovery-scenario-closure-20261030.md#r65-pause-20261107)에 연결한다.
`xenova_clock` 질의·실제 점유 증거와 정상 READY·주행·입력 종료 뒤 전체 반환 수락은 구분한다.

### 조회 busy와 실제 입력 문맥 변경

coordinator는 `xd_inputs_status()`로 이번 회차의 원 snapshot 반환값을 확인한다. raw `-EBUSY`
일 때만 lattice의 파생 문맥 갱신을 건너뛰며, 그 회차 입력은 여전히 비워지고 계산·출력은 거절된다.
기존 clock 매핑은 원 lease 안에서만 남는다. 실제로 관측한 연결 해제·control 오류·Gateway 또는
앱 instance 변경은 기존 무효화 경로를 따른다. 성공한 snapshot의 `control.error=-EBUSY`를
조회 실패로 바꾸어 해석하지 않는다. getter는 coordinator 회차 전용이며 현재 연결 상태나 출력
허가를 반환하는 API가 아니다. 기존 구조체·wire는 유지하고 원 rc용 int 4B만 추가했다.

### 계산 결과의 counter/uptime 경계

`xdiag_clock_us()`의 raw counter와 `k_uptime_get()`은 단위가 다르고 같은 epoch도 보장하지 않는다.
Telemetry의 `clock_domains.same_epoch=false`를 유지하며, 관측한 차이를 고정 보정값으로 쓰지 않는다.
[`drive_sample.clock_domain`](zephyr/src/adapter.h)은 앱 내부 계산 결과의 시계를 구분한다.
기본값은 기존 uptime-ms다. lattice AUTO/MANUAL의 `valid_until_ms`와 AUTO binary 입력의
`input_received_ms`는 counter-ms이며, MANUAL 조작 입력의 수신 시각은 기존 uptime-ms다.
공개 wire·MQTT2.1·전역 clock API는 바꾸지 않는다.

[`main.c`의 `command_time()`](zephyr/src/main.c)은 명령 발행 직전에만 uptime-before-ms →
counter-us → uptime-after-ms를 인접하게 읽는다. 원 counter 만료까지 남은 시간을 ms로 내림하고
uptime-before에 더해 runtime용 절대 기한을 만든다. 이 기한이 uptime-after까지 소진됐으면 거절한다.
AUTO 입력 age는 원 counter 경과 시간을 ms로 올림하고, 시각 쌍을 읽는 동안의 지연과 ms 양자화
여유를 보수적으로 더한다. MANUAL은 bound만 변환하고 실제 입력의 uptime 수신 시각으로 age를 계산한다.
대기·스케줄 지연이 원 기한이나 lease를 늘리지 않는다.

변환은 발행용 로컬 값에만 적용한다. 원 sample과
[`drive_lattice_completed()`의 `prepared_expiry`](zephyr/src/lattice_app.c)는 raw counter를 유지하고,
준비의 현재성 검사도 원 clock·원 만료를 사용한다. runtime과 I/O owner의 현재 문맥·uptime 만료·
최종 `publish_until` 검사는 그대로다. 변환이 새 준비·권한·receipt를 만들지 않는다.
이미 만료된 결과는 기존 `xd_command()`의 `encoded=-ETIME` 경로에 제출 없는 실패로 기록하고,
앱은 후속 정상 계산을 계속 처리한다. 실제 clock 읽기 실패·역행·overflow·알 수 없는 domain은
원 errno와 종료 진단을 보존하며, 정리 실패로 최초 원인을 바꾸지 않는다. 원 시각 0을 결측으로 바꾸지 않는다.

[실제 main·입력 C 회귀](tests/test_command_attempt_main.py)를 포함한 관련 94개가 통과했다.
양·음의 epoch 차이, 0 표본, 경계 만료·지연·overflow와 만료 후 정상 회복·모드 철회를 확인했다.
RTOS 시각·외부 계산/전송 owner는 이 main 시험의 명시적 fixture다. 실제 native Zephyr workers의
수동 메모리 회귀는 대기·실행·보관 사본과 AUTO 독립 진행을 별도로 확인했다. 고정 ARM compiler의
구조체 검사에서도 sample 128B·입력 순번 offset 32B, 작업 2360B·완료 2592B를 확인했다.
1.0.18/1.0.19의 ARM 빌드·부분 포장과 이 compile/native 근거는 실보드 계산·주행·실차 수락과 구분한다.


### 일시적 문맥 조회 실패와 열린 transport

[`lattice_input.c`](zephyr/src/lattice_input.c)의 map/state 단일 소유자는 첫 `xd_context_get()`이
실패했을 때만, 회차 진입 전에 이미 열려 있고 현재도 열린 소켓의 poll을 한 번 허용한다.
다음 회차를 위해 연결을 새로 만들거나 끊어진 소켓을 재연결하지 않는다. 이 poll은 RX 전용이
아니며 기존 CONNACK·구독·keepalive도 처리할 수 있다. 콜백의 앱 자료 채택 gate는 닫힌 상태다.

문맥이 없을 때 지도·차량·probe·StateApplied를 채택하거나 pending 지도 처리를 진행하지 않는다.
정상 문맥을 다시 얻은 회차만 기존 검증과 pending 처리로 돌아간다. 실패를 성공으로 바꾸거나
원 clock lease·반영 시각·신원을 갱신하지 않는다. shutdown·close·역행·identity 변경 보호는 유지한다.

[실제 입력 소유자 회귀](tests/test_input_transport_barrier.py)와 관련111개 검사가 통과했다.
실제 C MQTT client·engine·nanopb를 사용했고 열린/닫힌 소켓·무효 자료·정상 회복을 구별했다.
ARM22/23의 직접 maps frame은 base 640→688B, 대상5함수 text는 +224B, 소유 bool은 +2B다.
이 수치는 전체 스택 최고값이나 전체 ELF layout 비용을 대신하지 않는다. 프로필의 16MiB/1vCPU,
MQTT I/O 250ms 등 기존 한도는 유지했다.

R41 보드의 같은 소스 진단형23에서 원 실행에 맞는 AUTO 명령85개 중8개가 0이 아닌 값을 발행했다.
실제 planner/tracker 결과가 발행 경계를 지난 근거이며, Gateway의 1초 적용 확인은
`CONTROL_APPLY_UNCONFIRMED`로 끝났고 출력권은 차단됐다. PTY 주행·정상 복귀·메모리 반환까지
통과한 결과는 아니다. 원 명령·기한·현재성·권한을 각각 구별한
[실보드 후속 기록](../../../docs/verification/recovery-scenario-closure-20261030.md#clock-retry-submit-20261106)을 따른다.
