# 주행 입력과 실제 path 인계 — drive-world-1

이 디렉터리의 `world.proto`가 Python/Gateway와 C/Zephyr의 공통 원본이다.
운영 차량 값은 이 schema에 넣지 않는다. `lattice-lab-1`은 합성 Ackermann 차량과
시험 지도를 위한 프로필이며 실제 차량의 보정·성능·안전 수락을 대신하지 않는다.
현재 후보의 시험 수준은 [후속 보고](../../verification/recovery-scenario-closure-20261030.md#history-driving-app)에 구분한다.

## 통로와 소유자

| 통로 | 생산 → 소비 | 본문과 제한 |
|---|---|---|
| broker J `xenova/v1/world/snapshot.pb` | mapper → 각 VM의 전용 world 수신자 | `WorldSnapshot`, QoS0·non-retained·단일 PUBLISH·비압축·32KiB 이하 |
| broker P `xenova/v1/vehicle/state.pb` | Gateway의 단일 serial/odom 소유자 → 각 VM의 기존 작은 제어 수신자 | `VehicleSample`, 2KiB 이하 |
| broker P `xenova/v1/vms/<UUID>/drive/link/out` | 그 VM의 state 소유자 → Gateway binary 수신자 | `DriveLink`의 probe/snapshot/applied |
| broker P `xenova/v1/vms/<UUID>/drive/link/in` | Gateway → 그 VM의 state 소유자 | `DriveLink`의 clock/accepted |

지도와 전체 path는 G1 UDS·작은 JSON latest 슬롯에 넣지 않는다. Gateway actor는
권위와 G2 출처·적용 metadata를 소유한다. 큰 사본의 decode/encode는 binary I/O 소유자가
수행하고 actor에는 검증된 bounded 객체와 불변 bytes를 전달한다. 시리얼 writer를 추가하지 않는다.

broker J 주소·계정은 실행별 root Bootstrap의 선택적 `world_broker_ipv4`, `world_port`,
`world_username`, `world_password`, `world_profile` 다섯 필드로 함께 공급한다.
일부만 있거나 무효이면 설정 오류다. 누락 상태는 AUTO world 입력 미제공이며 MANUAL의
Pi 제어·차량 상태 연결까지 막는 전역 readiness gate가 아니다. `--drive-link`와
`--world-trust`는 Gateway의 명시 설정이며 import·패키지 설치는 연결이나 재무장이 아니다.

## 입력의 의미와 무효화

- 좌표는 metre/radian, rear axle 기준 SE(2) pose다. grid cell은 origin pose에서
  `resolution_m` 간격의 사각형이며 원점 회전도 적용한다. reference·좌/우 boundary는
  같은 frame의 순서 있는 점이다. 뒤집힌 경계·중복 segment·자기 교차를 정상 도로로 채택하지 않는다.
- 최대 grid는 64×64, road 배열은 각각 2~64점이다. occupancy는 0~100 또는 255(unknown),
  flag bit는 valid=1/static-retained=2/expired=4, age는 cell별 little-endian uint32 ms다.
  unknown/만료/무효 셀은 통행 가능으로 바꾸지 않는다. 보관 셀의 오래된 원 age와 새 관측의
  live age는 다르다. 재발행만으로 observation sequence나 원 관측 시각을 새로 만들지 않는다.
- mapper boot/instance·map/frame/odom/clock epoch와 vehicle/configuration/calibration/primitive
  문맥은 신뢰 프로필과 대조한다. `primitive`는 생성 asset SHA-256이다. 이름만 맞는
  다른 곡선·차량 모델을 받아들이지 않는다. 새 출처는 신뢰 설정의 명시 갱신이 필요하다.
- `reference_odom_seq`는 정확한 anchor 표본이다. 현재 map pose는
  `T_map_base(anchor) × inverse(T_odom_base(anchor)) × T_odom_base(current)`다.
  anchor가 이력에서 사라지면 최신 표본을 대신 끼우지 않고 미확인으로 제한한다.
  서로 다른 broker를 거쳐 map이 먼저 오면 기존 3-slot pool의 writer 하나에서 완성본을
  잠시 보유한다. 정확한 anchor가 도착할 때 원 나이·출처를 다시 확인해 게시한다.
  그동안 이전 map은 원 유효기간 안에서만 사용하며, 재파싱하거나 관측 시각을 갱신하지 않는다.
- `observed_us ≤ completed_us ≤ published_us`는 mapper 시계다. 승인된 mapper→Pi offset
  구간과 만료를 사용한다. guest→Pi 구간은 원 probe 송신·수신과 Pi 수신·송신으로
  별도 계산한다. 서로 다른 시계를 직접 빼거나 수신 시각으로 원 관측 나이를 초기화하지 않는다.
  guest→Pi 오차 구간이 좁아져 현재 시각의 상한 추정이 조금 낮아져도 이전 수신 추정치와의
  역전만으로 만료시키지 않는다. `dw_snapshot_time`이 원 관측의 Pi 하한에서 정한 절대 만료를
  현재 Pi 상한과 대조한다. 원 publication 하한보다 이른 시각·실제 만료·trust 만료는 거절하며
  동기화 갱신으로 관측 시각이나 명령 기한을 새로 만들지 않는다.
- Pi 차량 시각은 mapper trust 없이도 교환한다. 차량의 device/odom epoch·Pi session·표본
  순서·센서 품질·시각 및 pose/steering 오차를 확인한다. 지원되지 않는 측정 steering은 0으로 채우지 않는다.

모든 required field는 실제 존재해야 한다. decode 전 wire 크기와 중복 singular/oneof,
문자열·repeated 수·부분 frame을 검사하고 decode 후 유한값·배열·의미를 검사한다.
알 수 없는 optional field는 무시하지만 `required_features`가 0이 아니면 거절한다.
Python은 검사하지 않은 unknown bytes를 사본 fingerprint에 넣거나 다시 relay하지 않는다.
문자열 식별자는 공통 ASCII 범위이며 C 문자열의 NUL 뒤 suffix로 신원을 다르게 표현할 수 없다.

## 불변 자료와 계산 수명

world pool은 3개, odom 이력은 128개다. map/path의 generation과 각 reader lease는 별개이며
한 독자의 이중 반환이 다른 독자의 참조를 해제하지 않는다. 읽는 중인 slot은 덮지 않는다.
path pool도 3개이며 planner/tracker/export/준비 독자의 유한 lease를 사용한다.
BUSY는 미완료 소유를 유지한 재시도이지 완료나 자유 공간이 아니다. 취소 뒤에도 writer·reader·자식 소유자가
종료하고 참조를 반환한 다음에만 저장 공간을 재사용한다.

실제 lattice는 node/open 8192, hash 16384와 최대 probe·확장/충돌/원 기한을 제한한다.
차체 OBB·cell/도로·표본 사이 sweep·현재 steering 접속을 검사한다. tracker는 실제 target
거리로 곡률을 구하고 속도/조향/rate 제한을 적용한 최종 반응·제동 궤적을 다시 확인한다.
지도 수신·주기·타이머는 READY나 계산 progress가 아니다. 새로운 유효 경로의 채택과 실제
tracker 계산을 구분하며 selected worker의 건강/기한을 통과한 완료만 source 계산 근거로 쓴다.

## 실제 stateful 사본

`DriveState`는 최대 512개 `PathPoint`의 완전 사본과 source VM/execution/app,
당시 generation·state/path/maneuver revision·현재 frame/odom/configuration/calibration/asset,
reference·실제 계산/차량 표본·tracker index·merge goal을 함께 보낸다.
원 명령, 과거 유효기간, 출력 승인은 복제하지 않는다.

Gateway는 당시 현재 owner의 완전 사본만 G2로 채택한다. 권한 종료 뒤 늦게 도착한 source
갱신은 거절한다. 이미 유효할 때 받은 사본은 현재 호환성·target의 실제 적용을 거쳐 사용할 수 있다.
target은 전체 경로의 기구학·현재 지도·실제 tracker 계산을 검증한 뒤 `AppliedState`를 보낸다.
PUBACK·checksum·메시지 수신은 적용 근거가 아니다. 거절한 후보는 다른 유효 적용 사본을 지우지 않는다.

`application_sequence`는 target의 적용/거절 판단 순서다. 실제 `calculation_sequence`와
별개이며 뒤늦은 이전 판단이 새 거절/적용을 덮지 못한다. 관측하지 못한 값은 optional 부재로 남긴다.
rebuild와 handoff 출처를 구분하고 거절 사유 코드는 유지한다.

Gateway full bytes는 최대 4개, 기존 G2 metadata는 8개다. 현재 적용 및 전달 중인 참조를
고정하고 나머지만 교체한다. 관측 실패는 종료가 아니므로 참조를 반환하지 않는다.
확인된 stopped/absent나 새 실행/app 신원이 옛 참조를 종결한다. 참조만 보존하며 이를 새 허가로 쓰지는 않는다.

## 생성과 시험

신규 펌웨어 선택은 `warm-lattice-lab`이며 명시적인 합성 차량 시험 프로필이다.
기존 `operations`·`warm-operations`의 AUTO 계산을 몰래 바꾸지 않는다. 새 프로필의
신뢰 binding은 `app_state_models={"autonomous":"stateful","manual":"stateless"}`를 사용한다.
그 선택은 Worker의 root 제공 경로에 연결할 설치 입력이며 일반 앱 메시지가 바꿀 수 없다.
`BUILD_RECEIPT.json`은 선택한 schema·primitive hash와 이 모델을 기록한다.

큰 binary I/O는 고정 Paho 2.1 수신부의 길이 검사 경계를 시험한다. 패키지의 protobuf는
고정 Yocto 6.33.6이며 생성기는 3.20.3이다. 다른 Paho 버전에 같은 내부 경계를 보장한다고 하지 않는다.
Python 개발 경로 import와 target package 설치/import는 별도 검증이다.

고정 도구와 원문 hash는 `platform/apps/drive/world/dependencies.lock.json`에 있다.
원문을 수정하면 같은 변경 묶음에서 C/Python을 함께 생성한다.

`vectors/world-4x4.textproto`는 제품 encoder를 거치지 않고 고정 `libprotoc 3.20.3 --encode`로
만든 독립 decoder 입력이다. 같은 디렉터리의 JSON에 wire hex와 SHA-256을 고정했다.
`test_frozen_protoc_golden_to_python_and_nanopb`가 실제 Python/upb 및 nanopb의 정상 decode,
원 cell age, 재인코딩과 중복 필드 거절을 대조한다. 최대 64×64는 별도 정상/상한 시험과
두 firmware 통합 시험에서 실행한다. 작은 golden을 최대 입력이나 실제 mapper 수락으로 세지 않는다.

```sh
python3 platform/apps/drive/tools/generate_world.py --tools <frozen-tools>
python3 platform/apps/drive/tools/generate_primitives.py --help
pytest platform/apps/drive/tests/test_world.py platform/apps/drive/tests/test_lattice.py platform/apps/drive/tests/test_engine.py
```

`tests/run_lattice_pair.py --help`는 별도 network namespace·실제 두 broker·동일 firmware 두 개·
제품 Gateway·PTY feedback의 통합 시험이다. 지도와 encoder/물리 모델, root/Store는 시험 대체 경계다.
planner/tracker/충돌 검사/상태 적용은 제품 C 구현이다. 가상 장치의 진행을 실제 ESP32·실차 수락으로 확대하지 않는다.
