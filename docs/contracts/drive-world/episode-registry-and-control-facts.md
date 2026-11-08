# 선택한 Episode profile과 원 물리 사실

이 계약은 B08 구현 후보이며 동적 통합·실차 수락 완료를 뜻하지 않는다. `world.proto`가 필드 원본이다.
기존 warm 승계의 유효 path/state 재검증과 같은 MANUAL SID의 연속 입력을 유지한다.

## 통로와 지원

| 위치 | 필드 | 의미 |
|---|---|---|
| ClockProbe7 | supported_state_features | 읽을 수 있는 기능 마스크. 생략은 legacy |
| DriveState34 / feature16 | episode | 완전 core/progress/원 acceptance 사본 |
| DriveLink11 | episode_request | 원 BEGIN/ADVANCE/FINISH 요청 |
| ClockReply12 | episode_status | 현재 registry와 exact 원 결과. 장치 적용이 아님 |
| ClockReply13 | control_facts | parent Vehicle에 결박한 Pi meter·일반 STOP 물리 사실 |

capability는 실제 State의 presence mask와 다르다. 인증된 현재 peer, 설치·승인 profile,
feature16 선언을 함께 확인한 경우에만 새 Episode 운행을 연다. 자기 선언은 권한이 아니다.
실제 State는 base7, Task8과 그 presence, Episode16과 그 presence를 각각 일치시킨다.
legacy peer에 새 NONE을 요구하거나, 지원 부재·유실을 NONE으로 해석하지 않는다.
feature16은 Status12와 ControlFacts13 해석을 함께 지원한다는 뜻이다.

새 mission의 실제 초기 NONE stamp를 C가 완전 State로 보내고 Gateway가 수락·target 적용한 뒤
현재 계산으로 출발한다. 초기 core는 state1/phase0와 나머지 episode 필드 zero/NIL이다.
ref는 Pi registry epoch/revision·실제 core SHA를 갖고 request/origin은 zero/NIL이다.
이는 존재하지 않는 EpisodeRequest의 성공 receipt가 아니다. `initial_none_core`와
`initial_none_snapshot`, C `dw_episode_initial_none`이 이 동일 형식을 구성한다.

## 원본과 경계

`EpisodeCore`는 원 episode/home/target/policy/blocker/basis, phase/고정 goal, 원 시각·거리 예산이다.
현재 lane을 원 home으로 덮지 않고, RETURN_HOME 후에도 원 target을 보존한다. blocker는 원 관측을
포함하는 최대8점 보수 hull이다. 미검출이나 새 path로 이를 삭제하지 않는다.
`EpisodeProgress`는 같은 core의 실제 표본에 따른 단조 meter/진전 HWM이다.
last_progress_pi_us=0은 아직 실제 진전이 없다는 뜻이다. watchdog은 이 경우 원 phase 시작을
기준으로 흐르며 재수신/ACK/heartbeat로 시작이나 기한을 갱신하지 않는다. ACTIVE의 원 start/E가
없으면 bootstrap 미준비다. FINISHED/NONE terminal은 원 SHA/예산을 보존하지만 이후 현재 path의
Task/phase와 옛 값을 같게 강제하지 않는다. ACTIVE에만 현재 Task/phase 연결을 검사한다.
`EpisodeAcceptanceRef`는 원 request SHA/nonce·원 발행자·Pi 등록판의 사실이다.

등록 ACK → C phase commit → 실제 completion → 즉시 완전 State → Gateway의 exact 수락 →
후속 fresh command 출력 순서를 따른다. 새 phase DRIVE를 제한하되 계산까지 막아 snapshot
생성과 교착시키지 않는다. phase 경계만 등록하며 같은 core의 pose마다 ACK를 요구하지 않는다.
Status의 registered/pending-state와 full-state-admitted, PUBACK, target apply, serial 제출,
device APPLIED와 actual stationary를 서로 바꾸어 사용하지 않는다.

BEGIN의 `began_guest_us`만 원 birth Guest 시각이다. ADVANCE/FINISH에는0 sentinel을 쓴다.
새 B가 A의 Guest 시각을 자기 clock으로 다시 변환하지 않는다. 이후는 accepted core의
원 Pi start/E·meter 기준을 보존하고 현재 owner의 phase 시작/완료 Guest 시각만 검증한다.
실제 phase commit은 Pi가 확정한 receipt.snapshot을 사용한다. candidate 자체는 승인이 아니다.
FINISH의 closed core는 state1(NONE)/phase5(FINISHED)이며 원 episode ID를 유지한다.
초기 NONE의 state1/phase0·NIL episode와 다르다. FINISH는 원 episode와 terminal 사실을 남긴다.
검증된 종료는 다음 BEGIN의 predecessor가 될 수 있지만
timeout/UNKNOWN을 새 NONE·새40초·새6m로 바꾸지 않는다. 정상 STOP 대기는 progress watchdog만
조건부 유예하며 원 phase/episode E는 갱신하지 않는다.

받은 path geometry는 현재 차량/문맥으로 재검증할 수 있다. 원 사본·원 grant와 현재 owner의
새 permission을 구분하고, old command/E/출력 권한을 새 identity나 시각으로 재포장하지 않는다.
같은 SID의 정상 MANUAL 승계는 새 Enter/attach를 요구하는 사건이 아니다.

## SHA의 범위

- Core: `XNVECORE1\0` + canonical protobuf Core. progress/ref/receipt는 범위 밖이다.
- Source: `XNVESRC1\0` + canonical EpisodeSourceContext. 현재 reference/world/observation revision만
  명시0으로 정규화하며 나머지 geometry identity는 그대로다. 원 basis revision/prefix는 core에 따로 고정한다.
- Request: 원 EpisodeRequest **body bytes**의 SHA다. DriveLink 외피와 known-field 재직렬화를 쓰지 않는다.
  `episode_request_body`/`budget_request_body`는 검사한 원 field11/8 body를 추출한다.
- Plan: `XNVPLAN1\0`, LE world feature, 길이u16+ASCII map/frame/odom/config/calibration/primitive/reference ID,
  u64 immutable Task.basis_revision(없으면0)/path/maneuver revisions, path count u16와 각 점의
  x/y/yaw/steering/velocity float32, gear count u8와 first/last u16·direction i8, task presence u8,
  task home/from/selected u32·changing u8·basis revision u64·prefix count u8·Point float32쌍,
  vehicle/mapper boot/instance/clock ID 순서다. 실제 State.reference_revision은 현재 witness로 보존한다.
- Selected goal: `XNVGOAL1\0`, mission UUID16/rev u64, Scene UUID16/rev u64, slot u32/rev u64,
  goal rev u64/Pose float32셋, polygon count u8/점 float32쌍, 위치/heading bound float32쌍이다.
- Basis: `XNVBASIS1\0`, count u8와 원 Point float32쌍(2~64)이다.

문자열은 길이·ASCII를, UUID/SHA는 정확16/32 bytes를 검사한다. full AcceptedState 원문 SHA는
core/request SHA와 별개다. request 안에 그 request의 receipt/hash를 넣는 순환은 없다.
hash는 구조적 동일성일 뿐 현재 충돌·출처·목표·권한 수락의 대체가 아니다.

## 원 meter와 일반 STOP

Pi meter는 accepted encoder 표본마다 wrap-aware `max(abs(deltaL),abs(deltaR))`를 누적한다.
actor가 받은 latest 두 점 차이나 전후진 상쇄로 대체하지 않는다. 값은 integer total ticks이고,
epoch/gap revision과 원 Vehicle sequence를 보존한다. parent ClockReply.vehicle의 정확한 seq로
host/device/odom/Pi session/calibration/source sample/time/error를 재사용한다. 불일치는 거절한다.
기존 anchor가 없거나 gap이 있으면 UNKNOWN/invalid이며 최신 표본으로 원 기준을 대체하지 않는다.

`metres_per_tick`은 double **보수상한 m/tick**이다. 실제 calibration 상한을 수락해야 하며
scale/calibration/연속성 변경은 새 meter epoch다. C는 정수 delta를 취하고 2^53 초과·비유한을
거절하며 곱셈도 상향 보수화한다. float32 rounddown으로 원6m 예산을 늘리지 않는다.

StopFacts는 일반 STOP의 원 Pi/device session·command seq/generation/counter·VM/source calculation,
APPLIED report/source sample/시간 구간, 그 뒤의 fresh stationary window와 양 wheel/yaw bound다.
Pi/operator STOP은 source tuple/calculation이 NIL/0이어도 원 논리 AUTO/MANUAL role을 보존한다.
role0은 NIL/0이어야 하고 VM source가 하나라도 있으면 tuple/generation/calculation이 완전해야 한다.
같은 MANUAL carry에서도 원 generation을
바꾸지 않는다. 이 facts는 cusp DirectionGateWitness의 ±1 READY나 새로운 출력 권한이 아니다.
actual stationary는 APPLIED 뒤의 유효 관측창이어야 하며 copied ACK/수신 ACK로 대신하지 않는다.

EpisodeRequest의 원 결과는 실제 후속 ClockProbe/Reply 경로로 받는다. 첫 요청 전송 뒤 기존 owner에
한 번 조기 probe hint를 주고, 요청이 pending이면 원 pose age 안의 bounded retry를 사용한다.
MANUAL probe의 주기나 원 clock/P4 확인·request E를 바꾸지 않는다. 새 계산의 즉시 State export도
같은 owner의 hint이며 별도 thread나 daemon이 아니다.

## 유한 크기와 검증 상태

| 메시지 | nanopb 생성 wire 최대 B | 정책 cap B | Windows GCC x86_64 sizeof B |
|---|---:|---:|---:|
| EpisodeCore | 457 | 768 | 376 |
| EpisodeProgress | 54 | 128 | 40 |
| EpisodeAcceptanceRef | 197 | 256 | 200 |
| EpisodeSnapshot | 716 | 1024 | 616 |
| EpisodeSourceContext | 559 | 768 | 544 |
| EpisodeRequest | 1592 | 2048 | 1440 |
| EpisodeReceipt | 829 | 1536 | 728 |
| EpisodeStatus | 1062 | 2048 | 944 |
| PiAbsoluteMeter | 62 | 128 | 64 |
| StopFacts | 413 | 768 | 352 |
| DriveControlFacts | 497 | 1024 | 440 |
| DriveState | 18145 | 32768 | 12592 |
| DriveLink | 18235 | 32768 | 12680 |
| ClockReply | 3779 | 32768 | 3528 |

State metadata(field29이상)의 생성 상한 합계2096B는 기존4096B cap 안이다. 전체 Link32768,
Task1024, path512/gear3/basis64/blocker8 상한을 유지한다. sizeof는 C `-fsyntax-only`의
compile-time 검산값이며 Nano/Zephyr ABI·stack 측정이 아니다. 큰 I/O/copy storage는 static이며
fingerprint는 스트림으로 계산해 path 크기의 stack/heap을 만들지 않는다.

고정 protoc3.20.3/nanopb0.4.9.1로 생성했고, AST311과 shared C/기존 포트 fixture의 syntax를 검사했다.
새 공통 회귀는 `world/tests/test_episode_contract.py`에 작성했으며 PC에서 실행하지 않았다.
현재 기능·실행 budget·자원 수명·C/Python SHA 일치의 동적 확인은 root의 Nano 통합 묶음에 남아 있다.
