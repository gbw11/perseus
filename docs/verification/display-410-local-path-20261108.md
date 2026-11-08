# 410 B안 최소 프레임 경로 — 로컬 검증 (2026-11-08)

관련: [S15P31E110-410](https://ssafy.atlassian.net/browse/S15P31E110-410) · 설계: [display-plan-b-domu-render-relay.md](../design/display-plan-b-domu-render-relay.md)

## 범위

**로컬 PC(WSL Ubuntu)에서만 확인했다.** Xen, 라즈베리파이 보드, 실제 domU, 실제 차량 UI(vm-screens), HDMI 모니터, S24는 쓰지 않았다. 로컬 통과를 실보드 수락으로 쓰지 않는다.

- 기준 커밋: `origin/platform` `215457f5` 위의 `feat/platform/S15P31E110-410-domu-render-relay`
- Python 3.14 venv (`outputs/venvs/display-374-integration`), FastAPI 0.162.2, uvicorn 0.54.0, pytest 9.2.0
- ffmpeg (x11grab 지원), Xvfb

## 1. 시험

```bash
cd platform/services/display/router
python -m pytest -p no:cacheprovider -rs tests
```

결과: **437 passed, 1 skipped** (27.7초).

- skip 1건: `test_viewer_stall.py`. 로컬 venv에 playwright가 없다. 기존 시험이며 이번 변경과 무관하다.
- 새 시험: `test_guest_frames.py`, `test_guest_sender.py`. 확인 내용:
  - 출처 주소 제한: 와일드카드·공인·멀티캐스트·loopback 거부. loopback은 시험용 플래그가 있을 때만 허용.
  - `jpeg_dimensions`가 잘린 데이터, SOS 선행, 버퍼 밖 길이를 거부한다.
  - 2 MiB 초과 프레임을 버리고 센다.
  - 해상도가 다른 프레임과 속도 초과 프레임을 표시하지 않는다.
  - 401·잘못된 Content-Type·500·연결 거부를 각각 고정 오류 코드로 남긴다.
  - 2초 지나면 `frame_stale`.
  - relay·guest data 앱이 인증을 요구하고, 토큰을 응답에 싣지 않는다.
  - guest data 앱은 경로 하나만 연다(다른 경로·POST·쿼리는 404).
  - 송신기 토큰 파일은 0600이어야 한다. 동시 스트림은 2개까지(3번째는 503).
  - 실제 소켓으로 송신기 → 수신기 종단을 확인했다. 송신이 멈추면 `unavailable`로 바뀐다.
  - ffmpeg `testsrc`로 640×480 프레임이 생성되는지 확인했다.
- 기존 `JpegParser` 시험(`test_outputs.py`)은 이동 후에도 그대로 통과했다.

## 2. 로컬 종단 확인 (Xvfb가 domU 화면 역할)

```bash
python tests/probe_guest_render_path.py --seconds 6 --save /tmp/relayed-frame.jpg
```

흐름: Xvfb `:97`(1280×720) 루트를 `xsetroot`로 칠함 → `guest_sender --source x11` → `GuestFrameReceiver` → `create_relay_app` → MJPEG 소비자.

| 항목 | 결과 |
| --- | --- |
| 중계된 프레임 | 6.18초 동안 32장, **5.17 fps** (설정 5) |
| 해상도 | 전부 1280×720 |
| JPEG 평균 크기 | 5,606 B (단색 화면이라 매우 작음. 실제 UI 크기 아님) |
| 파랑 화면 수신 | (24,115,231) ≈ `#1a73e8` |
| 화면을 빨강으로 바꾼 뒤 반영 | **795 ms 안**에 relay에서 빨강 확인 (측정 단위 0.6초 수집 반복 포함) |
| 송신기 종료 후 | 즉시 `unavailable` / `guest_stream_closed` |
| 거부·버림 | 0 / 0 |
| 상태 JSON에 토큰 | 없음 |

참고:
- WSLg는 `/tmp/.X11-unix`를 읽기 전용으로 마운트한다. 그래서 확인 도구는 Xvfb를 abstract socket(`-nolisten unix`)과 `-noreset`으로 띄운다. 실제 domU 구성과는 무관하다.
- 이번 확인은 단색 화면이다. 실제 Chromium 화면에서의 JPEG 크기·CPU·메모리는 domU 이미지 구성 후 보드에서 측정한다.

## 3. 아직 확인하지 않은 것 (보드·domU 필요)

- 실제 VM3(512 MiB, vCPU 1, pCPU {0,3}를 dom0와 공유)에서 Xvfb + Chromium + ffmpeg의 CPU·메모리.
- xenbr0 위 192.168.77.20 → 192.168.77.1 실제 전송, 브리지 위 다른 domU 영향.
- dom0 HDMI 프레임 출력기(미구현), 휴대폰 중계(미구현), `display_mode` 연동.
- 10분 연속, domU 재시작·모드 전환·케이블 분리 복귀.
- 출발지 제한 방화벽 규칙(미적용).
