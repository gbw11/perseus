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
| broker P `xenova/v1/vms/<UUID>/drive/link/out` | 그 VM의 state 소유자 → Gateway binary 수신자 | `DriveLink`의 probe/snapshot/applied/budget_request |
| broker P `xenova/v1/vms/<UUID>/drive/link/in` | Gateway → 그 VM의 state 소유자 | `DriveLink`의 clock/accepted/mission_grant/budget_reply |

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

`ClockReply.pi_receive_us`는 binary 수신자의 원 수신 시각이고, `pi_send_us`는 같은 Pi 단조 시계의
Paho publish 진입 직전 시각이다. actor의 응답 준비·직렬화·mailbox 대기를 송신 시각으로 사용하지 않는다.
Gateway는 한 번 검증·직렬화한 불변 응답에서 이 uint64와 protobuf 길이만 갱신한다. probe 신원·순번,
차량 원 표본·trust 만료는 갱신하지 않는다. 이 경계는 커널 송신 완료·PUBACK·guest 수신을 뜻하지 않으며,
그 뒤 실제 전달 지연은 guest의 기존 RTT 기반 오차 검사에 남는다. 시각 갱신 실패는 해당 응답을
`CLOCK_SEND_TIME`으로 제한하고 다음 유효 응답의 정상 진행을 허용한다. wire 버전과 기존 오차 한도는 같다.

DriveLink의 같은 I/O owner는 Paho의 `socket`·`want_write`·`loop_read`·`loop_write`·`loop_misc`로
bounded select를 처리한다. `loop`/`loop_start`와 혼용하거나 자기 thread를 깨우는 socketpair를
별도로 만들지 않는다. 이전 packet이 아직 송신 대기 중이면 새 latest 슬롯을 꺼내지 않는다.
따라서 작은 mailbox의 교체가 QoS 0의 Paho 내부 무제한 대기로 옮겨 가지 않는다. 이 제한은
G1 원 작업·필수 저장을 폐기하는 정책이 아니며 clock wire·오차·lease 의미도 바꾸지 않는다.

guest의 네 번째 시각은 `link_received()` 진입에서 얻은 같은 µs 시계값이다. 전체 binary payload가
callback에 도착한 경계이며 protobuf decode·context 조회 뒤 시각이나 소켓 첫 바이트 도착 시각이 아니다.
원 lease도 이 수신 시각부터 계산한다. 이후 `dl_clock_get()`의 현재 시각·context·만료 확인은 유지한다.
transport callback의 별도 `received` 인자는 uptime **ms**이므로 이 네 시각에 섞지 않는다.

모든 required field는 실제 존재해야 한다. decode 전 wire 크기와 중복 singular/oneof,
문자열·repeated 수·부분 frame을 검사하고 decode 후 유한값·배열·의미를 검사한다.
알 수 없는 optional field는 무시하지만 지원하지 않는 `required_features` bit는 거절한다.
공용 codec은 과거 지도 의미 0·1과 lane/localization/whole-cell permission을 포함한 11,
주차 후보를 더한 15를 검사한다. codec의 형식 지원은 실제 임무 실행 지원과 별개다.
새 profile의 주행 소비자는 LANE grant를 요구하며 후진 주차 실행·전체 phase 승인은
별도 구현 대상이다. 아직 지원하지 않는 parking state를 FOLLOW로 바꾸어 승인하지 않는다.
Python은 검사하지 않은 unknown bytes를 사본 fingerprint에 넣거나 다시 relay하지 않는다.
문자열 식별자는 공통 ASCII 범위이며 C 문자열의 NUL 뒤 suffix로 신원을 다르게 표현할 수 없다.

## 도로 우선 입력 — feature bit 0

도로 우선 운용은 `WorldSnapshot.required_features=1`과 cell flag bit3(`8`)을 함께 사용한다.
bit3은 **cell 중심이 선호 도로 안에 있음**을 뜻한다. 기존 left/right boundary는 확장 통행
가능 영역의 hard 경계이며, 그 안의 도로와 여유 구역을 같은 선호도로 취급하지 않는다.
unknown/무효/만료/점유는 도로 flag가 있어도 계속 통행 불가다. feature 없이 bit3을 쓰거나
feature를 선언하고 도로 cell이 하나도 없는 입력은 거절한다. 미지원 feature bit도 거절한다.

생산자는 실제 도로 구분을 공급한다. 공통 제공물은 다음과 같다.

```python
from xenova_drive_world import mark_road, encode

# left/right는 같은 frame의 실제 선호 도로 Point 점열이다. 폭 기본값을 만들지 않는다.
mark_road(snapshot, road_left, road_right)
payload = encode(snapshot)
```

`mark_road`는 bounded scanline으로 기존 cell_flags를 채운다. 점유/유효성·age·시각·출처·
바깥 boundary는 바꾸지 않는다. 실제 지형의 통행 가능성·원 geometry 품질은 생산자의 책임이다.
0.72m 도로는 저장소 시뮬레이터 시험값이며 운영 폭으로 복사하지 않는다.

탐색은 같은 node pool/heap에서 차체와 여유가 도로 밖을 사용하는 거리를 길이·곡률 비용보다
먼저 비교한다. 기존 4096칸 유도값 작업 공간은 이 우선 비용의 2D 유도값으로 재사용한다.
차체의 inscribed disk를 반영해 중심점만 도로 안인 경우를 유도값에서 구분한다. 추가된 고정
shoulder 색인은 64개 uint64(512B)이며 node는 기존 48B 이하를 유지한다. node 수·확장·cell·
원 기한은 늘리지 않는다. 유도값과 양자화는 여전히 bounded guidance이며 전역 최적성은 주장하지 않는다.
analytic 직선 종단이 도로 밖을 가르면 그 연결을 채택하지 않고 실제 primitive 탐색을 계속한다.

`DriveState.world_features`(선택 tag 28)와 불변 path는 생성 당시 의미를 보존한다. 생략은
과거 의미 0이다. 현재 지도와 다르면 인계를 설정 불일치로 거절하고, 기존 path의 제출도
차단한다. 원 snapshot/AcceptedState와 실제 target 검증을 거친 같은 의미만 사용한다.
구형 reader는 새 feature map을 VERSION으로 거절하므로 조용한 의미 하향을 허용하지 않는다.
feature=0은 과거 hard-corridor 호환 모드이며 도로 우선 품질 수락으로 쓰지 않는다.

원 최대 wire·map/path/odom slot·배열 상한은 같다. 새 firmware와 생성 C/Python·`road.py`를
포함한 codec 및 그것을 로드한 Gateway를 같은 지원 후보로 설치한다. 기존 Gateway recipe는
공용 package의 모든 `.py`를 설치하므로 새 helper도 같은 경로로 포함한다.

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

`merge_goal`은 지속하는 우회 의도의 목표다. 실제 추종할 국소 경로가 그 지점을 지나 앞쪽으로
갱신돼도 원 목표를 새 path 끝점으로 바꾸지 않는다. 수신 앱은 실행 경로의 끝을 `path`의 마지막
자세에서 복원하고, 우회 방향·목표·maneuver revision은 별도로 유지한다. 국소 path 변경은
path revision, 우회 의도의 변경·종결은 maneuver revision으로 구분한다. 종단 위치에 가까워진
것만으로 실제 재합류나 출력 승인을 확정하지 않는다.

`merge_passed_reference_revision`은 이 maneuver의 목표 구간을 실제로 통과했음을 확인한
reference revision이다. 선택 필드(tag 27)이며 생략은 통과 근거 없음이다. 목표가 현재
reference에 실제로 속하고, 현재 위치 오차를 포함해 그 구간을 지난 경우에만 기록한다.
목표가 rolling window에서 빠져도 같은 frame·odom·설정·reference revision의 근거는 유지한다.
같은 합류 목표를 유지한 우회 방향 변경은 이 통과 사실을 무효화하지 않는다. 방향 변경의
maneuver revision은 증가시키고, 사본에는 보존한 목표·통과 근거와 현재 방향을 함께 담는다.
실제 도로 기준의 위치·방향·조향 조건까지 충족해야 재합류를 종결하며, 통과 자체는 완료나
출력 승인이 아니다. reference revision이 달라지면 기존 근거를 재사용하지 않는다.

필드가 있으면 0이 아니고, 활성 우회 방향과 사본의 reference revision에 맞아야 한다.
C/Python reader는 불일치·중복을 거절한다. 필드가 없는 과거 완전 사본은 현재 지도·기구학·
실제 계산으로 계속 검증하며 통과를 추측하지 않는다. snapshot과 AcceptedState의 같은 필드를
Gateway binary relay가 보존하고, target은 경로 채택 후에만 근거를 자기 실행에 적용한다.
다른 도로 revision에서 경로 자체가 검증돼도 과거 revision의 통과 근거는 가져오지 않는다.

새 feature11/15 profile의 `WorldSnapshot`은 major1/minor2이며,
`DriveState`·`DriveLink`는 major2/state_features7이다.
nested `VehicleSample`·`MissionGrant`는 major1을 유지한다. A/B의 `lattice`·`engine`·`lattice_app`과 C/Python 생성물·공용 codec,
이를 실제로 로드한 Gateway binary 수신 프로세스를 같은 지원 후보로 맞춘다. 옛 Gateway는
모르는 선택 필드를 버리므로 새 통과 근거를 보존하는 혼합 실행은 지원하지 않는다.
Worker는 raw 사본이나 이 필드를 직접 읽을 필요가 없다. Gateway의 출처·권위 검사는
계속 필요하며, 설치만으로 재무장하지 않는다.

공용 계약의 major2 전환은 기존 major1 runtime과의 혼합 운영을 지원하지 않는다.
Gateway의 `DriveLink` 생성·수신, Zephyr의 link/state 생성·수신 및 planner의 mission metadata를
같은 지원 후보로 전환해야 한다. 이 선행 계약만 교체한 상태는 운영 가능한 통합 구성이 아니다.
기존 `PreparedClock`과 송신 직전 시각 갱신·bounded MQTT I/O·차량 관측/진단 수명은 유지한다.

`MapperClockProbe.mapper_trust`(선택 tag13)는 Pi clock owner가 이미 수락한 원 J/Pi lease다.
초기 probe에는 없으며 현재 왕복의 Pi 수신 완료 시각이나 새 승인을 대신하지 않는다.
`mapper_clock.py`는 원 probe/lease·과거 관측 support window와 만료를 검사한다.
`dw_pool_refresh`는 같은 source의 정상 clock lease 갱신에서 held map의 관측 시각·원 만료를
갱신하거나 reader/writer를 fence하지 않는다. 다른 source/정책은 기존 source 전환과 같이 fence한다.

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
