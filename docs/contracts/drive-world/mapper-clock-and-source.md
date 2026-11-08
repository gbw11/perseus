# Mapper clock 반환과 S/M source 제안

기존 mapper clock 교환은 Pi만 P4를 알아 Jetson이 원 Pi 센서시각을 J로 옮길 수 없었다.
또 EMPTY bootstrap 뒤 M의 map/frame 제안을 S에 전달하는 작은 통로가 명시되지 않았다.
다음은 이 두 공백을 보완하는 계약이다. 식별 결합·시계 수락·metric 관측·scene·이동 허가는 각각 구분한다.

## 이전에 수락한 시계 구간의 반환

`MapperClockProbe`의 기존 tag1~12를 유지하고 optional `WorldTrust mapper_trust=13`을 추가한다.
최초 probe에는 없으며 Pi clock owner가 같은 승인 source의 두 유효 왕복을 실제 수락한 뒤
**다음** probe에 이전에 수락한 원 trust를 싣는다. uplink는 offset/expiry를 만들지 않는다.
현재 교환의 P4를 Jetson이 안다고 해석하지 않는다.

| 항목 | 상한 |
| --- | --- |
| Probe generated max |913B |
| Probe software cap |1024B (기존512B 변경) |
| Reply generated max/software cap |914B/1024B |
| WorldTrust generated max |531B |
| local TRUST_CANDIDATE |12B header+실제 Probe+실제 Reply≤2048B |
| local PROBE_PLAN JSON |2048B (기존1024B 변경), 전체 local packet≤4096B 유지 |

TRUST_CANDIDATE의 Probe/Reply 개별 상한만 확인해서1024+1024를 허용하지 않는다.
원 local header와 body의 실제 합계도4096B 이하여야 한다. 최대 schema 상한과 실제
객체 길이를 구분하며 protobuf/JSON escaping 뒤 길이를 검사한다.

offset은 Pi−J다. Pi clock owner는 P1≤P4, J2≤J3, 원 nonce/seq/P1·peer 문맥과 기한을
대조하고 timestamp 오차와 승인 relative drift를 포함한다. source admission revision은
Pi 소유이고 S의 local source_binding_revision과 숫자가 같을 필요가 없다.
정상 lease renewal은 source를 교체하거나 원 world 관측 기한을 늘리지 않는다.

J 소비자는 vehicle·mapper boot/instance·J epoch·map/frame/odom·configuration·전체 차량
calibration bundle·primitive를 **현재 S 결합**과 모두 대조한다. 현재 J에서
`J_now+offset_hi<원 expires_pi_us`인 동안만 사용한다. 원 Pi interval은
`[pi_lo−offset_hi, pi_hi−offset_lo]`로 옮기며 수신시각으로 capture를 대신하지 않는다.

승인 lookback H를 clock 정책에 명시한다. drift 정의가 `|dP/dJ−1|≤rho`이면 Pi 기간을
J 기간으로 바꾸는 분모를 포함해 RTT와 max(lease,H)를 보수적으로 덮는다.
원 policy의 lease/H에서 얻은 지원 Pi 구간 밖의 Vehicle/ToF/capture를 거절한다.
pad 확대가 width budget을 넘으면 admission을 거절하며 expiry·source age를 늘리지 않는다.

## 별도 S↔M binary 통로

제어 LocalEnvelope의4096B cap과 DB_COPY는 유지한다. 별도 Linux seqpacket에서
little-endian `header_length:u32, body_length:u32, header JSON, body bytes`를 사용한다.
header≤4096B/body≤32768B/전체≤40960B다. 송신 pending1개, 회차 수신 최대4개다.

header의 exact fields는
`version,service_instance,channel_epoch,mapper_worker,sender,message_sequence,kind,request_id,
issued_j_us,expires_j_us,payload_length,payload_crc32,metadata`다.
version1, sender=S|M, UUID는 canonical nonzero, sequence/time은 decimal u64 string,
metadata는 유한 object다. 실제 full-send의 DataSent와 queue 예약을 구분한다.
원 issued<expires≤issued+5초이며 message kind별 실제 데이터 기한은 더 짧게 제한한다.

| 방향 | kind | body |
| --- | --- | --- |
|M→S |SOURCE_IDENTITY |없음, 작은 제안은 metadata |
|M→S |WORLD_CANDIDATE |공용 WorldSnapshot 원 bytes≤32768B |
|M→S |SURVEY_SUMMARY |원 SurveySummary≤512B, 공용 codec cap288B |
|S→M |SOURCE_IDENTITY_ACK |원 PublicationBinding≤2048B |
|S→M |SOURCE_PUBLICATION |scene-only 새 PublicationBinding≤2048B |
|S→M |SOURCE_VEHICLE / SOURCE_TOF |원 protobuf≤2048B, 세부 cap은 sensor 계약 |
|S→M |SOURCE_CLOCK |초기 또는 원 trust를 반환한 Probe≤1024B |
|S→M |SOURCE_SCENE |원 SceneGrant≤1024B |
|S→M |SOURCE_STORAGE |exact `{binding,bootstrap}`≤2048B, 원 D 사본 |
|S→M |SOURCE_STORAGE_ABORT |원 DbAbort history≤8192B, 현재 성공이 아닌 terminal event |

각 kind의 metadata 의미·현재 source/expiry는 typed consumer가 추가 검사한다.
CRC·구조·수신 성공은 metric/world/주행 승인이 아니다. raw 영상/NN tensor·큰 DB 사본은
이 통로에 넣지 않는다. 이미 kernel/broker로 나간 packet의 회수를 주장하지 않는다.

## SOURCE_IDENTITY

metadata의 exact fields는
`proposal_nonce,database_uuid,mapper_instance,raw_binding_sha256,map_epoch,frame_epoch,
odom_epoch,vehicle,configuration,calibration,primitive,calibration_profile_sha256,
vehicle_sample_sha256,vehicle_sequence,pi_host_boot,device_session,gateway_instance,pi_session,
checkpoint_manifest_sha256`다. request_id는 원 proposal job이며 nonce/원 issued/expiry를 함께 고정한다.

M은 현재 D의 EMPTY에서 map/frame UUID를 한 번 골라 ACK/재시도까지 유지한다.
COMPLETE는 실제 bounded LOAD로 검증한 원 checkpoint의 map/frame을 보존한다.
S가 미리 부여한 mapper_instance를 M이 바꾸지 않는다. mapper OS boot와 J clock epoch는
S 소유이며 이 제안으로 재발급하지 않는다.

S는 등록 M/channel·현재 RawBinding·실제 D UUID·승인 구성과 원 VehicleSample hash/sequence를
대조한다. Pi host/Gateway/device/Pi session/odom의 현재 원천이 바뀐 old sample을 거절한다.
원 Vehicle 전달의 최신성은 identity 단계의 제한일 뿐 아직 Pi/J pose freshness 증거가 아니다.

같은 nonce/job/원 시각/본문의 재전달은 같은 binding을 반환한다. nonce 재사용·다른 본문·기한
연장은 거절한다. M 교체 시 이전 S source revision floor를 이어 증가시키며 D-only 교체는
기존 M/binding/range를 유지한다. 미해결 nonce를 무제한 보관하거나 조용히 버리지 않는다.

S의 ACK는 immutable PublicationBinding을 수락했다는 뜻이다. 그 뒤 clock reply는
capture/NN/metric 준비를 기다리지 않는다. scene 승인·positive FREE·AUTO 출력 허가는 별도다.

## 보정 ID의 관계

RawBinding.calibration_bundle과 내부 map snapshot.calibration_id는 pin한 local
CalibrationProfile.sha256이다. wire World/Vehicle/Trust와 StorageContext.calibration은
승인한 **전체 차량 보정 bundle**이다. 두 hash를 같은 이름의 alias로 바꾸지 않는다.

운영 source 정책은 `profile.sha256`와 `profile.vehicle_bundle_sha256`의 명시 관계를 대조한다.
WorldSource.from_calibration은 내부 snapshot 검사를 local hash로, wire template 검사를
전체 차량 hash로 수행한다. direct WorldSource의 같은-ID fallback은 진단 호환 경로이며
실제 LIVE source는 명시 관계를 사용한다. ToF는 전체 bundle에 등록된 해당 sensor leaf와
대조한다. 이 관계의 형식 검사는 실제 장착·시간·first-surface 오차의 수락을 대신하지 않는다.

## Survey 보고와 scene-only publication 전달

다음은 기존 S/M local header13필드/sequence를 쓰는 두 explicit variant다. header4096B,
body32768B, packet40960B, 송신 pending1/RX4와 event 수명 최대5초는 유지한다.

| 방향/kind | body | exact metadata |
| --- | --- | --- |
| M→S `SURVEY_SUMMARY` | 원 protobuf `SurveySummary`≤512B. 공용 codec의 실제 cap은288B | `publication_sha256,grant_sha256` |
| S→M `SOURCE_PUBLICATION` | 기존 canonical PublicationBinding≤2048B | `source,origin,geometry_origin,context_sha256` |

SURVEY_SUMMARY의 outer issued는 원 summary.completed_peer_us이며 expires는 실제 원
관측/clock/scene 기한 중 최소와 transport5초 이하다. main은 측정 완료 시각을 다시 찍지
않는다. 단 status3의 새 부정 상태 사건은 실제 revoke 완료 시각을 기록할 수 있다. 이때
원 started/last_observed/timestamp_error/hash는 유지하며 긍정 관측 갱신으로 해석하지 않는다.
S는 등록 peer, 현재 publication SHA, 현재 원 SceneGrant SHA/nonce/survey/revision/profile,
compatible clock/source와 원 기한을 검산한다. 부적합 보고는 유한 거절/계수하며 source 전체를
새 incarnation으로 바꾸지 않는다. S-jio는 검산한 원 bytes를 현재 compatible reply19에만
복사한다. 새 probe/clock lease가 원 summary의 시각이나 expiry를 늘리지 않는다.
이 local summary의 producer는 공용 encoder이며 encode(decode(body))==body도 검산한다.
unknown optional을 버린 다른 본문을 원 보고와 동일하다고 relay하지 않는다.
JIO는 마지막 같은 admission_id의 admission/lifecycle revision 감소와 동일 lifecycle의
다른 원문을 거절한다. 새 admission_id는 다시 revision1이므로 전역 max를 적용하지 않는다.
서로 다른 과거 ID 전체를 기억하는 registry는 없다. 다른 old ID의 replay는 M Scene의
new-grant 상태 검사, 원 publication SHA, 현재 Pi Authority gate의 제약도 함께 필요하다.

현재 Pi ADMITTED grant(lifecycle_state2)와 일치한 APPLIED summary(status2)만 scene ref를
연결한다. SceneAdmissionRef는 protobuf MessageToDict(preserving_proto_field_name=True)의
표준 JSON으로 보존하고 ParseDict/공용 encode 검산을 거친다. 같은 ref/report의 재전송은
revision을 늘리지 않는다. REVOKED/FAILED/현재 grant 또는 clock 만료는 null로 철회한다.
scene-only 변경은 source_binding_revision만 정확히1 증가시키며 S의 기존 on_publication을
통해 D와 M에 전달한다. 기존 geometry/raw/map/frame/odom/Pi source는 유지한다.

M은 최초 identity ACK와 달리 새 proposal을 만들지 않는다. 원 registered S peer, 정확한
다음 publication revision, 같은 source/origin/geometry/raw와 실제 원 ADMITTED grant ref를
대조한 뒤 context SHA를 재구성한다. null 철회는 원 기하 문맥에서 안전 수락한다. 이전
proposal request/issued/expiry를 새 publication 전달에 재사용하지 않는다. 이 전달은 M의
COMPLETE/pose/metric/scene evidence나 이동 권한을 S가 대신 수락했다는 뜻이 아니다.
유효하게 생성한 SOURCE_PUBLICATION의 원 전달 기한이 끝나면 조용히 drop하거나 새 기한을
붙이지 않고 명시 timeout으로 source 재동기화 필요를 노출한다. 다음 revision만 보내 이전
revision gap을 숨기지 않는다. 잘못된 survey 보고의 유한 거절과 이 전달 실패는 구분한다.

M-main은 WORLD_CANDIDATE와 SURVEY_SUMMARY를 최신 각1개 원문으로만 stage한다.
이미 선택된 DataLink packet은 바꾸지 않으며 같은 원문의 retry도 원 issued/expiry를 유지한다.
M의 `SurveySummary.world_sequence`는 같은 채택 새 RGB 후보의 World sequence다. 원 RGB의
sensor sequence와는 별도이며 World 미준비/만료로 발행 순번에 공백이 생길 수 있다.
이 값만으로 해당 World의 송신/PUBACK/Pi 수락이나 Scene 사용권을 증명하지 않는다.

## 실제 source 전달과 geometry origin

설치 profile의 `device`는 MQTT 계약의 논리 mapper ID다. RawBinding.device_id는 SDK의
실제 OAK MXID이며 별도로 유지한다. S가 승인한 설치 profile·현재 raw binding·보정 관계를
같이 고정하고, 두 문자열을 같다고 가정하거나 원 MXID의 대소문자를 바꾸지 않는다.

SOURCE_IDENTITY_ACK metadata는 exact `source,origin,geometry_origin,context_sha256`다.
source는 원 clock source11필드, origin은 실제 Pi host/Gateway/device/Pi session4필드다.
geometry_origin은 versioned `profile,vehicle,pi_host_boot,gateway_instance,device_session,
pi_session,odom_epoch,calibration`이며 sample sequence/time/pose/lease를 넣지 않는다.
M은 원 제안·D UUID·publication과 이 값으로 StorageContext SHA를 다시 대조한다.
ACK는 원 proposal request/issued/expiry를 유지한다. 현재 실제 RAW ACK 전에는 작은
identity 제안 하나만 보류하며 원 기한이 끝나면 새 기한으로 되살리지 않는다.

원 Vehicle/ToF/Scene/Clock 전달 metadata는 exact
`received_j_us,sensor_id,clock_probe_sha256`이다. received는 S-jio의 원 callback J2다.
local1초 전달 기한은 Pi 원 측정 age나 원 WorldTrust expiry를 늘리지 않는다. M은
Vehicle24개·Probe8개·ToF sensor별1개·Scene1개의 원 bytes와 callback 시각을 유지하고,
compute job에는 최대64KiB 불변 snapshot을 준다. 실제 clock/sensor/pose 검산은 compute
소유이며 참조한 원 probe가 없으면 수신시각으로 대체하지 않는다.

새 실제 Pi/Gateway/device/session/odom을 수신하거나 현재 M을 닫으면 S-jio의 source
token과 아직 미발행 world/clock 참조를 즉시 철회한다. 정상 time lease 갱신은
PublicationBinding·지도 ID·A의 OAK 세션을 교체하지 않는다.

StorageContext의 optional geometry_origin은 명시한 새 문맥에만 포함한다. legacy 문맥은
그 key가 없던 원 field set/canonical bytes/SHA를 그대로 roundtrip한다. PUBLICATION BIND는
기존 exact3필드 또는 geometry_origin이 추가된 exact4필드를 받으며, Pub12 SHA와 원 ACK
request 관계는 유지한다. D는 원 Pi origin을 별도로 수락하고 CURRENT_MAP LOAD context를
재구성한다. 원 D BOOTSTRAP_RESULT나 durable receipt에 S 소유 origin을 끼워 넣지 않는다.

catalog 미사용 범위의 첫 mapping은 원 range job/context와 실제 COMMITTED receipt를
검산한다. 양쪽에 명시한 origin이 있으면 versioned geometry-scope로 같은 M/raw/실제
origin의 scene-only 전환을 비교할 수 있다. 원 reservation/receipt를 새 context로
재발급하지 않는다. legacy는 exact context 규칙을 유지하고, 현재 job/provenance/freeze/
COMMIT의 full context 검사는 계속 적용한다.

SOURCE_STORAGE_ABORT body는 원 operation/body/request/issued/expiry/mapper/control channel/
context/observed/reason/history를 보존한다. outer local delivery의 새 시각은 통지 수명이며,
안쪽 만료 job을 새 DB RESULT나 현재 성공으로 바꾸지 않는다. 모호한 native owner나 이미
선택된 transport packet은 이 통지로 반환한 척하지 않는다.
