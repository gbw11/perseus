# 이미지에 포함되는 dom0 실행 로그

`xenova-dom0-manager` 패키지는 기존 수집 코드를 재사용하는
`xenova-runtime-log-collector.service`를 설치하고 부팅 시 활성화한다.
이미 Manager가 포함된 `xenova-xen-bringup-image`에는 이미지 목록 변경 없이 들어간다.
기존 API·Recovery Worker unit, 서비스 토큰, 네트워크 설정은 교체하지 않는다.

## 실행 경로

등록된 dom0 system service의 journal → 기존 `dom0_manager.log_collector`
→ `/var/lib/xenova-runtime-logs/logs.sqlite3` → 기존 인증된 Manager 로그 API
→ 기존 Spring 중계 → 웹 실행 로그 패널.

Manager drop-in과 collector는 **같은**
`/etc/xenova/runtime-logs/collector.env`를 읽는다. 이미지의 이 파일에는 DB 경로만
있으며, 토큰·가짜 UUID·개발용 PYTHONPATH를 넣지 않는다. 디렉터리는 0700,
환경 파일과 DB는 0600이다. DB 디렉터리는 systemd StateDirectory로 만든다.
DB 경로를 변경하면 collector의 StateDirectory/ReadWritePaths도 함께 맞춰야 한다.

수집 대상은 unit ExecStart의 명시적 일곱 system service다:
Manager, Recovery Worker, Gateway, Mosquitto, display feed, HDMI launcher, mobile access.
이 unit을 조회한다고 시작·재시작하지 않는다. 꺼진/미설치된 서비스에서 로그가
나오지 않는 것은 정상이며 수집기 자체 상태와 개별 서비스 정상 여부는 다르다.

collector 하나와 journalctl 하나만 실행한다. 별도 HTTP 서버, MQTT 연결,
VM 상태 폴링, xl console, 키 입력 수집은 추가하지 않는다.
기존 24시간/20,000행/메시지 8,192자 제한과 journal cursor 재개·중복 제거를 사용한다.
이는 SQLite 파일의 엄격한 바이트 상한이나 실보드 CPU/RAM 측정값을 뜻하지 않는다.
낮은 CPU/I/O 우선순위로 동작하고 네트워크 소켓은 AF_UNIX로 제한한다.

## 포함되지 않는 로그

- Debian·Jetson 로그는 별도 소스 등록·송신기·인증된 ingest 경로가 필요하다.
- Zephyr printf와 주행 앱 로그는 펌웨어 담당의 실제 송신/중계가 필요하다.
- 다른 SSH 터미널의 출력, Docker stdout, systemd **user** unit 로그는 자동으로
  이 system-unit 수집 대상에 합쳐지지 않는다.
- 원격 ingest/shipper는 자동 설치·활성화하거나 새 포트로 노출하지 않는다.
  기존 `deploy/runtime-logs` 템플릿은 수동 연동용이며 예시 8084는 mobile 포트와
  겹칠 수 있으므로 그대로 기동하지 않는다.

## 빌드·배포 후 확인

빌드 환경에서 `bitbake -e xenova-dom0-manager`로 bbappend와 service 목록을 확인하고
`bitbake xenova-xen-bringup-image`로 새 이미지를 만든다. 소스 MR을 머지하는 것만으로
이미 운영 중인 보드/외부 서버가 변경되지는 않는다.

```sh
systemctl is-enabled xenova-runtime-log-collector.service
systemctl status xenova-runtime-log-collector.service --no-pager
systemctl show xenova-dom0-manager.service -p DropInPaths
journalctl -u xenova-runtime-log-collector.service -n 30 --no-pager
```

설치된 Python에서 sqlite3와 `dom0_manager.log_collector`를 import할 수 있고
journalctl이 존재하는지 확인한다. 기존 안전한 인증 도구로 Manager의
`GET /api/v1/logs/sources`, `GET /api/v1/logs?limit=1`을 조회한다.
서비스 토큰을 셸 명령 인수·스크린샷·문서로 노출하지 않는다.
collector 시작 직전 DB가 아직 없으면 API는 기존대로 503을 반환한다.
조용한 서비스라면 items가 비어 있어도 sources의 수집기 상태를 별도로 확인한다.
외부 웹에서 ‘선택 VM만’을 켜면 vm_id=null인 dom0 로그는 제외될 수 있다.

`systemctl stop xenova-runtime-log-collector.service`로 수집기만 중지할 수 있다.
이로 인해 VM·Manager·HDMI를 중지하지 않는다. Manager의 Wants 관계 때문에
Manager를 다시 시작하면 수집기도 다시 시작될 수 있다. 웹을 닫거나 끊어도
수집기는 계속 실행된다. journal에서 이미 삭제된 과거 로그는 복원할 수 없다.
