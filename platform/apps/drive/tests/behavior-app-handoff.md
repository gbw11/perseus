# B08 C 앱·planner 인계

이 파일은 실행 준비 자료다. PC에서 앱·pytest·collect·native·SSH를 실행하지 않았다.
검증된 것은 C 구문/타입 검사, Python 3.11 AST, 생성물 및 원 코드 대조다.
실제 성공/성능/ARM stack 최고 사용량은 Nano의 통합 결과로 판정한다.

## 소스와 저장소

`planner/behavior.*`, `parking.*`, `lattice.*`, `engine.*`, `task_reference.*`가 알고리즘이고,
`zephyr/src/lattice_app.c`가 실제 planner/worker 완료/명령/State 접점이다.
`lifecycle.h`는 운영 `main.c`와 시험 bridge가 함께 부르는 동일 classifier다.
입력 bridge는 공통 담당의 `world/tests/input_bridge.*`와 실제 `lattice_input.c`를 사용한다.
원 B07·시험 archive·primary Pi WIP·외부 설계 결과는 수정하지 않았다.

큰 자료는 engine 및 owner별 static 저장소다. planner I/O의 Status/Facts,
첫 phase snapshot 재시도, 등록용 State는 서로 다른 소유자다. 빠른 tracker는 작은 facts
metadata를 nonblocking owner lock으로 읽고 불변 path의 phase를 사용한다.
State/ClockReply 전체를 제어 stack에 복사하지 않는다.

### 구문만으로 얻은 크기

`gcc -dumpmachine`은 `x86_64-w64-mingw32`다. 아래 값은 이 ABI의 실제 `sizeof`이며
`-fsyntax-only -Wlarger-than`의 named object 경고에서 얻었다. 링크/앱 실행은 없었다.
ARM Zephyr 및 Nano ABI 크기와 동일하다고 주장하지 않는다.

| 구조 | bytes |
|---|---:|
| drive_engine | 783408 |
| lp_search | 543448 |
| lp_path | 13088 |
| dp_state (세 개 주차 path 포함) | 41096 |
| DriveState | 12592 |
| ClockReply | 3528 |
| EpisodeStatus | 944 |
| EpisodeRequest | 1440 |
| EpisodeReceipt | 728 |
| DriveControlFacts | 440 |

추가 atomic bool 뒤에도 동일 compile-only named-object 검사로 위 값을 재확인했다.
실제 runtime `XD_BEHAVIOR_RESOURCES`는 주차 workspace, 두 State, planner I/O staging,
snapshot Status, planner stack 예약을 별도 출력한다. 기존 path pool 통계는 lease를 갖는
세 slot의 통계다. 앱 planner stack 예약8192B, input map/link stack 각각8192B이며
이 예약값과 wire 상한은 stack 최고 사용량의 근거가 아니다.

## 명시적 시험 profile과 생성물

`lattice-lab-1`은 `kind=test`다. 차체·제동·오차·clock 등의 운영 실측값이 아니다.
기존 생성기로 아래 명령을 적용한 생성물을 저장했다.

```sh
python platform/apps/drive/tools/generate_primitives.py \
  --profile platform/apps/drive/profiles/lattice-lab-1.json \
  --output platform/apps/drive/planner/generated
```

| 항목 | 값 |
|---|---|
| canonical profile SHA256 (manifest) | `a23283ac5ae8f920aa6e27ae5283caef7587dee1a3b613aab6fdec8296443957` |
| raw profile file SHA256 | `01c9d1cf2cdbded2d78bf750bf445b4a1708064d818d9b587b725024f144ba62` |
| primitive asset SHA256 | `f2eda7f978711764a76fe4c97668aa6b4d10b457eeffdc2bf533bc929e2f3a51` |
| generator SHA256 | `8d0ec62662da467831d988e9a086f25efced2d01a1d1a81a1d777f5d14a8bff4` |
| generated C SHA256 | `1dbb9107b9ee92490ad0d888dff1232d526b34e72cc6527d9962f652ed033b15` |

회피40s/6m·phase10s·progress2s, 차로 정착200ms, 최소 진전.02m를 명시했다.
주차 seed hint는 .04m/.08rad이며 terminal region과 다르다. 최소 제어속도.01m/s는
20ms·1m/s²의 시험 가속 ramp 안에 들어가도록 정한 시험값이다. 충분 clearance.08m,
순위 band.01m, 실제 정지 속도 상한.02m/s·yaw rate.04rad/s·관측500ms를 명시했다.

## 실제 앱 persistent bridge

`test_behavior_app_bridge.py::app_bridge_binary`가 Nano에서 실제 C source를 ASan/UBSan으로
빌드하는 fixture다. 자체 test가 없으므로 Gateway 통합 시험이 이 fixture를 import한다.
아래는 반환한 실행파일의 stdin 명령이다. 각 명령은 stdout `END rc guestNowUs`로 끝난다.
`guestNowUs`는 wall monotonic 기준 +1,000,000us이며 임의 전진/초기화 API는 없다.

```text
boot <VM UUID> <execution UUID> <AUTO app UUID> <Pi hostBoot UUID> <guestBoot UUID> <Gateway app UUID>
control <원 control JSON hex>
io
link <원 DriveLink protobuf hex>
world <원 WorldSnapshot protobuf hex>
vehicle <원 VehicleSample protobuf hex>
step <1..2048>
calc <해당 execution의 실제 command calculation sequence>
life
meta
quit
```

`io`는 실제 input owners, `step`은 실제 map owner→planner 한 turn→state owner를 호출한다.
`calc`는 실제 controller 선택→adapter→completion→command3 serializer를 호출한다.
`life`는 운영 main과 공유하는 실제 classifier/serializer를 호출하며 READY를 설정하지 않는다.
명령 source/sequence는 이 C process 자체와 실제 Gateway binding에 맞춰야 한다.
다른 C source의 JSON을 Python에서 현재 source로 다시 라벨링하면 이 시험의 근거가 아니다.

출력은 `WIRE <actual topic> <원 payload hex>`,
`COMMAND <원 guestUntil> <원 command3 JSON>`, `LIFECYCLE <actual JSON>`, `META <diagnostic JSON>`다.
Clock Probe→Reply raw SHA/P4→accepted_receipt를 실제 왕복시켜야 한다. 테스트는 confirmed,
offset, lease, E, synthetic State/READY를 직접 주입하지 않는다.
RTOS scheduling·인증된 전송·bootstrap·차량 plant만 명시적 software leaf다.
출력 raw bytes를 수정하지 말고 Gateway actor/issuer/OutputOwner에 넣는다.
Python Pi clock은 실제 C monotonic 진행과 같은 시간축을 사용해야 한다.

## 함께 실행할 영향 범위

새 `test_behavior_parking.py`, 실제 app/Gateway 통합, 공통 Episode hash/codec,
Gateway episode/meter/parking budget/recovery 시험을 한 묶음으로 실행한다.
signed/31cm·cusp·task reference·engine/mission·실제 completion/input/main 원 회귀도 함께 둔다.
양성은 initialNONE→BEGIN→CHANGE/PASS/REJOIN→FINISH→새 BEGIN, 빈칸 평가→예약→접근→실제
Gate 후 후진→containment/STOP/PARKED, 현재grant로 실제 B revalidate/apply/newcalc를 잇는다.
음성은 원E·누적6m·progress2s·같은phase 재계획·clock/meter gap/epoch·UNKNOWN·뒤쪽 미관측·
선택칸/goal 변경·원 STOP/source/grant 불일치·State가 먼저 수락되지 않은 새phase 출력이다.
중심 seed에서 벗어나도 contained이면 추가 correction 없이 완료하고, 반대 방향/오차 밖은 거절한다.

## 남기는 수락 경계와 한계

최초 StateCopy는 원 Vehicle sequence·pose-age·source admission을 포함한 기존 검사를 유지한다.
이미 target에 실제 적용한 local geometry의 currentGrant 재검증은 원 source State를 다시
디코드하거나 원 시각을 재발급하지 않는다. 현재 map/pose/basis/목표와 전체 경로·실제 tracker를
다시 확인하고 발행 직전 current target/grant를 비교한다. old command/E는 재사용하지 않는다.
`AppliedState.calculation_sequence`는 기존 private preparation counter이므로 worker command
counter와 비교하지 않는다. 새 B 명령은 실제 B worker 계산/현재 control에서 만든다.

FINISHED는 공통 wire에서 state1/phase5이며 초기 NONE(state1/phase0)과 구분한다.
원 core/ref/budget은 보존하되 closed 기한·meter를 이후 FOLLOW의 motion lease로 쓰지 않는다.
S19 전진 phase에는 명시적인 normal STOP wait phase가 없다. 일반 STOP 확인 대기 지원은
실제 wait 시간만 progress elapsed에서 빼며 원 phase/episode E를 늘리지 않는다.
향후 wait 등록을 추가할 때 임의 STOP heartbeat로 progress를 새로 만들면 안 된다.

차량 WAIT/Jetson RUN/A·B role의 시연 운영, parked/interrupted pose의 EXIT·도로 합류,
global one-way 시작점 복귀는 root가 별도 후속 단위로 추적한다. 이 B08 코드는 그 새 공용
permission/mission/route 계약을 추측해서 추가하지 않는다. 실장치·실시간 성능은 미검증이다.
