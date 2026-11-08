# B안 guest display (S15P31E110-410 / 411)

표시 domU가 그린 화면을 dom0가 받아 HDMI에 내보내는 경로의 **배포 예제**다. 이 디렉터리의 파일은 아직 이미지에 설치되거나 enable되지 않는다. 설계는 [`docs/design/display-plan-b-domu-render-relay.md`](../../../../../../docs/design/display-plan-b-domu-render-relay.md)를 본다.

## 구성

| 위치 | 프로세스 | 역할 |
| --- | --- | --- |
| 표시 domU | `python3 -m xenova_display.guest_sender --bind <domU IP> --token-file ...` | 자기 X 화면을 MJPEG로 제공 (domU 이미지 작업 필요) |
| dom0 | `xenova-display-guest-relay.service` → `xenova_display.guest_frames` | domU 프레임 수신·검증 → `127.0.0.1:8084` relay, domU용 읽기 전용 데이터 `192.168.77.1:8085` |
| dom0 HDMI | 기존 `xenova-display-hdmi.service` → kiosk | `display-runtime.json`의 `renderer`가 `guest_frames`이면 Chromium 대신 `frame_presenter`를 출력마다 실행 |

## dom0 HDMI를 guest_frames로 바꾸는 설정

`/etc/xenova/display-runtime.json`에 아래 필드를 추가한다. 기본값은 `chromium`이라 추가하지 않으면 기존 동작 그대로다.

```json
{
  "renderer": "guest_frames",
  "guest_relay_port": 8084,
  "guest_relay_token_file": "/etc/xenova/guest-display/relay.token",
  "frame_decoder": "turbojpeg",
  "native_jpeg_library": "/opt/xenova-stream/RELEASE/native/libturbojpeg.so.0.2.0",
  "guest_stale_ms": 2000
}
```

- `frame_decoder: turbojpeg`는 257에서 검증한 TurboJPEG 라이브러리를 쓴다. 라이브러리가 없으면 `ffmpeg`를 쓸 수 있지만 한 프레임(5 fps 기준 약 0.2초) 늦게 보이고 첫 프레임은 보이지 않는다.
- 상태 파일(`display-runtime.json` 상태) 형식은 그대로라서 `display_mode status/hdmi/mobile`은 바뀌지 않는다. 출력마다 `renderer_kind`, `content_status`(`live`/`unavailable`), `content_error`가 추가된다.

## 토큰 파일

| 파일 | 소유·권한 | 읽는 쪽 |
| --- | --- | --- |
| `guest-stream.token` | root, 0600 | dom0 relay (domU `guest_sender`에도 같은 값, domU 안에서 0600) |
| `relay.token` | root:xenova-display, 0640 | dom0 relay, kiosk의 `frame_presenter` |
| `guest-data.token` | root, 0600 | dom0 relay (domU 화면 앱에도 같은 값) |

토큰 값은 Git·문서·로그에 넣지 않는다.

## 아직 남은 것 (다른 담당 협의 필요)

- 표시 domU 이미지: Xvfb/Xorg dummy, Chromium kiosk, ffmpeg, `guest_sender` unit — 게스트 템플릿 담당.
- yocto `xenova-display` recipe에 이 unit·예제 설치 — recipe 담당.
- `192.168.77.1:8085`를 출발지 `192.168.77.20`만 허용하는 방화벽 규칙 — 배포 담당.
- HDMI 1대만 연결된 보드에서 `display_mode`가 준비 완료로 판정하려면 출력 1개 판정 변경이 필요하다 (379/380/390).
