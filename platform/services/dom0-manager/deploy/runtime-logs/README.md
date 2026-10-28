# 실행 로그 수집 배포

웹 터미널은 기존 MO-04 경로를 유지한다. 이 수집기는 PTY·xl console·사용자 키 입력을 열거나 저장하지 않는다. 아래 unit은 설치 템플릿이며 아직 실보드에 배치/enable하지 않았다.

## dom0

1. 현재 Manager 코드에 로그 모듈을 배포한다. Python에는 sqlite3와 journalctl이 필요하다. minimal Yocto에 sqlite3가 포함됐는지는 실보드에서 별도 확인한다. API는 기존 Manager와 동일한 FastAPI 환경을 사용한다.
2. `@PYTHON@`을 실제 Python 경로로 치환한다. 예제 환경의 PYTHONPATH를 실제 설치 경로로 맞춘다. `/etc/xenova/runtime-logs`는0700, 환경·키·소스 설정 파일은0600으로 둔다.
3. collector와 기존 Manager 서비스 환경에 같은 `XENOVA_RUNTIME_LOG_DB`를 설정한다. collector를 먼저 실행한다. 기본 수집 대상은 Manager·Recovery Worker·표시 feed/graphics/kiosk 다섯 서비스다. 다른 서비스는 `--unit 실제이름.service`로 명시한다.
4. Manager의 기존 서비스 토큰·인증을 유지한다. API는 수집 DB가 없으면503으로 응답하며 빈 정상 목록으로 숨기지 않는다.
5. 게스트/Jetson의 전달이 필요하면 별도 수신기를 trusted bridge/VPN 주소에 명시적으로 바인딩한다. 이 수신기는 POST /api/v1/logs/ingest만 제공하며 VM 제어·로그 조회 API는 없다. 포트8084는 예제이며 네트워크/방화벽 설정은 자동 변경하지 않는다.

## 소스 등록

`sources.example.json`은 가짜 UUID와 무효 해시를 가진 예제다. 실제 VM UUID·unit 이름과 소스별 임의 토큰의 SHA-256을 등록한다. 한 소스의 토큰을 다른 VM에 재사용하거나 Manager 제어 토큰을 송신자에 배포하지 않는다. 수신기는 source_id→등록 vm_id/component로 강제 매핑하며 payload의 임의 VM ID·허용되지 않은 서비스는 거부한다.

토큰은 Python secrets.token_urlsafe(48) 등으로 별도0600 환경 파일에 저장하고, 원문은 해당 송신자의 root 전용 source.env에 `XENOVA_LOG_SOURCE_TOKEN`으로만 전달한다. 원문이나 private 환경 파일을 Git·URL·VITE 환경·채팅에 넣지 않는다. receiver에는 토큰 해시만 저장한다.

## Debian·Jetson

`dom0_manager/__init__.py`, `runtime_logs.py`, `journal_reader.py`, `log_collector.py`, `log_shipper.py`는 송신 측에서 표준 라이브러리만 사용한다. 이 다섯 모듈을 같은 패키지 경로로 배치하면 Agent/제어 코드를 바꾸지 않고 별도 서비스로 실행할 수 있다. receiver 설정과 동일한 source ID·실제 unit allowlist를 쓴다.

송신 unit의 `@INGEST_URL@`, `@SOURCE_ID@`, `@APP_UNIT@`을 바꾼다. HTTPS가 기본이며, 이미 합의한 내부 bridge/VPN HTTP를 사용할 때만 `@INTERNAL_HTTP_OPTION@`에 `--trusted-internal-http`를 넣는다. 그렇지 않으면 이 자리 자체를 지운다. 외부 redirect와 환경 프록시를 따라가며 키를 전달하지 않는다.

소스의 journal native cursor를 root 전용 checkpoint에 저장하고 수신 성공 후에만 진전시킨다. 네트워크 실패는 unit 재시작으로 재시도하며, 동일 source/boot/cursor는 receiver에서 중복 제거한다. journal 자체에서 이미 vacuum된 기록은 복구할 수 없다. 2초 빈 배치 heartbeat를 보내므로 조용한 서비스와 수집기 단절을 구분한다.

## 앱 출력 조건

별도 SSH에서 `python app.py`만 실행하면 해당 TTY 출력은 자동 수집되지 않는다. 앱을 systemd 서비스로 실행하고 `StandardOutput=journal`, `StandardError=journal`을 사용하거나, 알려진 unit으로 `systemd-run --unit=<name>`을 실행한 뒤 그 unit을 등록한다. `--pty`/`--pipe`로 출력이 TTY로 바뀌면 journal 수집과 다르다. 이 방식으로 명령은 웹 터미널에서 입력하고 서비스 출력은 공통 로그 패널에서 확인할 수 있다.

Spring이 Docker stdout에만 기록한다면 인프라 담당이 Docker/journal 전달 경로를 추가해야 한다. 기본 journal 수집기가 Docker 전체 컨테이너 파일이나 다른 SSH 세션을 몰래 열지 않는다. Zephyr는 systemd가 없으므로 기존 펌웨어 담당이 별도 로그 메시지 송신/중계를 연결해야 한다. 현재 변경이 Zephyr의 모든 printf를 자동 수집한다고 간주하지 않는다.

## 운영·중지

```sh
systemctl start xenova-runtime-log-collector
systemctl stop xenova-runtime-log-collector
systemctl start xenova-runtime-log-ingest
systemctl stop xenova-runtime-log-ingest
```

실제 배포 확인 전에는 enable하지 않는다. 수집기를 중지해도 VM·API·터미널·HDMI 서비스는 중지하지 않는다. 기본 DB 보존은24시간/20,000개, 메시지는8,192문자로 제한한다. 앱은 처음부터 비밀을 로그에 내보내지 않아야 하며, 공통 키/Authorization 마스킹은 보완 방어다.
