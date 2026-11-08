# 경로 상태와 실제 clock·방향 전환 증거

State2 tag1..32, ClockProbe tag1..5, ClockReply tag1..8의 의미는 유지한다.
새 운영 profile은 아래 생산자·소비자를 같이 배포한다. 구형 peer의 알 수 없는 필드
반향이나 State 사본만으로 새 clock·READY 권한을 만들지 않는다.

## 같은 계산의 TaskReferenceState

DriveState의 optional task_reference33과 state feature8은 서로 같은 존재 조건이다.
기존 base7은 유지한다. 본문이 있으면15, 없으면7이며 모르는 feature는 거절한다.
본문은 원 home/from/selected/changing, basis_revision, 열린 float32 prefix2..64,
vehicle/mapper_boot/mapper_instance/clock_epoch를 운반한다. active/bound는 본문 존재로
함께 참이며 일부만 참인 native 상태는 내보낼 수 없다.

parent reference_id/map/frame/odom/configuration/calibration/primitive는 기존 의미다.
parent reference_revision은 계산 witness, basis_revision은 원 prefix의 판이며 합치지 않는다.
현재 path의 같은 snapshot에서 source·basis를 대조하고 수출한다. 최신 World나 engine의
새 intent로 이미 계산한 path의 사본을 다시 구성하지 않는다. 채택은 실제 current trust와
prefix/append 규칙·현재 Vehicle pad·전체 path·원 Scene/Mission/expiry를 재검사한다.
선택 차로 실행 profile은 현대 World의 본문 없는 warm 적용을 허용하지 않는다. codec이
기존7의 구조를 읽는 것과 실제 profile이 이를 채택하는 것은 별개다.

Task 본문 cap1024B, State metadata4096B, 전체 wire32768B를 유지한다. 이 본문은 회피
episode 원 예산·종료 정책을 새로 만들거나 재발급하지 않는다.

## Pi↔VM clock receipt와 명시 ACK

Probe.receipt6은 원 probe_sequence·실제로 받은 DriveLink MQTT payload 전체의 SHA256·
actual guest_receive_us(P4)다. 재직렬화한 ClockReply 부분 본문을 hash하지 않는다.
Pi는 I/O publish 진입의 실제 send stamp된 원 bytes/P1/P2/P3를 유한 registry에 보존한다.
같은 VM/execution/app와 PiHost/Gateway의 원 key/digest/P4, 순서·RTT/drift·원 기한을 검산한다.
Reply.guest_clock_valid_until_pi_us11은 Pi가 enqueue 전에 고정하는 원 E다. send stamp,
receipt 또는 ACK 도착으로 갱신하지 않는다. 정책·오차·lease는 J→Pi WorldTrust와 별개다.

Reply.accepted_receipt10은 Pi가 검산한 receipt에만 설정한다. Probe echo는 ACK가 아니다.
VM은 ReplyN으로 pending numericN/P4만 만든다. ProbeN+1로 ReceiptN을 돌려보내고,
ReplyN+1의 명시 accepted_receipt가 자기 원 ReceiptN과 정확히 같을 때 numericN을
confirmed로 승격한다. 새 N+1은 pending이다. 현재 Vehicle/Gate는 새 Reply를 소비하되
confirmed numeric으로 원 기한을 계산하므로 서로 같은 probe seq를 강제하지 않는다.
ACK 유실의 동일 원 receipt는 idempotent하게 처리한다. 원 pending이 만료되면 이를 버리고
새 bootstrap을 진행하며 정상 링크가 영구 HOLD에 빠지지 않는 양성 시험을 둔다.

새 operator 설치 profile은 planner_manifest의 실제 canonical profile/generator 지문과
별도 고정 planner_asset_sha256을 검사하고 Pi guest_clock의 timestamp/drift/offset 폭/
lease를 compiled profile과 같게 제한한다. 현재 C의 독립 RTT 상한도1s로 일치시킨다.
이 검사는 시작 때 한 번 수행하며 프레임·명령 경로에서 파일을 읽거나 doctor를 호출하지
않는다. 설치 manifest와 실행 C 바이너리의 동일성 및 물리 clock 오차의 수락은 별도다.

## command3의 원 clock 참조

기존 exact drive-command-2는22키로 유지한다. 새 drive-command-3은 같은 A7 키에
clock_probe_sequence:u64decimalstring과 next_direction:signedint{-1,0,1}을 더한24키다.
AUTO cusp STOP/HOLD_STEER만 자기 immutable pending next gear의±1을 의도로 제시한다.
일반 DRIVE·비전환 STOP·MANUAL은0이다. 이 의도만으로 추진권한을 만들지 않는다.
nonSTOP job은 실제 confirmed 원
seq를 고정하고 completion/send/retry 때 최신 seq로 교체하지 않는다. Pi는 원 accepted
registry의 clock E와 실제 source/phase/driver/Vehicle/Mission E를 직접 검산한다.
latest clock으로 old job을 연장하거나 가능한 모든 clock의 min으로 영구 HOLD를 만들지 않는다.

numeric source는 VM/execution/app+PiHost/Gateway이고 current control generation/owner/role은
별도 권한 fence다. 준비된 B는 자기 원 valid clock을 original E까지 사용할 수 있다.
generation 변경만으로 새 clock·시간 예산이나 재arm을 강제하지 않는다. old owner A의
명령은 현재 권한에서 거절한다. STOP은 clock seq0을 허용하며 clock/World/driver/Gate
미준비를 기다리지 않는다. exact 문법·현재 producer/role·sequence fence는 그대로 검사한다.

## 실제 DirectionGateWitness

Reply.direction_gate9는 실제 Pi feedback owner의 원 STOP/APPLIED·양 wheel 정지·steer
settle 판정이다. GateRef.stop_command_sequence는 Pi serial STOP seq이며 VM의 원 STOP
calculation_sequence는 별도다. 원 A7 control generation/role/calculation/plan/phase/segment/
cusp/mission/driver key, stationary Vehicle seq, Pi/device session, 원 E를 함께 전달한다.
현재 C pending transition과 정확히 대조하고 한 번만 다음 gear에 소비한다. 같은 mission의
다른 cusp·old owner·old phase·복사된 READY는 이 증거를 대신하지 않는다.
초기 PULSE/NEUTRAL_ONLY의 실제 중립 STOP은 driver_action_seq0을 그대로 유지한다.
MANUAL의 requested_direction0은 그 중립 정지 근거를 표현할 수 있지만 어느 방향의
추진권한도 발급하지 않는다. 새 실제 입력·registry/fence와 C의 원 중립 기준을 별도로
검사한다. AUTO의 다음 gear는 반드시 자기 pending 방향±1과 같아야 한다.
STOP의 v/steer0이나 cusp index에서 다음 방향을 추정하지 않는다. AUTO는 원 command3의
next_direction과 full pending job을 Pi serial pin에 결박한다. 같은 logical cusp의 새
calculation/feedback sequence와 반복 STOP/HOLD는 원 budget·gate revision을 재시작하지
않는다. 충돌하는 의도나 새 plan/source는 old READY를 철회한다.

로컬 첫 STOP 시도가 latest mailbox에서 교체될 수 있으므로 이를 원격 pin으로 단정하지
않는다. C는 원 phase 안의 immutable STOP 후보를 최대8개만 보존하고 실제 Gate witness의
calculation_sequence로 원 후보를 선택한다. state1(STOPPING)은 실제 serial STOP write의
pin 식별이며 APPLIED·standstill·READY를 뜻하지 않는다. pin 뒤에는 후보를 늘리지 않고
원 STOP을 유지한다. 후보 상한·원 phase 만료 때는 새 허가 없이 STOP으로 남는다.

새 Reply나 STOP 반복으로 원 phase/cusp 예산을 다시 만들지 않는다. Gate의 원 E와 실제
source가 유효해야 하며 STOP은 Gate 준비를 기다리지 않는다. ordinary MANUAL의 같은 SID
CONTINUE_SESSION과 정상 준비된 AUTO 승계는 별도로 보존한다. cusp/settle/next-ready
근처 승계는 기존 R1 stop_then_resume 제약을 유지한다.

Gate의 phase E는 첫 실제 STOP pin에서 고정하는 원 phase budget이며 개별
DRIVE/HOLD_STEER의100ms command E와 다르다. 이를 혼합해100ms로 줄여 정상 settle를
불가능하게 만들지 않는다. C는 자기 원 pending phase E와 Gate E를 단축만 한다.
HOLD_STEER의 새 calculation/job·실제 serial E는 별도로 검사하되 원 STOP serial seq와
phase budget은 유지한다. 근거 없는 safety STOP은 언제나 가능하지만 그 사실을 뒤늦게
현재 pending transition의 READY 권위로 승격하지 않는다. 초기10s 방향 phase cap은
원 설계07장의 소프트웨어 예상값이며 실측 기기 성능을 뜻하지 않는다.
