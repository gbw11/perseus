# 411 B안 dom0 HDMI 프레임 출력기 — 로컬 검증 (2026-11-08)

관련: [S15P31E110-411](https://ssafy.atlassian.net/browse/S15P31E110-411) · 선행: [410 로컬 검증](display-410-local-path-20261108.md) · 설계: [display-plan-b-domu-render-relay.md](../design/display-plan-b-domu-render-relay.md)

## 범위

**로컬 PC(WSL Ubuntu)에서만 확인했다.** Xvfb 두 개가 각각 표시 domU 화면(`:97`)과 dom0 HDMI X 서버(`:98`, 1280×720 출력 2개)를 대신한다. Xen, 보드, 물리 HDMI 모니터, 실제 차량 UI, TurboJPEG 실제 디코드는 확인하지 않았다.

- 브랜치: `feat/platform/S15P31E110-411-hdmi-frame-output` (410 커밋 `46563a64` 위)
- Python 3.14 venv, ffmpeg, Xvfb, xrandr

## 만든 것

| 파일 | 역할 |
| --- | --- |
| `xenova_display/native_jpeg.py` `JPEGDecoder` | TurboJPEG로 JPEG를 출력기 공유 버퍼에 바로 BGRX 디코드 (프레임마다 할당 없음, 출력 크기와 다르면 거부) |
| `xenova_display/native_x11.py` `SharedPresenter` | override-redirect 창 + MIT-SHM으로 프레임 표시. 커서 숨김. 상태 화면(core font ASCII) |
| `xenova_display/frame_presenter.py` | relay에서 최신 프레임을 받아 표시. 2초 지나거나 끊기면 "GUEST DISPLAY UNAVAILABLE" 화면. 1초마다 다시 그려서 살아 있음을 보고 (stdout JSON) |
| `xenova_display/kiosk.py` | `renderer: guest_frames`이면 Chromium 대신 출력마다 `frame_presenter` 실행·감시·재시작. 상태 파일 형식 유지 (+`renderer_kind`, `content_status`, `content_error`) |
| `xenova_display/outputs.py` | `renderer`, `guest_relay_port`, `guest_relay_token_file`, `frame_decoder`, `guest_stale_ms` 추가. 기본값 `chromium` |
| `xenova_display/guest_frames.py` `main()` | relay 서비스 실행 진입점 (relay `127.0.0.1:8084`, domU용 데이터 `192.168.77.1:8085`) |
| `deploy/guest-display/` | unit·설정 예제·README (설치·enable 안 함) |

`native_session.py`, `deploy/native/*`, yocto recipe 등 다른 팀원 파일은 바꾸지 않았다. 기존 HDMI 세션이 실행하는 kiosk가 설정에 따라 출력기를 고른다.

## 1. 시험

```bash
cd platform/services/display/router
python -m pytest -p no:cacheprovider -rs tests
```

결과: **452 passed, 1 skipped** (30.3초). skip은 기존 `test_viewer_stall.py`(playwright 미설치)다.

새 시험(`test_frame_output.py`, `test_native_presenter.py`, `test_guest_frames.py` 추가분)이 확인하는 것:

- **디코더**
  - BGRX·pitch 인자가 맞게 넘어간다.
  - 빈 프레임, 2 MiB 초과, 잘못된 pitch, 다른 해상도를 거부한다.
  - 닫은 뒤 사용하면 오류를 낸다.
  - 상대 경로 라이브러리는 로드하기 전에 거부한다.
- **출력 루프**
  - 대기 → live → 1초 갱신 → 2초 만료 시 수신 불가 화면으로 바뀐다. 수신 불가 상태에서 다시 그리기를 반복하지 않는다.
  - relay 인증 실패와 디코드 실패를 고정 코드로 표시한다.
  - 상태가 바뀌면 즉시 보고한다.
- **relay 수신**: 해상도가 다른 프레임은 버리고, 401은 `relay_auth_failed`로 남긴다.
- **ffmpeg 예비 디코더**: 실제 JPEG를 디코드한다. 한 프레임 늦게 나온다.
- **토큰 파일**: 그룹 읽기는 허용하고, 그룹 쓰기와 다른 사용자 읽기는 거부한다.
- **설정**
  - 기본값은 chromium이다.
  - guest_frames는 토큰 파일 절대경로가 있어야 하고, turbojpeg를 쓰면 라이브러리도 있어야 한다.
  - relay 설정 예제가 유효하다. 데이터 bind는 브리지 주소만 허용한다.
- **kiosk**
  - 출력마다 출력기를 실행하고 같은 형식의 상태를 쓴다.
  - 한 출력이 분리되면 그 출력기만 종료한다.
  - 출력기가 죽으면 다시 실행한다.
  - 실행 명령에 토큰이 없다.
- **Xvfb 실제 그리기**: 프레임이 출력 영역에만 그려진다. 수신 불가 화면의 배경색과 상단 붉은 띠가 보인다. 창을 닫으면 원래 화면으로 돌아간다.

## 2. 끝단 확인 (실제 kiosk 프로세스)

```bash
python tests/probe_hdmi_frame_output.py --save-dir /tmp
```

흐름: domU 역할 Xvfb `:97` → `guest_sender --source x11` → `GuestFrameReceiver`·relay → **`python -m xenova_display.kiosk`(renderer=guest_frames, decoder=ffmpeg)** → 출력마다 `frame_presenter` → HDMI 역할 Xvfb `:98`.

| 항목 | 결과 |
| --- | --- |
| 시작 후 두 출력에 domU 화면(파랑) 표시 | 2.37초 (kiosk·출력기 시작 + ffmpeg 한 프레임 지연 포함) |
| 상태 파일 | 두 출력 모두 `mapped`, `renderer_alive: true`, `renderer_kind: guest_frames`, `content_status: live` |
| domU 화면을 빨강으로 바꾼 뒤 HDMI 반영 | **0.78초** |
| domU 송신기 종료 후 HDMI 화면 | **1.57초** 뒤 "GUEST DISPLAY UNAVAILABLE" 화면 (2초 만료 기준, 송신기 종료 처리 시간 포함) |
| 송신기 종료 후 상태 파일 | 약 3.1초 뒤 `content_status: unavailable`, `content_error: frame_stale`. 출력기는 계속 `renderer_alive: true` |
| kiosk 상태 | 계속 실행 중, stderr 출력 없음 |
| 상태 파일에 토큰 | 없음 |

참고:
- 상태 파일의 `content_status`는 화면보다 최대 약 2초 늦다. kiosk의 출력 확인과 상태 쓰기가 각각 1초 주기이기 때문이다. 화면 자체는 2초 만료 기준으로 바로 바뀐다.
- 가상 출력이라 `physical_verified`는 항상 false다. 물리 HDMI EDID 확인은 보드에서 한다.
- 이번 확인은 ffmpeg 예비 디코더를 썼다. 보드의 기본은 257에서 검증한 TurboJPEG 라이브러리다. TurboJPEG 실제 디코드는 보드에서 확인해야 한다.

## 3. 아직 확인하지 않은 것

- 보드 dom0 Xorg `:5` + 물리 HDMI에서 표시, 화면 꺼짐 방지, EDID 확인.
- TurboJPEG 실제 디코드와 dom0 CPU·메모리 (Chromium kiosk 대비 비교).
- HDMI 1대만 연결된 상태에서 `display_mode hdmi` 판정. 지금 코드는 출력 2개를 모두 요구한다 (379/380/390).
- 표시 domU 이미지(Xvfb·Chromium·ffmpeg·`guest_sender`)와 실제 차량 UI 화면.
- (휴대폰 선택지는 폰이 직접 렌더링하므로 이 HDMI 프레임 출력기의 대상이 아니다.)
