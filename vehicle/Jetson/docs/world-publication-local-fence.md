# World의 원 publication과 최종 dispatch

이 문서는 Jetson 내부 outbox 계약이다. queue 수락·MQTT publish 호출·송신 완료는
각각 다른 사실이며 Pi의 World 수락·Scene·이동 권한을 발급하지 않는다.

`JetsonJio.replace_publication(binding)`은 기존 exact PublicationBinding을 검산하고
canonical SHA를 별도 marker로 보존한다. scene-only 변경은 이 marker와 미선택 World만
바꾼다. raw source token·clock/probe·odom·World sequence HWM·모델/parser 준비를 초기화하지
않는다. 같은 원 binding 재전달은 no-op이다.

`offer_world(raw, expires_j_us=..., publication_sha256=...)`는 실제 S가 보유한 현재 binding의
SHA를 요구한다. M의 원 packet expiry와 serialized World bytes를 그대로 보존하며 접수나
재연결로 새 기한을 만들지 않는다. 모든 World는 현재 검산된 returned ClockLease를 필요로
한다. Scene이 없는 live-only 출력도 clock 부재를 우회하지 않는다.

JIO callback은 공용 clock producer가 검산한 원 lease의 지원 하한·offset·expiry와 원
source-admission revision을 작은 immutable window로 보존한다. 같은 source의 정상 숫자
갱신은 기존 World의 원 expiry나 publication을 바꾸지 않는다. prior trust 없는 새 Probe는
현재 clock window를 철회하므로 이미 queued인 World도 새 dispatch를 시작할 수 없다.

SceneRef가 있는 World는 원 publication의 SceneRef와 현재 실제 SceneGrant의 full ref·원
grant digest·ADMITTED lifecycle·source revision·Pi 시각 범위를 대조한다. 알려진 revoke/close
callback은 S-main의 scene-only binding clear 전에 도착해도 이 World의 새 dispatch를 막는다.
live-only World는 Scene 권한을 요구하지 않지만 publication·source·clock 검사는 유지한다.

최종 JIO dispatch는 짧은 lock 안에서 현재 marker/source/clock/Scene/원 expiry를 대조하고
시작점을 고정한다. encode와 Paho network I/O는 lock 밖이다. **철회보다 먼저 이 시작점을
통과한 송신은 이미 시작한 작업**이다. 그 뒤의 철회로 이미 전송한 bytes를 회수했다고
주장하지 않는다. 시작점 전에 철회된 후보는 새 publish를 호출하지 않는다. Pi 소비자의
원 source·clock·Scene·age 검산은 전달 뒤에도 별도로 필요하다.

M의 원 결과는 `relay_publications`를 통해 최신 두 참조 포트에서 M-main의 bounded outbox로
넘긴다. ACK는 이 작은 outbox의 수락일 뿐이다. S는 이미 선택된 옛 publication packet을
`world_fenced`로 폐기하며 이를 현재 문맥의 새 성공이나 서비스 고장으로 바꾸지 않는다.
port의 peek 뒤 원 expiry를 넘긴 정상 출력도 `output_expired`로 폐기하고 새 기한을 붙이지 않는다.

`DataLink.pending`은 미송신 원 immutable packet이다. `discard_pending(original)`은 정확히
그 handle의 M World/summary 출력만 폐기하고 이미 사용한 message sequence를 환불하지 않는다.
EAGAIN은 미송신이며 폐기 가능하다. 부분/불명 send는 sticky 실패이고 폐기 성공으로 숨기지
않는다. identity·input·DB/bootstrap 작업의 만료와 오류는 이 출력 폐기 경로로 삼키지 않는다.

진단의 `world_fenced`와 `expired`는 문맥 철회와 원 기한 만료를 구분한다.
`world_dispatch_started`는 위 시작점의 횟수이며 broker 완료나 Pi 적용 횟수가 아니다.
software FakeClient 회귀는 이 순서와 bounded outbox만 검산하며 실물 시계·보정·처리율이나
브로커/실차 동작의 수락 증거로 확대하지 않는다.
