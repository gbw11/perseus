# dom0 표시 파이프라인 (2~5단계)

HDMI 렌더러와 외부 웹이 함께 사용할 읽기 전용 상태 피드와 로컬 화면 선택 정책이다. 3단계 전체/상세 웹 렌더러에 이어 4단계에서 X11 실행기·실제 픽셀 캡처·원격 시연 전송을 추가했다. 실보드 HDMI 검증과 이미지 준비는 남아 있다. 기존 MO-04 터미널을 열거나 콘솔 출력을 상태 분석용으로 가로채지 않는다.

```text
기존 관리 서비스의 공유 상태 ┐
domU Agent/앱 ─ 로컬 MQTT ─┼─ dom0 display feed ─ 정책 ─ 계기판/인포테인먼트
권위 있는 제어 상태 ───────┘                 └ Spring ─ 외부 REST/WebSocket
```

## 실행

Python 3.11 이상에서 저장소 루트 기준으로 같은 가상환경에 설치한다. MQTT 소스는 paho 2.x를 사용한다.

```sh
python -m pip install ./platform/services/dom0-manager ./platform/services/vm-agent/mqtt-client ./platform/services/display/router
export XENOVA_OBSERVATIONS_PATH=/run/xenova/vm-observations.json
export XENOVA_DISPLAY_BINDINGS=/etc/xenova/display-bindings.json
# XENOVA_DISPLAY_TOKEN은 서비스 환경 파일 등으로 주입한다.
python -m uvicorn xenova_display.main:create_app --factory --host 127.0.0.1 --port 8082 --workers 1
```

동일 서비스 인스턴스의 상태를 사용하도록 단일 worker로 실행한다. 관리 서비스에도 같은 `XENOVA_OBSERVATIONS_PATH`를 설정하고 worker 1개로 실행해야 한다. 위 공유 모드를 명시한 수집기는 관리 서비스의 상태 파일만 읽고 Xen을 직접 조회하지 않는다. 경로를 설정하지 않으면 기존 드라이버 경로를 유지한다. 공유 파일이 실패하거나 만료되어도 직접 조회로 우회하지 않는다. 최종 공유 모드의 실보드 연속 검증은 별도이다. `xl console`, VM 시작/정지/재시작, 주행 명령은 실행하지 않는다. 토큰이 비어 있으면 시작을 거부한다. Python 경로/직접 실행 옵션은 기존 dom0-manager와 같다. [상태 공유·인증·적용 절차](../../../../docs/design/display-shared-observations.md)를 참고한다.

설정 파일은 [QEMU 예제](examples/qemu-bindings.json)를 참고해 실제 등록 UUID·앱 토픽에 맞춘다. `primary-drive`, `backup-drive`는 새 발행 기능을 설치했다는 뜻이 아닌 **수신할 앱 ID 설정 예시**다. 기존 발행자가 `autonomous`, `manual`을 쓴다면 설정에서 명시적으로 매핑할 수 있지만, 이 이름으로 VM 역할/현재 주행 모드를 추론하지 않는다. 같은 역할·UUID·앱 ID의 중복과 dom0 UUID는 거부한다.

| 환경변수 | 기본/의미 |
| --- | --- |
| `XENOVA_DISPLAY_BINDINGS` | 필수: JSON 매핑 파일 |
| `XENOVA_DISPLAY_TOKEN` | 필수: 내부 REST/WS 서비스 토큰 |
| `XENOVA_OBSERVATIONS_PATH` | 미설정 시 기존 경로 유지; 공유 모드 사용 시 관리 서비스와 같은 경로 지정 |
| `XENOVA_DISPLAY_MQTT_HOST` / `PORT` | `127.0.0.1` / `1883` |
| `XENOVA_DISPLAY_MQTT_USERNAME` / `PASSWORD` | 기존 브로커의 observer 읽기 전용 계정; 보드 진입점에서 필수 |
| `XENOVA_DISPLAY_CONTROL_SOURCE` | `dom0-gateway`: 제어 상태 발행 source_id |

MQTT는 기본적으로 dom0 loopback 브로커를 사용한다. 게스트별 토픽 발행권과 control 발행권은 브로커 계정/ACL로 제한해야 한다. 봉투의 `source_id` 검사만으로 발행자 인증을 대신하지 않는다. 실제 브로커 ACL이나 기존 메시지 계약은 변경하지 않는다.

사용자 선택에 따라 표시용 주행 상태는 **`xenova/v1/display/drive-state` / `type=display.drive_state` / 1초 주기 / QoS 1 / retain=false**로 분리했다. 수신 코드는 준비됐지만 실제 권위 발행자와 제어 담당의 발행/ACL 연결 합의는 남아 있다. 현재 브로커 `gateway` 계정에는 새 토픽 쓰기 권한이 없으므로 권한 반영 전 발행 가능하다고 가정하지 않는다. 기존 `xenova/v1/vehicle/control`은 구독/해석하지 않는다. [제어 담당 연결 규격](../../../../docs/contracts/display-drive-state-v1.md)을 따른다.

사용자 확정 1차 영상 목표는 출력별 1280×720·5 FPS, 지연 p95 ≤500ms, JPEG 두 출력 합계 ≤8Mbps, 송신기+인코더 CPU 평균 ≤1코어다. 실보드 달성 여부는 미검증이며 10 FPS는 후속 개선이다. [목표 및 미결정 항목](../../../../docs/contracts/display-250-decisions.md)을 참고한다.

## 인터페이스

253번 2단계에서 [UUID·앱 매핑 및 구독 수신 규칙](../../../../docs/contracts/display-source-bindings-v1.md)을 보완했다. [후보 설정](examples/integration-candidate-bindings.json)은 합성 UUID를 쓰며 기본 거부한다. 시험 시에만 `XENOVA_DISPLAY_ALLOW_CANDIDATE_BINDINGS=1`로 명시한다. 실제 배치 설정을 자동 교체하지 않는다. 토픽별 SUBACK/재접속 이후 새 보고를 확인하고, 구독 실패와 VM/앱 장애를 구분한다. [검증 결과](../../../../docs/verification/display-253-step2.md)는 실제 domU 연결 시험과 구분한다.

| 구간 | 엔드포인트 | 인증/데이터 |
| --- | --- | --- |
| dom0 내부 조회 | `GET /api/v1/display/state` | `Authorization: Bearer <서비스 토큰>` |
| dom0 내부 구독 | `WS /ws/v1/display/state` | handshake Authorization, 즉시 전체 스냅샷 이후 약 1초 간격 |
| Spring 외부 조회 | `GET /api/v1/display/state` | 기존 사용자 JWT Bearer |
| Spring 외부 구독 | `WS /ws/v1/display/state` | 첫 메시지 `{"type":"auth","access_token":"<JWT>"}` |

Spring은 아래 환경변수로 명시적으로 켠다. 기본은 비활성화이며 기존 서비스 실행에 새 수집기 연결을 강제하지 않는다.

```sh
export DISPLAY_FEED_ENABLED=true
export DISPLAY_FEED_BASE_URL=http://127.0.0.1:8082
# DISPLAY_FEED_SERVICE_TOKEN에는 dom0 XENOVA_DISPLAY_TOKEN과 동일한 값을 주입한다.
```

외부 WS는 `authenticated` 다음 `{"type":"display.state","state":{...}}`를 전달한다. 수집기에 연결할 수 없으면 `display.unavailable`을 보내고, 연결이 복구되면 최신 전체 스냅샷을 다시 전달한다. 외부 REST는 이 경우 503이다. 클라이언트는 unavailable 수신 시 화면의 이전 상태를 정상으로 유지하지 말아야 한다. 재접속 시 epoch·sequence 기준을 다시 잡는다. WS는 10초 안에 인증하지 않으면 4401로 닫히며 JWT 만료 시에도 닫힌다. 인증 후 제어/단말 입력은 1008로 거부한다. 서비스 토큰은 브라우저에 전달하지 않는다.

수집기 스냅샷의 `source_epoch`는 수집기 프로세스 세대, `sequence`는 전체 스냅샷 증가 번호다. `vms`에는 역할·UUID·Xen 상태·앱 상태·기대/관측 OS·소스별 수신 경과 시간을 포함한다. 상태 표시 규칙은 [계약](../../../../docs/contracts/display-pipeline-v1.md)에 설명한다.

## 3단계 로컬 화면 렌더링

`frontend`에서 `npm run build -w @xenova/vm-screens`로 빌드한 뒤 수집기에 `XENOVA_DISPLAY_WEB_ROOT=<vm-screens/dist 절대 경로>`를 지정한다. 두 화면은 `/display/monitor-1`(차량 계기판), `/display/monitor-2`(인포테인먼트·선택 게스트 콘텐츠)다. `outputs.screen`을 각각 `instrument`/`infotainment`로 고정한다. 이전 `view` 필드는 선택 API 호환용이다. 속도·배터리·네비·음악과 게스트 영상 어댑터가 연결되기 전에는 수신 대기를 표시한다. 외부 대시보드의 페이지 라우팅과는 별도의 dom0 로컬 화면이다. 새 화면은 mock telemetry나 기존 자동/수동 VM 시나리오에 연결하지 않는다. 기존 `/split`, `/detail/:app`는 이전 시연 화면으로 남아 있으며 HDMI의 기본 진입점은 아니다.

| 로컬 경로 | 용도/권한 |
| --- | --- |
| `POST /api/v1/display/session` | 신뢰된 실행기가 Bearer 서비스 토큰으로 브라우저 세션 준비 |
| `GET /api/v1/display/presentation` | 상태 + 출력 선택 + VM별 표시 결정. 서비스 토큰 또는 화면 쿠키 |
| `POST /api/v1/display/selection` | 모니터 2의 pinned/follow_active 설정. 서비스 토큰 전용 |
| `POST /api/v1/display/outputs/{output_id}/ack` | 해당 DOM의 렌더 revision 확인. 서비스 토큰 또는 화면 쿠키 |

화면 인증은 서비스 토큰을 번들/URL/페이지 JS에 넣지 않고, 신뢰된 실행기가 브라우저 컨텍스트의 요청 기능으로 session API를 호출해 준비한다. 발급된 HttpOnly·SameSite=Strict 쿠키는 최대 8시간이며 최대 32개 세션을 보관한다. 쿠키로는 화면·정적 자산·presentation 조회와 DOM 렌더 확인만 가능하고 selection 변경이나 기존 내부 state API/WS 접근은 불가하다. HTTPS에서는 Secure를 설정한다. 실제 키오스크 실행기/세션 갱신 자동화는 4단계 대상이다.

선택 변경 요청 예(현재 presentation의 epoch/revision을 사용):

```json
{"policy_epoch":"현재 policy_epoch","revision":1,"selection":"follow_active","vm_id":null}
```

`pinned`는 등록 UUID가 필수다. 잘못된 UUID/조합은 400, 이전 epoch/revision은 409, 요청 형식 오류는 422다. 기본은 VM1 pinned다. follow_active에서도 신선하고 명시적인 active_vm_id만 따라가며, 확인된 새 값이 없으면 마지막 선택을 유지한다. 장애·AUTO/MANUAL 변화만으로 다른 VM을 고르지 않는다.

254번 보완: `outputs[].reason`에 전체/상세 화면의 선택 이유를 제공한다. 상세 화면은 이를 표시하며, 표시 피드 자체가 끊기면 과거 선택임을 안내한다. follow_active의 대상은 등록된 주 구동·백업 VM으로 제한하며, Debian 인터랙션 VM을 활성 구동 대상으로 선택하지 않는다. Debian을 명시적으로 pinned하는 것은 가능하다. 앱 구독 경로·보고 신선도를 먼저 확인하므로 과거 `failed` 값만으로 현재 앱 오류를 표시하지 않는다. 보드 없는 상태 전이 검증과 미검증 범위는 [254 검증 기록](../../../../docs/verification/display-254-policy.md)에 정리했다.

presentation의 `revision`은 선택 설정 버전, `render_revision`은 실행 ID/상태·내용의 해시다. 렌더러는 두 animation frame 이후 epoch/revision/render_revision을 ACK한다. 이전 내용의 ACK는 409이며, 출력별 ACK는 3초 동안 갱신되지 않으면 만료된다. 응답의 `renderer_ack.stage=dom_rendered`는 DOM 렌더 확인이며 물리 HDMI `ready`나 캡처 성공을 의미하지 않는다. 물리 `applied_revision`/장치 연결 상태는 4단계에서 추가한다.

로컬 렌더러는 약 1초마다 same-origin presentation을 읽고 요청에 2.5초 제한을 둔다. 순차 요청으로 겹침을 막으며 중복/역순 스냅샷·퇴역한 수집기 epoch를 수락하지 않는다. 인증 실패·통신 오류·규격 오류에서는 마지막 값임을 명시하고 정상 연결/활성 주행 배지를 제거한다. 기본값은 미수신 대기 화면이다. 그래픽 소스 어댑터가 없으므로 앱 영역은 실제 상태 화면/미연결 안내를 표시한다.

개발 Vite 프록시는 `VITE_DISPLAY_FEED_ORIGIN`(기본 127.0.0.1:8082)으로 `/api/v1/display`만 직접 연결한다. 새 HDMI 화면은 기존 refresh 로그인·개발용 VITE_SCREEN_PASSWORD나 Spring 서버에 의존하지 않는다.

검증: `tests/probe_browser.cjs`는 Windows Chrome/Playwright + 기존 WSL/QEMU를 사용하는 별도 시험 도구다. 실제 Xen 조회와 합성 장애/주행 사례를 구분한다. 테스트 서버의 `/test/scenario` 경로는 `tests/preview_server.py`에만 존재하며 제품 앱에는 포함되지 않는다. [3단계 검증 기록](../../../../docs/verification/display-step3.md)을 참고한다.

## 4단계 X11 실행과 원격 시연

실보드 환경 확인은 `python3 tools/probe_board_environment.py`로 JSON을 수집할 수 있다. 기존 X11 세션을 알고 있으면 `--display :0`처럼 지정한다. 이 도구는 패키지 설치·모드 변경·서비스/VM 조작·GUI 실행을 하지 않으며, 수집 성공을 물리 HDMI 통과로 보고하지 않는다. [250 결정 기록](../../../../docs/contracts/display-250-decisions.md)에 제어 발행자·보고 주기·보드 환경·성능 목표의 확정 상태를 기록한다.

현재 구현 백엔드는 **X11/XRandR + Chromium + FFmpeg x11grab/MJPEG**다. Wayland/Weston에서는 이 실행기를 그대로 사용할 수 없다. 실제 dom0에 Xorg 세션·창 관리자와 GPU 드라이버, `xrandr`, Chromium, FFmpeg의 x11grab 및 MJPEG 인코더, 한글 글꼴이 있어야 한다. Python 설치에는 `python -m pip install './platform/services/display/router[display]'`로 Playwright를 추가하고 타깃용 Chromium 경로를 지정한다. 실제 Yocto 이미지에 이 구성이 설치돼 있다고 가정하지 않는다.

1. `python -m xenova_display.outputs --display :0`으로 실제 connector·지원 모드·EDID hash를 읽는다.
2. [설정 예제](examples/x11-runtime.json)의 connector/EDID/해상도/좌표를 실제 설치에 맞게 작성해 `/etc/xenova/display-runtime.json` 등으로 저장한다. 예제의 0으로 채운 hash는 교체 전 실제 장치와 매칭되지 않는다.
3. 수집기의 `XENOVA_DISPLAY_WEB_ROOT`를 3단계 빌드로 설정한다. 전용 그래픽 사용자에게 기존 `XENOVA_DISPLAY_TOKEN`과 X11 인증 환경을 제공한다.
4. 같은 그래픽 사용자 세션에서 키오스크를 시작한다.

```sh
python -m xenova_display.kiosk --config /etc/xenova/display-runtime.json \
  --status "$XDG_RUNTIME_DIR/xenova/display-runtime.json" --chromium /usr/bin/chromium
```

시작 시 매핑이 확인된 출력만 모드/좌표를 적용한다. 잘못된 EDID/지원하지 않는 모드로 다른 화면을 추정하지 않는다. 별도 브라우저 프로필로 두 창을 띄우고 native window bounds를 검사한다. 한쪽 분리/브라우저 종료는 해당 출력의 창만 정리·재시도하며, feed 접속 실패는 기존 창을 유지해 수신 중단 UI를 표시한다. 1시간마다 화면 쿠키를 갱신하고 feed 재시작의 401도 다시 인증한다. 브라우저의 자동 번역 팝업은 전용 임시 프로필에서 비활성화한다. 기존 개인 브라우저 프로필은 사용하지 않는다.

원자적으로 쓰는 runtime JSON에는 layout_epoch, 갱신 단조 시각, connector·좌표·renderer_alive·DOM revision을 기록한다. `applied_revision`은 실제 x11 backend에서 매핑과 창 위치, 신선한 DOM ACK가 함께 확인됐을 때만 설정한다. 이는 **X11 설정/DOM 확인 값이며 실모니터 육안 검증 완료를 의미하지 않는다**. 가상 backend는 항상 physical_verified=false, applied_revision=null이다.

송신기는 별도 실행한다. feed 서비스 키와 구별되는 **원격 시연 전용 접속 키**를 `XENOVA_STREAM_TOKEN`으로 주입하고 아래 환경을 준비한다.

```sh
export XENOVA_DISPLAY_RUNTIME_CONFIG=/etc/xenova/display-runtime.json
export XENOVA_DISPLAY_RUNTIME_STATUS="$XDG_RUNTIME_DIR/xenova/display-runtime.json"
python -m xenova_display.stream --host 127.0.0.1 --port 8083
```

송신기는 runtime 상태가 6초 이상 지연되거나 매핑/renderer가 유효하지 않으면 해당 프레임을 제공하지 않는다. FFmpeg는 출력별 실제 X11 root 사각형을 읽으며 웹 URL/별도 headless 페이지를 캡처하지 않는다. JPEG는 출력별 최대 2MiB·최신 1장만 보관하며 느린 수신자를 위해 과거 프레임 큐를 쌓지 않는다. 인코더가 종료되면 해당 인코더만 재시도한다. 키오스크와 송신기 사이에는 프로세스 종료 의존성이 없다.

실제 Capture는 프레임 생성·무효화 알림을 모든 대기 수신자에게 전달한다. 수신자는 새 프레임을 읽을 때마다 인증·출력 유효성을 재검사하고, 알림이 없어도 최대 0.5초마다 인증·종료·연결 상태를 다시 확인한다. 프레임별 대기열은 만들지 않으며 여러 수신자가 하나의 알림을 서로 소비하지 않는다. 기존 동기식 진단 수집기에는 주기 대기를 유지한다. [257 분리 계측·가속 후보 검증](../../../../docs/verification/display-257-native-jpeg-20261030.md)은 임시 라이브러리 시험과 제품 기본 경로를 구분한다.

시연 PC에서는 SSH 포워딩 등 프로젝트의 신뢰된 연결로 8083에 접근하고 `http://127.0.0.1:8083/demo`를 연다. 예를 들어 `ssh -N -L 8083:127.0.0.1:8083 <dom0 사용자>@<dom0 주소>`로 접속할 수 있다. 뷰어에서 시연 접속 키를 입력하면 1시간짜리 HttpOnly/SameSite=Strict 쿠키를 발급한다. HTTPS에서는 Secure다. 접속 키는 URL·번들·캡처 프레임에 넣지 않는다. 뷰어는 수신 JPEG만 canvas에 디코딩하며 VM 상태 JSON으로 화면을 재구성하지 않는다. 실제 디코드가 3초 이상 중단되거나 상태 조회가 실패하면 마지막 프레임임을 표시한다.

| 송신기 경로 | 역할 |
| --- | --- |
| `GET /demo` | 접속 키 입력·두 캡처 화면 수신 |
| `POST /auth` | 시연 키 확인/쿠키 발급 |
| `GET /api/v1/streams` | 출력·캡처 세대·프레임 번호/나이·연결 상태 |
| `GET /streams/{output_id}.mjpeg` | 해당 출력의 JPEG multipart 스트림 |

스트림 상태/프레임은 쿠키 또는 별도 시연 Bearer 키가 필요하다. query token은 지원하지 않는다. 스트림 도중에도 쿠키 만료를 확인한다. 프레임과 DOM revision의 정확한 대응은 아직 미검증이므로 metadata의 render_revision=null, render_correlation=unverified다.

사용자 systemd 템플릿은 [키오스크](deploy/xenova-display-kiosk.service), [송신기](deploy/xenova-display-stream.service)에 있다. 둘 다 같은 그래픽 사용자로 실행하고 환경 파일 권한을 0600으로 관리한다. `%h/.config/xenova/display.env`는 feed 키/XAUTHORITY, `stream.env`는 별도의 시연 키/XAUTHORITY를 제공한다. Python 가상환경·Chromium 경로는 실제 설치 경로로 맞춰야 한다. 이 템플릿은 실제 보드에 설치/활성화하지 않았다.

시험 전용 `backend=virtual_x11`은 `--allow-virtual` 또는 `XENOVA_STREAM_ALLOW_VIRTUAL=1`을 명시해야 한다. `--test-no-sandbox`는 virtual backend에서만 허용한다. 이는 실제 HDMI 성공으로 기록되지 않는다. `tests/probe_display_runtime.py --execute --output <디렉터리>`로 기존 WSL/QEMU 환경에서 가상 두 화면 → 실제 캡처 → 원격 디코드 시험을 재현할 수 있다. [4단계 검증 기록](../../../../docs/verification/display-step4.md)을 참고한다.

## 5단계 통합·연속 동작 검증

기존 WSL/QEMU 실험실에서 아래 명령으로 4단계 수락 시험에 장애 복구와 10분 연속 관측을 추가한다. 테스트용 `psutil`도 필요하다. 제품 의존성에는 추가하지 않는다. 실행 디렉터리는 이 router 디렉터리이며 QEMU SSH 설정은 시험 도구의 기존 실험실 경로를 사용한다.

```sh
PYTHONPATH=.:../../dom0-manager:../../vm-agent/mqtt-client \
python -B tests/probe_display_runtime.py --execute --soak-seconds 600 \
  --output /tmp/display-step5-evidence
```

독립 Xvfb·키오스크·인코더·수신 브라우저를 만들고 실제 QEMU Xen을 읽는다. query adapter 예외, 로컬 상태 HTTP 차단, 한쪽 native 브라우저 컨텍스트 종료, 원격 브라우저 offline/online을 시험한다. 실제 guest 정지/장애 명령이나 기존 X 디스플레이 변경은 수행하지 않는다. 마지막에 시험용 프로세스를 정리한다.

`evidence.json`은 수락 결과/복구 시간/측정 요약, `soak-samples.jsonl`은 5초 간격 원본이다. 연속 시험의 live는 창·HTTP 상태 채널·프레임 생성·원격 수신 상태이며 모든 VM/앱이 정상이라는 뜻이 아니다. 전체 시험 프로세스 트리 CPU/RSS에 수신 Chromium도 포함되고, RSS 합계에는 공유 페이지 중복이 있다. 색상 변화 지연은 동일 호스트에서 200ms 간격으로 확인한 값으로 실보드/원격망 성능을 대체하지 않는다.

[5단계 결과](../../../../docs/verification/display-step5.md)와 [실보드 수락 절차](../../../../docs/verification/display-hardware-acceptance.md)에 가상 검증과 남은 물리 시험을 구분한다. systemd 프로세스 단위 재시작과 실제 HDMI 분리/재연결은 실보드 절차에서 확인한다.

## 관측의 한계

- Xen 조회 성공 시에만 목록에서 사라진 등록 VM을 `stopped`로 표시한다. 조회 실패는 `disconnected`, 미수신은 `unknown`이다.
- Xen 수신 유효기간 3초, 앱 heartbeat 3초, Agent heartbeat 15초를 사용한다. 모든 경과 시간은 dom0 단조 시계로 계산한다. inventory는 30초 이후 stale로 표시하지만 heartbeat와 독립된 과거 근거로 유지한다.
- 실행 domid가 바뀌거나 부팅 ID가 변경되면 이전 앱·OS 자료를 비운다. inventory만으로 새로운 부팅을 확정하지 않는다. 먼저 비-retained heartbeat를 받아야 한다. 이전 boot/프로세스 epoch·중복/역순 seq는 버린다.
- `run_id`는 표시 수집기·관리 상태 제공자의 세대와 관측 실행 식별자(제공 시) 또는 domid를 묶는다. 시작 세대가 없는 드라이버는 동일 domid의 빠른 교체를 모두 증명하지 못한다. `guest_boot_id`는 게스트 보고값이며 OS의 `current_run_verified`는 항상 false다. 관측 식별자만으로 현재 실행과 OS 근거가 확실히 결합됐다고 표시하지 않는다.
- 프로세스 수명 내 소스별 retired epoch는 128개까지만 추적한다. 한도 이후 새로운 세대는 수락하지 않고 운영 재시작/점검이 필요하다. 임의 발행자 수를 누적하지 않도록 등록된 세 VM·앱·지정된 control만 구독한다.
- Zephyr Starter는 현재 앱 heartbeat를 보내지 않는다. 이때 앱/관측 OS는 unknown이고 기대 OS와 실제 Xen 실행 상태만 표시한다. 모터/미러링/승계 기능이 구현됐다고 표시하지 않는다.
- `display.drive_state`의 명시적 `active_vm_id`와 `mode`를 따로 전달한다. `active_app`이나 구형 `vehicle.control`로 VM을 추측하지 않는다. retained 재생 보고는 현재 상태로 수락하지 않으며 3초 수신 지연 시 mode=unknown이다. AI 상태는 아직 unknown이다.

## 검증

수집기 디렉터리에서 `python -m pytest`를 실행한다. Spring은 `backend`에서 `./mvnw -Dtest=DisplayIntegrationTest,ConsoleWebSocketIntegrationTest test`로 검증한다.

`tests/probe_display_control.py --execute --output <결과.json>`은 loopback 브로커를 띄워 별도 표시 상태 보고 → 실제 MQTT 수신 → REST/WS·상세 정책 연결을 시험한다. 기존 테스트 런타임의 amqtt/paho/httpx/websockets가 필요하며 PYTHONPATH는 위 runtime probe와 같다. 제어 엔진/Xen 관측은 합성이고 운영 ACL·실보드 검증이 아니다. [250 후속 검증 결과](../../../../docs/verification/display-250-followup.md)를 참고한다.

`tests/probe_qemu.py`는 기존 격리 실험실 전용이며 일반 테스트에서 자동 실행되지 않는다. `--execute --output <증거 파일>`이 필요하다. 시험 호스트에 amqtt 0.11.3/pexpect, 기존 ARM64 휠과 Java 21/Spring 컴파일 결과가 있어야 한다. 실제 dom0의 임시 디렉터리에 수집기를 올리고 실제 Debian은 메모리에서만 임시 보고기를 실행한다. 테스트 브로커는 호스트 loopback에 있고 SSH 포워딩을 사용하므로 제품 배포의 로컬 Mosquitto 토폴로지와 구별한다. 기존 VM/이미지/서비스·실행 도메인 ID를 바꾸지 않는다. 결과는 [검증 기록](../../../../docs/verification/display-step2.md)을 참고한다.


## 전용 X11 절전 방지와 시연 인코딩

키오스크의 `--prevent-idle-blank`는 전용 차량 화면 세션에서만 사용한다. `xset`으로 기존 screen saver/DPMS 값을 읽고, screen saver timeout과 blanking 및 지원되는 DPMS를 끈 뒤 적용 결과를 확인한다. 일반 종료 시 기존 값을 복원한다. 공유 데스크톱에는 이 옵션을 사용하지 않는다. 강제 종료(SIGKILL)·전원 차단은 복원을 보장하지 않으며, 새 전용 X 서버 세션의 초기 설정을 다시 적용해야 한다. 배포 예시 unit은 이 옵션을 명시한다. 실제 보드의 상시 서비스에는 아직 설치하지 않았다.

`outputs[].width/height`는 X11 원본 캡처 영역이다. 별도의 `stream_width/stream_height`는 기본 1280×720인 전송 JPEG 크기이며 짝수만 허용한다. 원본 전체를 비율 유지 축소하고 검은 여백을 채우므로 영역을 잘라내지 않는다. 스트림 metadata의 `rectangle`은 원본 영역, `width/height`는 인코딩 결과 크기다. MJPEG는 yuvj420p, encoder 1 thread와 단순 filter pipeline 1 thread를 사용한다. encoder의 `-threads`와 filter의 `-filter_threads`는 별도 설정이다([FFmpeg 공식 문서](https://ffmpeg.org/ffmpeg.html)).
