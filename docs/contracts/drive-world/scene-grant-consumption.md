# Pi 지도 사용권의 VM 소비

`SceneGrant`의 생산자는 Pi SceneAuthority다. 이 문서는 기존 Pi→mapper 전달과
별도로 VM의 실제 경로·출력이 같은 원 사용권을 소비하는 통로를 정의한다.
지도 파일 복원·SceneRef 형식 검사·MissionGrant 수신만으로 과거 FREE 사용권을
만들지 않는다. live-only 입력은 SceneGrant 없이 사용할 수 있다.

## 전달과 호환

DriveLink major2에 Pi→VM `oneof scene_grant = 10`을 추가한다. tag2..9와
WorldTrust의14필드, SceneGrant tag1..25는 유지한다. 본문은 기존 SceneGrant
그대로이며 inner cap1024B, 전체 DriveLink cap32768B다.

기존 broker P `xenova/v1/vms/<UUID>/drive/link/in`과 state I/O owner를 사용한다.
QoS0·non-retained이며 VM별 scene latest 슬롯 하나만 둔다. 새 socket·전송 thread를
추가하지 않는다. 기존 Pi→mapper/<device>/scene.pb 및 S→M SOURCE_SCENE은 별도다.

승인·철회 및 VM probe 뒤 재전달은 같은 원 grant를 사용한다. 재전달·clock renewal·
게스트 재시작이 issued/valid_until·admission/lifecycle revision·nonce·survey·digest를
새로 발급하지 않는다. PUBACK이나 전송 슬롯 소비는 사용권 적용 확인이 아니다.

실제 새 Scene·명시 복원·철회는 Pi clock/source owner의 의미 전환으로 source admission
revision을 전진시킨다. 같은 원 요청의 재전달은 같은 의미 key이며 전진하지 않는다.
이때 수치 clock lease·원 expiry·drift 이력·time lease revision은 재발급하지 않는다.
새 의미의 실제 probe/lease가 확인되기 전에는 새 ADMITTED 추진권한을 열지 않는다.
동일 ID의 terminal 이후 재사용은 원 allow_restore와 frozen checkpoint/full ref/expiry,
새 요청 nonce 및 source/lifecycle 전진을 검산한 purpose1 복원만 허용한다.

새 Gateway와 두 VM의 schema/decoder/consumer를 같은 배포 단위로 맞춘다.
tag10 미지원 소비자는 static-retained 기능을 지원하지 않는다. legacy features0/1의
retained bit2는 Python/C에서 거절한다. 기존 live-only legacy 메시지는 유지한다.
옛 retained 입력을 fresh LIVE로 바꿔서 통과시키는 호환 fallback은 없다.

## 권위와 수명

Gateway actor는 실제 SceneAuthority의 원 grant와 현재 mapper clock/source를 결박한다.
원 Authority journal의 실제 완료만 승인하며 UNKNOWN은 새 요청이나 성공으로 바꾸지
않는다. 파일 저장은 유한한 실행 경계에서 수행하고 actor의 STOP·현재 입력·만료 확인이
저장 완료나 filesystem lock을 기다리지 않도록 한다. operator2 예약과 feedback3의
실제 MatchedStop 생산은 별도 책임이며 값 stub을 실제 장치 근거로 승격하지 않는다.

VM은 Pi host/Gateway·차량·지도/좌표/odom·설정/보정/primitive와 현재 clock/source,
source admission·lifecycle 순서 및 원 full SceneRef와 기한을 검사한다. ADMITTED 원 grant와
맞는 static-retained만 사용할 수 있다. source 교체·권위 철회·만료는 의존하는 계획과
출력을 fence한다. malformed·과거 grant 재전달은 정상 사용권 갱신이 아니다.

retained에 의존하는 경로는 현재 MissionGrant의 scene admission ID/revision도 해당
World.scene/current SceneGrant와 같아야 한다. 이 pair는 episode fence이며 전체 SceneRef나
SceneGrant의 원 valid_until을 대신하지 않는다. Gateway는 같은 원 mission/state 관계로
출력 의존성을 확인한다. live-only/NONE의 nil sentinel은 유지한다.

Scene 변경으로 모든 AUTO/MANUAL이나 정상 A/B 승계를 일괄 재무장시키지 않는다.
현재 사용권에 의존하는 경로·명령과 일반 live-only 주행의 수명을 구분한다.

## 검증 경계

codec roundtrip/inner cap/legacy 거절과 함께 실제 Gateway actor·durable journal·
DriveLink relay·C decoder/current grant·plan/command expiry를 한 연결로 시험한다.
기존 정상 clock renewal·live-only·warm A/B·MANUAL 회귀도 영향 범위로 유지한다.
시험용 source와 stop fixture는 실제 OAK/보드·통제된 트랙·물리 정지 수락과 구분한다.
