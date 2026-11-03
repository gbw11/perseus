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

## 차량 HDMI용 읽기 상태

차량 HDMI 데이터는 전용 `/ws/face/*` 경로를 만들지 않고 기존 읽기 API를 재사용합니다.

```text
GET /api/v1/display/state    VM 실행·앱·OS·주행 모드·active_vm_id (사용자 JWT)
WS  /ws/v1/display/state     같은 Display 스냅샷 (첫 프레임 사용자 JWT)
GET /api/v1/drive/state      속도·조향·전압·경고·앱 명령·복구 재개 상태 (사용자 JWT)
WS  /ws/v1/drive/state       같은 Drive 스냅샷 (첫 프레임 사용자 JWT)
```

- VM A/B의 현재 구동 여부는 고정 역할명이 아니라 신선한 `active_vm_id`와의 일치로 판정합니다.
  일치하는 정상 주행 VM이 Active이고, 나머지 정상 주행 VM이 Standby입니다. source가 `stale`,
  `disconnected`, `unknown`이면 이전 ID로 현재 구동 VM을 추론하지 않습니다.
- Drive의 최상위 `source`는 MQTT 연결 경계를, `control.source`와 `vehicle.source`는 각 데이터의
  `fresh`/`stale`/`disconnected`/`unknown` 상태와 age를 제공합니다. 마지막 값은 장애 분석을 위해
  남지만 source가 `fresh`가 아니면 현재 값으로 표시하지 않습니다.
- `apps.*.direction`은 신선한 `drive.command`의 부호로 계산합니다. `v_mps` 양수/음수/0은
  `FORWARD`/`REVERSE`/`STOPPED`, `steer_rad` 양수/음수/0은 `LEFT`/`RIGHT`/`STRAIGHT`입니다.
  오래된 명령은 `UNKNOWN`입니다. `STOPPED`는 브레이크와 입력 없음까지 구분하지 못합니다.
- `vehicle.batt_v`는 전압이며 잔량 퍼센트가 아닙니다. SOC, 네비게이션, 음악 데이터는 아직
  계약되지 않았으므로 임의 변환값이나 데모값을 반환하지 않습니다.
- 복구 시작·완료·실패는 기존 `GET /api/v1/events`, `WS /ws/v1/events`를 사용합니다. dom0 로컬
  presentation 연결은 platform 범위이며 이 백엔드 경로와 별도로 작업합니다.
- dom0 키오스크의 HttpOnly 쿠키는 Spring 사용자 JWT가 아니므로 위 API와 VM 변경 API에서 모두
  401로 거부됩니다. 키오스크는 기존 로컬 presentation과 렌더 ACK 범위만 사용합니다.

## Zephyr 지표와 앱 보고 조회

- `GET /api/v1/vms/{vmId}/metrics?source=zephyr_telemetry`와 기존 메트릭 WebSocket에서
  v1 RX/TX 누적값은 `values[].counter_value`의 uint64 십진 문자열로 제공됩니다. 실제 0은
  `"0"`이고, 미측정이면 `unavailable_reason`과 `source_rc`가 제공됩니다.
  누적값을 차분할 때는 VM, `boot_id`, `network.publisher_instance_id`, `labels.iface`,
  `labels.counter_epoch`가 같고 `network.observed_uptime_ms`가 증가한 두 유효 표본을
  사용합니다. 수신 시각은 조회 정렬용이며 차분 간격은 관측 업타임으로 구합니다.
  Telemetry 2.1의 직접 계측 `rx_bytes_per_s`는 기존 지표 조회에서 함께 받을 수 있습니다.
- `GET /api/v1/vms/{vmId}/threads`는 최근 Zephyr 2.1 CPU 표본 한 묶음을 반환합니다.
  이름과 별개인 `thread_id`, `cpu_percent`, 계측 구간, 페이지 수와 수집 상태를 제공합니다.
  `complete`는 페이지 전달의 완전성이고, 실제 수집 잘림은 `threads_status=truncated`로
  별도 표시합니다. 서로 맞지 않는 페이지는 `conflicting_pages`로 표시합니다. 최근에
  관측한 publisher의 결과이며 Xen의 현재 실행 확인과는 별개입니다.
- `GET /api/v1/drive/state`와 `/ws/v1/drive/state`의 `vm_apps[vm_id][app_id]`는
  VM별 토픽의 마지막 `lifecycle`과 `heartbeat`를 각각 제공합니다. 두 신호의 신선도는
  `lifecycle_source`와 `heartbeat_source`로 확인합니다. `WAITING`은 대기 보고이며
  heartbeat 수신 여부는 별도입니다. 앱 상태는 출력 권한이나 VM 등록 상태를 바꾸지 않습니다.
  기존 공용 토픽의 `apps`와 모드 필드도 유지됩니다.

신규 지표는 기존 `vm_metric_samples.metric_values`에 값과 관측 문맥을 함께 저장합니다.
이 버전은 과거 배열과 새 객체 형식을 읽습니다. 새 객체를 저장한 뒤에는 배열만 읽는
이전 Spring 바이너리로 되돌리기 전에 DB 읽기 호환을 확인해야 합니다.

## Linux·Zephyr 수집기 상태

- `GET /api/v1/vms/{vmId}/telemetry/heartbeat`에서 VM별 마지막 v2.1 보고와
  `received_at`·`age_ms`를 조회합니다. `status=FRESH`일 때만 `publisher_state`의
  `running`·`degraded`·`stopping`을 현재 상태로 표시합니다. `STALE`은 관측 만료,
  `UNKNOWN`은 미수신입니다. 만료된 마지막 보고는 진단용으로 남습니다.
  수신 lease 기본값은 6초이며 `AGENT_TELEMETRY_HEARTBEAT_LEASE`에서 설정합니다.
- `last_collection_success_uptime_ms`는 게스트 부팅 후 마지막 수집 성공까지의 시간을
  십진 문자열로 제공합니다. 기존 `last_collection_success_age_ms`는 보고 당시 경과 시간입니다.
  `collection_failures_total`은 Publisher 세션의 누적 실패 횟수입니다.
  누계가 남아 있어도 신선한 heartbeat가 `running`이면 화면에는 `running`으로 표시합니다.
  이 응답은 최신 수신 관측이며 현재 Xen 실행을 확인하는 근거로 쓰지 않습니다.
  기존 Linux v1 `agent_status` 및 VM 복구 경로와 독립입니다.
- Flyway V7은 `vm_telemetry_heartbeats`에 VM당 최신 보고 한 건을 저장합니다. 같은 세션의
  중복·역전과 직전 부팅·Publisher의 지연 보고를 걸러 수신 유효기간 갱신을 방지합니다.
- 새 v2 지표는 등록 VM의 종류에 따라 `linux_telemetry` 또는 `zephyr_telemetry`로 분류합니다.
  등록 전 공통 Publisher 지표는 `vm_telemetry`입니다. 과거 저장 행의 출처는 보존합니다.
  기존 Linux v1 자원 지표는 `guest_agent`를 유지합니다.
- 브로커의 Spring 계정에는 기존 v1 읽기와 v2 `telemetry/metrics`, `telemetry/heartbeat`
  읽기 권한이 필요합니다.

## Linux 프로세스 수집 상태

`GET /api/v1/vms/{vmId}/processes`의 `items`와 함께 같은 표본의 `process_collection`을 반환합니다.
`status`는 `first_sample`, `ok`, `partial`, `read_failed`, `disabled`입니다.
`discovered`, `read_ok`, `vanished`, `failed`, `truncated`, `error`는 Agent 보고값입니다.
`truncated`는 Agent의 목록 크기 제한을 나타내며 API 조회의 `limit`과 독립적입니다.
보고가 없는 구형 Agent나 과거 DB 행의 값은 `null`이며 빈 표만으로 수집 결과를 판단할 수 없습니다.
표본 만료는 기존 `stale` 필드로 확인합니다. Flyway V8은 기존 프로세스 스냅샷에 nullable 열을 추가합니다.

## 테스트

Windows:

```powershell
.\mvnw.cmd test
```

Linux/macOS:

```bash
./mvnw test
```
