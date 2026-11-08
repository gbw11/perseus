# vehicle → develop 통합 검증 — 2026-11-08

## 범위와 기준

- 브랜치: `codex/vehicle-develop-integration`. 기존 미커밋 구현은 별도 원 작업 폴더에 유지했다.
- 최초 기준: vehicle `47e5f460`, develop `ef41104a`.
- 작업 중 갱신한 최종 기준: vehicle `c4199446`, develop `22cd0225`.
- 두 기준을 모두 조상으로 포함한다. 최신 vehicle의 `vehicle/Jetson` 트리는 그대로 보존한다.
- 사용자 요청에 따라 develop 대상 MR을 만들며 squash·자동 merge를 사용하지 않는다. 실제 merge는 운영자가 수행한다.

## 충돌 해결과 호환 보완

22개 충돌 파일의 공통 schema·C/Python codec·planner·Zephyr 입력·Gateway 연결을 결합했다.
자동 병합으로 중복된 fixture 함수·mission/budget 분기·지역 선언도 정리했다.

`world.proto`·`world.options`와 생성 C/Python은 develop 원본과 byte-equivalent다.
생성물을 손으로 수정하지 않았다. vehicle의 mapper clock/source 계약·독립 world 패키지·
차선 기하·지도 저장·복원 구현과 develop의 State33·clock receipt/ACK·SceneGrant 전달·
방향 witness·command3·독립 MANUAL 연결을 함께 유지했다.

첫 원본 비교에서 기존 전진 시험은 vehicle 46 PASS, develop 37 PASS/9 FAIL이었다.
병합본에서도 같은9건이 재현돼 다음 호환 보완을 별도 커밋으로 분리했다.

- legacy0/1 NONE 전진 경로는 기존 전진 tracker를 사용한다. 종단 heading 오차 합산,
  실제 차로 복귀, 정지 경로·장애물 정지 동작을 유지한다.
- 명시 signed 검색과 lane profile은 새 segment/progress/cusp gate를 유지한다.
  legacy AUTO의 후진 State 수락 금지는 그대로다.
- 동일 pose·조향·속도0의 legacy 두 점 정지 경로만 별도로 검사한다.
- 시험용 state export의 고정 vehicle_sequence=2를 실제 관측 순번으로 교체했다.
  제품의 원 Vehicle 조회·나이·frame/Scene/mission 검사는 완화하지 않았다.
- 유효 clock payload/context에서 실제 수신 clock이 실패하면 기존 DW_TIME 사유를 보존한다.
  malformed wire/context의 기존 거절 우선순위는 유지한다.
- PreparedClock의 역전 시각 음성은 두 실제 검사 위치의 오류 문구를 모두 허용하며,
  ValueError 거절 자체는 계속 요구한다.

## 실제 검증

Windows 파일/문법 검사와 격리된 Ubuntu24.04/WSL Python3.12·실제 cc 컴파일로 확인했다.
검증 도구만 ignored build 하위에 분리했으며 프로젝트 의존성 파일·시스템 환경을 바꾸지 않았다.
protobuf6.33.6·paho2.1.0·NumPy1.26.4·OpenCV4.10.0.84는 기존 선언 버전이다.

| 범위 | 최종 PASS | 미실행 |
|---|---:|---:|
| 공통 C/Python·planner·State/Scene·native 입력/command3/clock |289|0|
| Gateway clock·Scene·임무·운영·상태 사본 |87|1|
| Jetson World 생산·차선 bound/basis·Scene freeze deadline |41|0|
| 최신 vehicle 상태·JPEG·HTTP·상주 연결 |65|0|
| 중복을 제거한 합계 |482|1|

한 번의 전체483개 실행 결과가 아니다. 기존 묶음의276 PASS/2 FAIL 중 clock 관련
영향 범위를83 PASS로 재검증했고, classname/name별 최신 결과로 결합했다.
원 실패를 PASS로 바꾸거나 subtest를 독립 메서드 수에 합산하지 않았다.

미실행1건은 `test_actual_binary_mqtt_stamps_at_publish_and_keeps_expired_source_evidence`다.
명시 native broker 또는 fixed broker/root/qemu가 제공되지 않아 시험 자체가 skip했다.
이 결과는 실제 MQTT broker·Nano/OAK·Pi domU·ESP32·트랙·목표FPS 수락이 아니다.
프론트 소스는 최신 develop을 그대로 반영했으며 별도 프론트 실행 시험은 하지 않았다.
CI 결과는 별도로 확인해야 한다.

실제 선택과 원 로그/JUnit·원본 비교는 이번 worktree의
`vehicle/Jetson/build/integration-audit/`에 보존했다. 포함 그룹은 다음과 같다.

- `verified-contracts-planner`: world/geometry/trust/maximum, lattice/engine/signed/task,
  clock receipt/mapper clock, Scene 소비, C command3/input clock/transport barrier.
- `verified-input-clock`: input clock/transport barrier, command3 completion, clock confirmation.
- `final-gateway`: guest clock/send boundary, Scene authority/runtime/revision,
  mission link/operator profile/runtime/state transfer.
- `final-jetson-producer`: world adapter/station bounds, Scene freeze deadline, road basis/roots.
- `latest-runtime-video`: runtime status/current snapshot, OAK JPEG/stream/HTTP,
  JIO publication, resident video/wiring.

추가 검사: 충돌 표식0, `git diff --check` PASS, 변경 Python의3.11 문법 검사 PASS,
최종 schema/options/generated C/Python의 develop 일치, 최신 vehicle/develop 조상 포함,
최신 vehicle 대비 Jetson 트리 차이0을 확인했다.
