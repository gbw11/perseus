# Perseus Backend

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

## 테스트

Windows:

```powershell
.\mvnw.cmd test
```

Linux/macOS:

```bash
./mvnw test
```
