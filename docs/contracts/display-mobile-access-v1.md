# 휴대폰 표시 접속 계약 v1 — 349

2026-11-03 사용자 선택: 갤럭시 S24의 첫 접속은 **임시 6자리 연결번호 입력** 방식이다.
휴대폰 자체 렌더링과 휴대폰 우선/필요 시 HDMI 대체 시연 원칙(348~352)을 따른다.

## 데이터·제어 경계

- 기존 dom0 display-feed 한 개의 `127.0.0.1:8082`를 재사용한다.
- USB 전용 접속 서비스는 허용된 GET만 중계한다. 수집기·VM/Gateway 상태·MQTT를 새로 만들지 않는다.
- 서비스 토큰은 기존 root 전용 환경에서 주입하고 loopback GET에만 사용한다.
  휴대폰에는 내부 Bearer 토큰·관리자 비밀번호·기존 feed cookie를 전달하지 않는다.
- 휴대폰 cookie는 중계 전용이다. 허용 범위는 `/display/mobile`, 빌드된 정적 자산,
  `/api/v1/display/presentation`이다. 주행 명령·VM 관리·화면 선택·내부 state/WS·세션 발급·HDMI ACK는 차단한다.
- 별도 WebSocket·비디오 캡처·인코더·HDMI 브라우저가 필요하지 않다.

## USB 접속 설정

348에서 실제 확인한 USB 인터페이스와 폰 IPv4를 root 전용 JSON에 명시한다.
구성은 `schema_version=1`, `usb_interface`, `phone_ipv4`, `port`만 허용한다.
코드는 USB 버스·UP 상태·RFC1918 IPv4·폰과 동일한 서브넷의 단일 보드 IPv4를 확인한다.
모호하거나 미확인인 값은 거부하며 IP·라우트·DNS·DHCP·기존 통신 설정을 변경하지 않는다.

listener는 그 보드 IPv4와 포트, 해당 USB 장치에만 묶는다. 실제 연결 peer가 설정된 폰 IPv4와
같아야 한다. Host는 보드 IPv4:포트, POST Origin은 해당 HTTP origin과 일치해야 한다.
X-Forwarded-For와 proxy 헤더를 신원으로 신뢰하지 않는다. Wi-Fi/Tailscale/외부망으로 자동 우회하지 않는다.

현재는 직결 USB 내부 HTTP 접속만 지원한다. 일반 네트워크/인터넷 공개·HTTPS 프록시 배포는 범위 밖이다.
기존 display-feed bind를 외부 인터페이스로 바꾸지 않는다.

## 연결번호와 세션

| 항목 | v1 동작 |
|---|---|
| 연결번호 | 난수 6자리, 120초 유효, 한 번만 사용 |
| 실패 제한 | 한 번호에 최대 5회 시도. 만료/실패 한도 뒤 운영자가 새 번호 발급 |
| 번호 제공 | root 전용 `/run/xenova-display-mobile/pairing-code`, 로그·URL·HTML에 노출 안 함 |
| 재발급 | 접속 서비스 main에 SIGUSR1. 기존 폰 세션도 무효화하며 feed/VM/Gateway는 재시작하지 않음 |
| 입력 | `/connect`의 동일 origin POST form. URL query나 GET으로 번호를 입력하지 않음 |
| 세션 | 무작위 opaque cookie `xenova_mobile`, HttpOnly·SameSite=Strict, 최대 8시간 |
| 저장 | 단일 폰 세션을 접속 프로세스 메모리에만 보관. 재시작하면 무효화 |
| 범위 | 표시 읽기 전용. 차량 제어·VM 생성/중지 권한 없음 |

기본 실행 서비스는 자동 enable하지 않는다. 실제 USB 준비·설정·기존 feed를 확인한 운영자가 시작한다.
환경 파일·인증정보를 새 예제로 덮어쓰지 않는다.

## 오류·복귀

- 최초 접속/세션 만료: HTML 진입은 연결번호 화면으로, API 조회는 401로 처리한다.
- 잘못된 번호/만료/횟수 초과: 번호 재확인·재발급 안내. 이전 번호를 성공으로 재사용하지 않는다.
- feed 미준비/중단/내부 인증 실패: 503. HTML은 화면 준비 안내, API는 고정 오류 코드.
  원시 upstream 응답·예외·비밀값을 오류에 넣지 않는다.
- feed 복귀: 다음 GET에서 기존 최신 presentation을 전달한다. 성공 상태를 중계에 캐시하지 않는다.
- USB 동일 주소 복귀: 세션이 유효하면 기존 브라우저의 다음 조회로 새 상태를 받는다.
- USB/폰 주소 변경: 자동으로 새 주소를 추측하지 않는다. 운영자가 실제 주소를 확인해 해당 설정만
  갱신하고 접속 서비스만 재시작한 뒤 새 URL/연결번호로 접속한다.
- 이전 상태를 정상으로 표시하지 않는 freshness/순서 처리는 기존 `useDisplay`/presentation 계약을 따른다.
  접속 서비스가 활성 VM·주행 모드·출력 허가를 판단하거나 변경하지 않는다.

실제 USB·S24·재부팅·장시간 시험은 별도다. 로컬 테스트 통과를 물리 연결 성공으로 표시하지 않는다.
운영 명령은 [실행 안내](../../platform/services/display/router/deploy/mobile-access/mobile-access.README.md)에 있다.
