# Xenova Backend

Spring Boot 기반 VM 관리 서버입니다.

## 요구 사항

- Java 21 이상
- 저장소에 포함된 Maven Wrapper 사용

## 실행

Windows:

```powershell
.\mvnw.cmd spring-boot:run
```

Linux/macOS:

```bash
./mvnw spring-boot:run
```

기본 포트는 `8080`이며 `SERVER_PORT` 환경 변수로 변경할 수 있습니다.

## 확인

```text
GET http://localhost:8080/actuator/health
```

정상 응답:

```json
{"status":"UP"}
```

## 인증 API

```text
POST /api/v1/auth/signup
POST /api/v1/auth/login
POST /api/v1/auth/refresh
POST /api/v1/auth/logout
```

로그인은 15분 유효한 JWT Access Token을 응답 본문으로, 7일 유효한 Refresh Token을
`HttpOnly` 쿠키로 발급합니다. 재발급은 Refresh Token 쿠키로 두 토큰을 새로 발급하고 기존
Refresh Token은 폐기합니다. 이미 폐기된 Refresh Token이 다시 사용되면 탈취로 보고 해당 사용자의
모든 Refresh Token을 폐기합니다. 로그아웃은 두 토큰을 서버에서 폐기합니다.

로컬 실행에는 개발용 JWT 비밀값과 H2 파일 DB가 사용됩니다. 운영 환경에서는 반드시 다음
환경변수를 설정해야 합니다.

```text
JWT_SECRET=<32바이트 이상의 임의 비밀값>
DB_URL=<운영 DB JDBC URL>
DB_USERNAME=<DB 사용자>
DB_PASSWORD=<DB 비밀번호>
AUTH_COOKIE_SECURE=true
```

## 화면 배치

모니터 2대(`display-0`, `display-1`)에 띄울 화면은 백엔드가 정하고, dom0 표시 서비스가 백엔드에서
명령을 받아 키오스크 브라우저를 옮깁니다. 모니터 브라우저는 백엔드에 붙지 않습니다.
기준 문서는 `docs/contracts/display-route-v1.md` 입니다.

```text
GET  /api/v1/displays                       콘솔: 배치 목록 (사용자 JWT)
PUT  /api/v1/displays/{display_id}/route    콘솔: 배치 변경 → 202 (사용자 JWT)
WS   /ws/v1/displays                        콘솔: display.snapshot 수신 전용 (첫 프레임 사용자 JWT)
WS   /ws/v1/display-service                 dom0 표시 서비스 전용 (첫 프레임 서비스 토큰)
```

- 표시 서비스는 첫 프레임 `{"type":"auth","service_token":"..."}` 로 인증하고 `service.hello` 를 보냅니다.
  백엔드는 디스플레이마다 지금 배치를 `route.set` 으로 보내고, 이후 PUT 마다 다시 보냅니다.
  서비스는 `route.applied` / `route.failed` 로 결과를, `display.status` 로 모니터 연결 변화를 알립니다.
- 동시에 붙을 수 있는 표시 서비스는 1개입니다. 새 연결이 인증하면 이전 연결은 4409 로 닫힙니다.
- 서비스 토큰은 `XENOVA_DISPLAY_SERVICE_TOKEN` 환경변수(`xenova.display.service-token`)로 줍니다.
  비어 있으면 표시 서비스 연결을 모두 4401 로 거부합니다. 표시 서비스에도 같은 값을 설정합니다.
- 배치 상태는 메모리에만 있어 백엔드가 재시작하면 두 모니터 모두 `split` 으로 돌아갑니다.

## 테스트

Windows:

```powershell
.\mvnw.cmd test
```

Linux/macOS:

```bash
./mvnw test
```
