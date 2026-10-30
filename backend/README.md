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

### Docker 이미지

```bash
docker build -t xenova-backend ./backend
docker run --rm -p 8080:8080 --env-file .env xenova-backend
```

이미지는 Java 21로 빌드한 뒤 JRE 런타임에 JAR만 복사하며, 일반 사용자 `xenova`로 실행합니다.
`/actuator/health`가 정상 응답하는지 컨테이너 헬스체크가 확인합니다.

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

### VM 웹 콘솔 중계

`/ws/v1/vms/{vm_id}/console`은 첫 메시지의 사용자 JWT로 인증한 뒤 등록된 실행 VM의
dom0 콘솔로 연결합니다. `DOM0_MANAGER_BASE_URL`과 `DOM0_MANAGER_SERVICE_TOKEN`을
서버에 설정하고, dom0-manager의 `XENOVA_CONSOLE_ENABLED=1` 및 같은 서비스 토큰을 준비합니다.
프론트엔드에 서비스 토큰을 전달하지 않습니다. 데이터·종료 규격은
[웹 터미널 계약](../docs/contracts/browser-console-v1.md)을 참고하세요.

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

로컬 실행과 운영은 MySQL 8.4를 사용하며, 애플리케이션 시작 시 Flyway가 아직 적용되지 않은
마이그레이션을 실행합니다. 로컬에서는 저장소 루트의 `compose.yaml`로 MySQL을 먼저 실행하고,
운영 환경에서는 반드시 다음 환경변수를 설정해야 합니다.

```text
JWT_SECRET=<32바이트 이상의 임의 비밀값>
DB_URL=jdbc:mysql://mysql:3306/xenova?useSSL=false&serverTimezone=UTC&characterEncoding=UTF-8
DB_USERNAME=<DB 사용자>
DB_PASSWORD=<DB 비밀번호>
AUTH_STATE_STORE=redis
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_PASSWORD=<Redis 비밀번호>
REDIS_SSL=false
REDIS_TIMEOUT=2s
REDIS_HEALTH_ENABLED=true
AUTH_COOKIE_SECURE=true
```

MySQL은 사용자·VM 등 영구 데이터를 저장합니다. Redis 7은 Refresh Token, 로그아웃된 Access
Token의 JTI, 로그인 실패 횟수를 TTL과 함께 저장합니다. Refresh Token 회전 및 재사용 감지와
로그인 실패 횟수 증가는 Redis Lua 스크립트로 원자적으로 처리하므로 백엔드를 여러 대 실행해도
인증 상태가 공유됩니다. 사용자 이름은 로그인 제한 키에 원문 대신 SHA-256 해시로 저장됩니다.
자동 테스트는 `AUTH_STATE_STORE=jpa`와 MySQL 8.4 Testcontainers를 사용해 Redis 없이 실행되며,
운영 기본값은 `redis`입니다. 테스트 실행 환경에는 Docker Engine이 필요합니다. 테스트 시작 시
빈 MySQL DB에 Flyway 마이그레이션 전체를 적용한 뒤 Hibernate가 엔티티와 스키마의 일치를 검증합니다.
Redis 헬스체크는 Redis가 함께 배포되는 운영 환경에서만 `REDIS_HEALTH_ENABLED=true`로
활성화합니다.

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
