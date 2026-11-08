# B안 guest display (S15P31E110-410 / 411 / 412 / 413)

HDMI 선택지에서 **표시 domU가 화면을 그리고**, dom0는 완성된 화면을 받아 HDMI 1대에 내보내는 경로의 **배포 예제**다. 이 디렉터리의 파일은 아직 이미지에 설치되거나 enable되지 않는다. 설계는 [`docs/design/display-plan-b-domu-render-relay.md`](../../../../../../docs/design/display-plan-b-domu-render-relay.md)를 본다.

- 상태 수집·정리·화면 결정(policy)은 dom0에 둔다. domU는 정리된 데이터를 읽기만 하고 그린다.
- 휴대폰 선택지는 이 경로를 쓰지 않는다 (폰이 직접 렌더링).

## 구성

| 위치 | 서비스 → 프로세스 | 역할 |
| --- | --- | --- |
| 표시 domU | `xenova-guest-display-app.service` → `xenova_display.guest_app` | `127.0.0.1:8086`. vm-screens 빌드 파일 제공 + `GET /api/v1/display/presentation` 하나만 dom0 guest data로 전달 (토큰은 페이지에 없음) |
| 표시 domU | `xenova-guest-display-session.service` → `xenova_display.guest_session` | Xvfb `:0`(1280×720) + Chromium kiosk로 `http://127.0.0.1:8086/display/mobile`을 그림 |
| 표시 domU | `xenova-guest-display-sender.service` → `xenova_display.guest_sender` | `:0` 화면을 캡처해 `192.168.77.20:8090`에서 인증된 MJPEG 제공 |
| dom0 | `xenova-display-guest-relay.service` → `xenova_display.guest_frames` | domU 프레임 수신·검증 → `127.0.0.1:8084` relay. domU용 읽기 전용 데이터 `192.168.77.1:8085` |
| dom0 HDMI | 기존 `xenova-display-hdmi.service` → kiosk | `display-runtime.json`의 `renderer: guest_frames`면 Chromium 대신 `frame_presenter` 실행 |

domU 파일은 [`domu/`](domu/)에 있다: 서비스 3개, `xenova-guest-display.target`, 설정 예제 `guest-display-domu.json.example`.

## dom0 설정 (HDMI 1대 + guest_frames)

예제: [`examples/single-hdmi-guest-frames-runtime.json`](../../examples/single-hdmi-guest-frames-runtime.json). `/etc/xenova/display-runtime.json`으로 두고 EDID를 실제 값으로 바꾼다. `renderer`를 빼면 기존 Chromium 동작 그대로다.

- `frame_decoder: turbojpeg`는 257에서 검증한 TurboJPEG 라이브러리를 쓴다. 라이브러리가 없으면 `ffmpeg`를 쓸 수 있지만 한 프레임(5 fps 기준 약 0.2초) 늦게 보이고 첫 프레임은 보이지 않는다.
- 상태 파일 형식은 그대로라서 `display_mode status/hdmi/mobile`은 바뀌지 않는다. 출력마다 `renderer_kind`, `content_status`(`live`/`unavailable`), `content_error`가 추가된다.

## domU 설정

`/etc/xenova/guest-display-domu.json` (예제 [`domu/guest-display-domu.json.example`](domu/guest-display-domu.json.example)):

| 필드 | 기본값 | 의미 |
| --- | --- | --- |
| `web_root` | 필수 | vm-screens 빌드 결과(`index.html`, `assets/`) 위치 |
| `page` | `/display/mobile` | HDMI에 그릴 화면. 휴대폰과 같은 화면을 가로 비율로 쓴다. 팀원 새 화면이 생기면 여기만 바꾼다 |
| `data_host` / `data_port` | `192.168.77.1` / `8085` | dom0 guest data 경로 |
| `display`, `width`, `height` | `:0`, 1280, 720 | domU 가상 화면 |
| `chromium` | `/usr/bin/chromium` | Debian chromium 패키지 경로 |

## 토큰 파일

| 위치 | 파일 | 소유·권한 | 읽는 쪽 |
| --- | --- | --- | --- |
| dom0 | `guest-stream.token` | root, 0600 | relay (domU sender와 같은 값) |
| dom0 | `relay.token` | root:xenova-display, 0640 | relay, kiosk의 `frame_presenter` |
| dom0 | `guest-data.token` | root, 0600 | relay의 guest data 경로 (domU 화면 앱과 같은 값) |
| domU | `/etc/xenova/guest-display/guest-stream.token` | xenova-display, 0600 | `guest_sender` |
| domU | `/etc/xenova/guest-display/guest-data.token` | xenova-display, 0600 | `guest_app` |

토큰 값은 Git·문서·로그에 넣지 않는다.

## 아직 남은 것 (다른 담당 협의 필요)

- 표시 domU 이미지: 패키지·`xenova-display` 계정·위 unit·설정·토큰 배포 — 게스트 템플릿 담당. 요청서: [`docs/requests/2026-11-09-display-domu-renderer-platform.md`](../../../../../../docs/requests/2026-11-09-display-domu-renderer-platform.md)
- VM3 메모리(현재 512 MiB) 확인 — 팀 결정.
- yocto `xenova-display` recipe에 dom0 relay unit 설치 — recipe 담당.
- `192.168.77.1:8085`를 출발지 `192.168.77.20`만 허용하는 방화벽 규칙 — 배포 담당.
