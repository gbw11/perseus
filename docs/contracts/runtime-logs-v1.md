# 실행 로그 수집·조회 v1

## 목적과 현재 범위

웹 터미널 입력과 실행 로그 조회를 함께 제공하되 서로 다른 연결로 유지한다. 기존 `/ws/v1/vms/{vm_id}/console`은 입력·출력 PTY 중계이며 변경하지 않는다. 로그 수집은 xl console을 추가 점유하지 않고 키 입력·명령 문자열·비밀번호를 감사 기록으로 저장하지 않는다.

```text
[dom0 service stdout/stderr → journal] → local collector → SQLite
[Linux guest / Jetson journal] → source-scoped HTTP shipper → private receiver → SQLite
SQLite → authenticated dom0 GET → Spring JWT GET proxy → browser log panel
```

웹 로그는 1초 간격의 커서 기반 증분 조회다. WebSocket 로그 스트림을 구현했다고 표기하지 않는다. 서비스가 코드에서 로그를 출력하지 않은 동작까지 자동으로 추론하지 않는다.

## 소스별 준비 상태

| 소스 | 제공한 경로 | 실제 배포 상태 |
|---|---|---|
| dom0 Manager·Recovery·HDMI 서비스 | unit allowlist journal collector | 로컬 WSL 실제 journal 검증, 보드 미배포 |
| Debian 앱·Agent | 별도 journal HTTP shipper | 모듈·unit 준비, 실제 unit 이름/UUID/키 배포 필요 |
| Jetson 인식·AI | 같은 Linux journal shipper | 모듈·unit 준비, 실제 주소·unit·키 연결 필요 |
| VM1·VM2 Zephyr | source-scoped 로그 수신 계약 | journal 없음. 기존 펌웨어/중계 담당의 송신 연동 필요 |
| 외부 Spring Docker stdout | 수신 계약·별도 소스 등록 지원 | Docker→journal 또는 컨테이너 로그 어댑터는 인프라 연동 필요 |

위 표의 미연동 소스까지 전체 로그가 현재 들어온다고 주장하지 않는다. 로그 패널은 등록된 수집 소스의 연결 상태를 보여준다. 서비스가 조용하다는 것과 수집기 단절은 다르다.

## 조회 API

브라우저→Spring과 Spring→dom0에서 동일한 경로를 사용한다. 브라우저는 기존 JWT, Spring은 기존 DOM0_MANAGER_SERVICE_TOKEN을 사용한다. 별도 브라우저 토큰이나 source key를 번들에 넣지 않는다.

- `GET /api/v1/logs?after=<id>&vm_id=<UUID>&source_id=<id>&component=<unit>&level=ERROR&limit=200`
- `GET /api/v1/logs/sources`

after 생략: 최신 최대 limit개를 오래된 순으로 반환한다. after 지정: 해당 정수 ID 다음부터 오래된 순으로 반환한다. limit=1..500, 기본200. 필터는 모두 선택 사항이며 지정한 값이 일치하는 기록만 반환한다.

```json
{
  "epoch": "persistent-log-database-id",
  "items": [{
    "id": 51,
    "event_id": "sha256(source_id, boot_id, native_id)",
    "source_id": "debian-vm3",
    "vm_id": "00000000-0000-4000-8000-000000000003",
    "component": "interaction-app.service",
    "boot_id": "guest-journal-boot-id",
    "observed_at": "2026-10-28T12:00:00+00:00",
    "received_at": 1793188800.0,
    "level": "ERROR",
    "message": "render task failed"
  }],
  "next_cursor": 51,
  "has_more": false,
  "history_truncated": false,
  "oldest_cursor": 1,
  "latest_cursor": 51
}
```

`has_more`는 after를 준 조회에서만 계산하며 after가 없으면 항상 false다. `oldest_cursor`는 보존 기간 안 기록이 없으면 null이다.

`GET /api/v1/logs/sources` 응답은 `{"epoch", "items": [{"source_id", "vm_id"(없으면 null), "components": [...], "state": "connected|disconnected|unavailable", "updated_at", "last_record_at"}]}`이다. `updated_at`·`last_record_at`은 epoch 초(없으면 null)다. 수신기 설정(sources.json)에만 있고 한 번도 보내지 않은 소스는 `unavailable`·두 시각 null로 붙는다. 기계 판독 스키마는 [dom0-manager OpenAPI](dom0-manager-openapi.yaml)의 `RuntimeLogPage`·`RuntimeLogSources`·`RuntimeLogIngestResult`이며 Spring은 dom0 본문을 그대로 전달한다.

observed_at은 소스가 보고한 발생 시각이고 received_at/id는 수신 순서다. guest boot_id는 기록의 출처이며 Xen 실행 세대 검증 근거로 쓰지 않는다. 서로 다른 소스의 시계가 정확히 동기화됐다고 가정하지 않는다. record 순서는 수신 id 기준이다.

재접속은 마지막 next_cursor로 조회하며 중복 id는 합친다. epoch가 달라지면 이전 커서를 버리고 최신 목록부터 다시 동기화한다. history_truncated면 보존 한도에 의한 누락을 사용자에게 알린다. 브라우저 버퍼는1,000개로 제한한다. 오류 시 이전 기록을 유지하되 연결 끊김으로 표시한다. 일시정지는 조회만 멈추며 수집기·VM·콘솔을 중지하지 않는다.

수집 DB가 미설정/미생성 상태면503, 인증 실패401, 잘못된 필터400이다. Spring은 보드 조회 장애를 LOG_SOURCE_UNAVAILABLE로 구별한다. DB 없는 빈 목록을 정상 수집으로 취급하지 않는다. 수집 source는2초 heartbeat, 10초 미갱신 시 disconnected다. 아직 한 번도 전송하지 않은 등록 source는 unavailable이다.

## 사설 송신 API

`POST /api/v1/logs/ingest`는 송신자→내부 수신기 경로다. Spring의 공개 브라우저 API는 이 POST를 중계하지 않는다. 전용 listener는 이 경로만 노출하고 VM 제어·로그 조회 기능을 제공하지 않는다. HTTPS 또는 명시적으로 신뢰한 내부 bridge/VPN을 사용한다. 내부 listener 기본 주소는127.0.0.1:8084이며 실제 guest bridge로의 bind/방화벽 변경은 배포 담당과 결정한다.

헤더:

- `Authorization: Bearer <source-only token>`
- `X-Log-Source: debian-vm3`

```json
{
  "records": [{
    "component": "interaction-app.service",
    "boot_id": "guest-boot-id",
    "observed_at": "2026-10-28T12:00:00Z",
    "level": "ERROR",
    "message": "render task failed",
    "native_id": "stable-journal-cursor-or-producer-event-id"
  }]
}
```

수신기는 root 전용 sources.json의 source→VM UUID/component allowlist/토큰 해시에 강제 매핑한다. payload가 VM UUID나 source ID를 임의 지정할 수 없다. 다른 source 키·허용하지 않은 component는 거부한다. read/control API에 source key를 쓰면401이다. 토큰 원문은 송신자의0600 환경 파일에만 전달한다.

배치는 최대32개·64KiB, 메시지는 최대8,192문자, level=DEBUG/INFO/WARN/ERROR/FATAL. 같은 source/boot/native_id는 중복 저장하지 않는다. 응답은 accepted/duplicates 개수다. 빈 records는 수집 연결 heartbeat이며 로그를 가짜로 추가하지 않는다. Linux 송신기는 문자열 직렬화 용량을 고려해 메시지를4,096자로 제한한다.

송신기는 수신 성공 후 native cursor를 체크포인트에 저장한다. 네트워크 실패/수신기 재시작은 checkpoint부터 재시도한다. 이미 journal에서 제거된 과거 기록은 복원하지 못하고, 이 경우 최신 bounded tail부터 다시 수집한다. 무손실 전체 감사 로그 보관 시스템으로 보장하지 않는다.

## 저장·출력 제한

기본24시간/20,000개 보존, 메시지8,192자. journal cursor/중복 인덱스를 사용하고 read 쿼리는 파라미터 바인딩한다. 등록된 unit만 journalctl로 읽으며 shell 명령이나 파일 경로를 사용자 조회 파라미터로 실행하지 않는다. JSON journal 레코드가64KiB를 초과하거나 텍스트 MESSAGE가 아니면 수집 대상에서 제외한다. ANSI/제어 문자를 제거하고 흔한 비밀번호·토큰·Authorization 값을 저장 전에 마스킹한다. 마스킹은 보완 방어이며 소스가 처음부터 비밀을 로그에 출력하지 않아야 한다.

브라우저는 로그를 HTML이 아닌 텍스트로 표시한다. 원래 앱의 여러 줄 오류/stack trace는 보존한다. 실제 VM 콘솔에 입력하는 기능이나 VM 제어 명령은 로그 API에 없다.

배포 단위·환경 변수·SSH로 실행한 앱을 서비스 로그로 내보내는 방법은 `platform/services/dom0-manager/deploy/runtime-logs/README.md`를 참고한다.
