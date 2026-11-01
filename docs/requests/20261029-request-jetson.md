# Jetson 인지·지도·영상 구현 요청

대상: Jetson 센서·인지·지도·영상 담당자. **상태: 공통 wire·Python encoder·제품 C 소비자 제공 / 실제 Jetson 제품·센서 수락 전.** 이 저장소에서 실제 Jetson 제품 소스 위치는 확인되지 않았습니다. 담당 저장소·파일·entrypoint·고정 설치 버전을 회신해 주세요. 설계의 논리 디렉터리를 실제 파일로 적지 않습니다.

## 현재 할 일과 읽는 순서

1. [주행 설계](../platform/drive-pipeline-architecture.md) 2·5~8·18~20절에서 장비·broker·바이너리·좌표/시각·책임을 읽습니다.
2. 같은 문서 10·12절의 Pi 소비·상태 인계와 [복구 시나리오](../platform/recovery-scenarios.md)의 A5·B4·H1·지도 복원 사례를 읽습니다.
3. 아래 논리 모듈을 담당 소스에 대응시키고 [제공된 공통 계약](../contracts/drive-world/README.md)의 encoder·벡터를 연결합니다.

## 담당자 작업

| 연결 ID·모듈 | 입력→출력 | 권고 구현·금지 조건 | 수락 |
|---|---|---|---|
| DEC-01·06·15, 센서/인지 | OAK-D-Lite Fixed-Focus·ToF 2개 원 표본→원 시각·진행·품질·제한된 인지 | 카메라 한 소유자, 센서별 실패·나이를 구분. 검출 confidence를 거리 오차/안전 확률로 치환하지 않음 | 실제 센서·설치 버전으로 정체/부분 실패/복귀를 주입. 합성 fixture와 실제 관측 구분 |
| DEC-04~07·ARCH-T01~09·36, mapper/encoder | 원 관측·Pi odom→완전 WorldSnapshot·차량 pose/frame 대응 | 누적 지도와 국소 영역 추출, 고정 배열/packet 상한, 바이너리 full snapshot. 좌표/odom 기준 표본·시각 품질 보존 | 실제 C decoder와 정상/최대/손상 벡터 같은 의미. 다른 epoch·부분 자료·원 관측 시각 갱신 위장 수락 0 |
| DEC-17·ARCH-T33, 지도 저장/복원 | 누적 지도·관측 근거→재시작 후 사용 가능한 지도/정합 | 저장 방식은 담당자가 근거로 선택. 제자리/근접 위치 복원부터, 정보량·잔차·모호성·위치 오차 평가 | 정상 복원·모호한 대응 거절. 미검출을 free로 바꾸거나 근거 없는 95% 단일 점수로 수락하지 않음 |
| DEC-14·ARCH-T07·28, broker J/발행 | mapper·소형 건강 상태→직접 구독자 | Pi broker와 bridge 없음. 지도와 작은 상태/제어 경로를 분리하고 계정/ACL·도달성 확정 | 느린 A 수신자가 B·Pi 제어를 막지 않음. 원문·계정을 공통 이미지에 넣지 않음 |
| DEC-15·ARCH-T08, 영상 | 단일 카메라 JPEG→제한된 HTTP MJPEG/snapshot | 유한 queue·viewer 수·품질/FPS·인증, 느린 시청자 제한. 접속자별 재인코딩/카메라 재초기화 없음 | 영상 정체 중 mapper 진행 유지, frame 만료 표시·실제 대역폭·지연 측정 |

## 플랫폼 제공물과 선행 결정

### DEC-14·ARCH-T07·28 — 별도 broker 자격의 실제 Pi 제공물

Pi의 `xenova_provisioning.install_world`와 설치 CLI `xenova-world-install --vm-id UUID --file /private/approved-world.json`를 제공한다.
[필드·private 권한·실행 예제](../contracts/vm-provisioning-v1.md)를 따른다.
입력에는 해당 UUID의 world 자격만 넣는다. Pi 비밀번호를 Jetson에 전달할 필요가 없다.
공통 Bootstrap codec으로 검사한 뒤 기존 drive private 설정에 world 부분만 설치하며 새 실행에 적용한다.
이 호출은 Jetson 계정을 만들거나 broker/bridge/mapper를 변경하지 않는다.

Jetson broker 담당자는 VM별 UUID 인증·`xenova/v1/world/snapshot.pb`의 필요한 읽기 권한을 승인하고
별도 broker IPv4/port·profile·private credential 파일을 제공해야 한다. Pi 계정으로 인증됐다는 이유로
Jetson 인증을 완료로 표시하지 않는다. 운영 계정/ACL 발급 위치는 담당자가 실제 소스와 설치본으로 회신한다.
제공자 쪽은 격리된 두 broker·동시 Agent client의 실제 인증과 자기 VM 외 보고 쓰기 거절을 시험했다.
실제 Jetson broker ACL·mapper 구독/바이너리 송신·두 연결 중 하나의 장애/회복은 공동 수락이다.
world가 없거나 손상돼도 MANUAL/Pi VM 관리를 전역 차단하지 않는다.
생성기 LF/UTF-8 보완으로 새 primitive asset hash가 생겼다. 실제 `primitive-manifest.json`의 hash를 소비하고,
보존된 과거 golden의 hash를 새 firmware에 맞는 현재 구성으로 가정하지 않는다.

정식 원본은 `docs/contracts/drive-world/world.proto`와 `world.options`입니다.
Python은 `from xenova_drive_world import schema, encode, decode`로 사용합니다.
`encode(WorldSnapshot)`의 bytes를 broker J의 `xenova/v1/world/snapshot.pb`에
QoS0/non-retained 단일 PUBLISH로 보냅니다. JSON 봉투·압축·조각 프로토콜을 추가하지 않습니다.
최대 wire 32KiB·64×64 cell·road 각 64점이며 운영 주기·해상도는 이 최대값과 별개입니다.

Pi 상태는 broker P `xenova/v1/vehicle/state.pb`의 `VehicleSample`입니다. 현재
host/gateway/device/odom 문맥·원 표본/오차와 `sequence`를 확인해 mapper의 pose anchor에
사용합니다. mapper가 채운 `reference_odom_seq`와 그 순간의 `reference_vehicle_pose`를
consumer가 다시 계산합니다. 최신 sample을 과거 anchor 대신 넣거나 송신 시각으로 원
`observed_us`·cell age를 갱신하지 마세요. 정확한 좌표·시간·unknown 의미는 공통 계약을 따릅니다.

현재 제공 예제 `platform/apps/drive/examples/world_fixture.py --output <new-file>`은
유효한 합성 map bytes를 만듭니다. 실제 센서·mapper 대신 사용할 운영 코드가 아닙니다.
`platform/apps/drive/tests/test_world.py`는 공용 Python encoder→제품 C decoder를 실행하고,
`tests/run_lattice_pair.py`는 독립 인증의 두 broker와 실제 C 계산→Gateway→PTY를 연결합니다.
Jetson 담당자는 실제 센서의 정상·최대·부분 실패·clock/epoch 교체 입력을 같은 decoder로
수락하고 원 입력/버전·실행 명령/결과·미수행 장비를 회신해야 합니다.

남은 공동 제공물은 실제 mapper의 출처/clock 대응과 만료, 센서 calibration·차량 설정,
broker J/P별 계정/ACL·도달성입니다. tag·codec를 새로 설계하라는 대기가 아닙니다.
`lattice-lab-1`은 명시 시험값이며 운영 승인값이 아닙니다. Jetson 부재가 정상 MANUAL이나
일반 VM 관리의 전역 readiness 조건이 되어서는 안 됩니다. Jetson 제품은 이번에 대신 수정하지 않았습니다.

## 진행 기록

- 2026-10-29: 제품 소스 변경·장비 적용 없이 담당자 구현 범위를 정리했습니다. 관련 계약이 정해지면 실제 파일·입출력 벡터·설치 조합·시험 결과를 누적합니다.
- 2026-11-02: 공통 계약 미제공 당시의 요청을 현재 wire·encoder·실제 C 소비 경로로 교체했습니다. 센서·mapper·영상·실물 수락은 담당 작업으로 유지합니다.
