# 표시 B안 — domU 렌더링 · dom0 중계 (S15P31E110-410)

작성: 2026-11-08 · 상태: **설계 + 로컬 최소 경로 검증까지**. 보드·domU 이미지·VM 자원은 바꾸지 않았다.

## 1. 결정

표시 선택지는 **둘 중 하나**다: **HDMI 모니터 1대** 또는 **휴대폰(S24)이 직접 렌더링**. 계기판·인포테인먼트는 선택한 단말의 한 화면에 함께 둔다.

**HDMI 선택지는 B안으로 간다.** 표시 담당 domU가 화면 앱을 실행하고 **픽셀 렌더링까지** 한다. dom0는 완성된 프레임을 **받아서 HDMI 1대에 내보내기만** 한다.

**휴대폰 선택지는 지금처럼 폰 브라우저가 직접 렌더링한다.** dom0가 영상을 중계하는 대상이 아니다 (기존 349·350·383·386 경로 유지).

- HDMI는 1대만 쓴다. 물리 HDMI 2대로 되돌리지 않는다.
- HDMI 기본 기동과 터미널 `status/mobile/hdmi` 왕복 전환은 유지한다.
- 상태 수집을 domU에 새로 만들지 않는다. domU는 dom0의 기존 표시 데이터를 **읽기 전용**으로만 받는다.
- 374(공유 VM 관측 freshness)는 별개 문제다. 구조를 바꿨다는 이유로 완료 처리하지 않는다.

## 2. 현재 경로와 B안 비교

| 항목 | 현재 (platform `215457f5`) | B안 |
| --- | --- | --- |
| 화면 앱(vm-screens) 실행 | dom0가 웹 자산 제공 (`xenova-display` → `/usr/share/xenova/display/web/`) | 표시 domU 안에서 실행 |
| 픽셀 렌더링 | HDMI: dom0 Chromium(X11 `:5`) / 휴대폰: 폰 브라우저 | **표시 domU** (Xvfb 또는 Xorg dummy 위 Chromium) |
| 화면 데이터 원본 | dom0 표시 feed `127.0.0.1:8082` | 같음. domU는 dom0 **guest data relay**로 읽기 전용 접근 |
| HDMI 출력 | dom0 Chromium 창을 직접 표시 | dom0가 domU 프레임을 받아 표시 (프레임 출력기 — 후속) |
| 휴대폰 | 폰이 HTML/JS를 받아 직접 렌더링 | **그대로** (B안 대상 아님) |
| dom0에 남는 일 | 수집·정책·웹 자산·렌더링·(시연 영상 캡처) | 수집·정책·프레임 수신 검증·HDMI 표시 (폰 모드는 기존 데이터·자산 제공) |
| 경계 | 서비스 토큰은 dom0 loopback 안 | 같음. domU에는 **별도 guest 토큰**만 |

## 3. B안 구성

```text
dom0 (192.168.77.1, xenbr0)                         표시 domU (후보: VM3 debian-dev, 192.168.77.20)
───────────────────────────────                     ─────────────────────────────────────────────
표시 feed 127.0.0.1:8082 (기존, service token)
   ▲ LocalFeed가 service token을 붙임
guest data relay 192.168.77.1:<port>  ◀── guest data token ──  화면 앱 (Chromium kiosk, X :0)
   GET /api/v1/display/presentation 하나만                         │ 렌더링된 화면
                                                                     ▼
GuestFrameReceiver  ◀── guest stream token ──  guest_sender: x11grab → MJPEG
   검증: 크기·JPEG 구조·해상도·속도·만료          192.168.77.20:8090 /frames.mjpeg
   │ latest frame 1장
   ▼
relay 127.0.0.1:<port> /streams/display.mjpeg  (relay token)
   └─ HDMI 1대 프레임 출력기  (411)

휴대폰 선택지: 기존 mobile_access가 폰에 데이터·웹 자산을 주고 폰 브라우저가 직접 그린다 (변경 없음).
```

방향: **dom0가 domU에 접속해서 당겨온다(pull).** domU는 dom0 내부 서비스에 쓰기 권한이 없고, dom0는 domU가 보낸 것을 그대로 믿지 않는다.

## 4. 이번 티켓에서 만든 것

| 파일 | 역할 |
| --- | --- |
| `platform/services/display/router/xenova_display/mjpeg.py` | 표준 라이브러리만 쓰는 `JpegParser`(기존 `stream.py`에서 이동, 크기 초과 개수 기록 추가)와 `jpeg_dimensions` (SOF 헤더만 읽는 제한된 검사) |
| `.../xenova_display/guest_sender.py` | **domU 쪽.** 자기 X 화면을 캡처해 인증된 latest-frame MJPEG 하나로 제공. 표준 라이브러리 + ffmpeg만 사용 (pip 불필요). 사설 IPv4 하나에만 bind, 동시 스트림 2개 제한, 토큰 파일 0600 강제, `testsrc`는 `synthetic: true`로 표시 |
| `.../xenova_display/guest_frames.py` | **dom0 쪽.** `GuestFrameReceiver`(당겨오기·검증·만료), `create_relay_app`(dom0 로컬 소비자용), `create_guest_data_app`(domU용 읽기 전용 데이터 한 경로) |
| `.../tests/test_guest_frames.py`, `test_guest_sender.py`, `guest_jpeg.py` | 단위·소켓 종단 시험 |
| `.../tests/probe_guest_render_path.py` | 로컬 Xvfb 종단 확인 도구 (Xen·보드 아님) |

`stream.py`는 `JpegParser`를 `mjpeg.py`에서 가져오도록만 바꿨다. 동작과 공개 이름(`xenova_display.stream.JpegParser`)은 그대로다.

## 5. 신뢰·보안 경계

- **domU는 신뢰하지 않는다.** dom0 수신기는:
  - 프레임 1장 최대 2 MiB(`MAX_JPEG`). 넘으면 버리고 `frames_rejected`에 센다.
  - JPEG 시작/끝과 SOF 헤더의 해상도가 설정값(기본 1280×720)과 같아야 한다.
  - 속도 상한 `max_fps`(기본 5). 더 빨리 오면 버리고 `frames_dropped`에 센다.
  - 마지막 프레임이 2초(`stale_ms`)보다 오래되면 `unavailable`. 연결이 끊기면 즉시 `unavailable`.
  - 오류는 고정 코드만: `guest_unreachable`, `guest_auth_failed`, `guest_protocol_error`, `guest_frame_invalid`, `guest_stream_stalled`, `guest_stream_closed`, `frame_stale`.
- **토큰 3개를 분리한다.** 어느 것도 응답·로그·argv에 나오지 않는다.
  1. guest stream token — dom0가 domU 프레임을 당길 때.
  2. guest data token — domU가 표시 데이터 한 경로를 읽을 때.
  3. relay token — dom0 내부 소비자(HDMI 프레임 출력기)가 프레임을 읽을 때.
  기존 feed의 service token은 dom0 밖으로 나가지 않는다 (`LocalFeed`가 dom0 안에서 붙인다).
- **네트워크.** `xenbr0`는 물리 NIC가 없는 격리 브리지다. 다만 모든 domU(Zephyr 포함)가 같은 브리지에 있고, dom0에 방화벽 규칙이 없다. 지금은 토큰이 유일한 문이다. 후속으로 `192.168.77.1:<data port>`는 출발지 `192.168.77.20`만 허용하는 nftables 규칙을 제안한다 (배포 담당과 협의).

## 6. 확인된 제약과 미결정

| 항목 | 확인한 사실 | 영향 / 필요한 결정 |
| --- | --- | --- |
| 표시 domU | VM3 `debian-dev`: PVH, **512 MiB, vCPU 1**, IP 192.168.77.20 (`defaults.json`) | 후보일 뿐 확정 아님. 다른 용도(interaction 앱 -128)와 겹치는지 확인 필요 |
| CPU | VM3는 전용 코어가 없고 dom0와 pCPU {0,3}를 공유 (`cpu-policy-preset.json`) | domU 렌더링 부하가 **dom0와 같은 코어**에 걸린다. B안이 dom0 코어 경합을 없애지 않는다 → 실측 필요 |
| 메모리 | 512 MiB에 Xvfb + Chromium + ffmpeg | 빠듯할 가능성이 크다. VM 메모리 증설은 VM 자원 변경이라 **팀 승인 필요** |
| domU 이미지 | 템플릿 recipe 패키지는 `python3-paho-mqtt`만 허용, overlay로 systemd unit 추가 불가 (`template_recipe.py`) | GUI·ffmpeg·unit 추가에 템플릿 코드 변경 필요 → **게스트 템플릿 담당과 협의** |
| Xen 그래픽 장치 | 모든 cfg에 `vfb/vkb/vdispl` 없음. 게스트·dom0 커널의 `XEN_FBDEV_FRONTEND`/`DRM_XEN_FRONTEND` 여부 미확인. QEMU VNC는 `-vnc none`만 검증됨 | 그래서 **Xen PV 화면 장치 대신 네트워크 MJPEG**를 택했다. PV 장치 경로는 커널 설정 확인 전에는 쓰지 않는다 |
| dom0 HDMI 표시 | dom0에 chromium-x11, Xorg, openbox, ffmpeg(x11grab)는 있음. mpv·gstreamer 없음 | HDMI 프레임 출력기 후보: (a) 기존 kiosk Chromium이 MJPEG 표시 — 쉬우나 무거움, (b) TurboJPEG 디코드 + X11 MIT-SHM 출력 — 기존 `native_jpeg`/`native_x11` 재사용, 가벼움. **(b) 권장**, 후속에서 측정 후 결정 |
| 휴대폰 | 휴대폰 선택지는 폰 직접 렌더링 (Jira 250·350·379·381의 현행 조건과 같음) | B안 프레임 경로를 휴대폰에 쓰지 않는다. 해당 티켓 조건은 그대로 둔다 |
| HDMI 1대 판정 | `outputs.RuntimeConfig`와 `display_mode`가 아직 출력 2개(monitor-1/2)를 전제한다 | HDMI 1대만 연결된 보드에서 HDMI 준비 판정이 되도록 출력 1개 구성 반영이 필요 (379/380/390) |
| 입력 | 이번 범위는 표시만 | 폰/HDMI에서 화면 조작(터치)이 필요하면 별도 입력 경로 설계가 필요 |
| 11/10 데모 | 지금 보드는 현재 경로(dom0 렌더링)로 동작 | B안은 domU 이미지·배포가 남아 있어 데모 전 전환은 위험. 데모는 현재 경로 유지를 권장 |

## 7. 후속 작업 (일반 작업, 각 ≤ 5 SP)

1. **표시 domU 이미지·실행 구성** — Xvfb/Xorg dummy, Chromium kiosk, ffmpeg, `guest_sender` unit, 토큰 배포. 게스트 템플릿 담당 협의 포함.
2. **dom0 HDMI 프레임 출력기** — [S15P31E110-411](https://ssafy.atlassian.net/browse/S15P31E110-411)에서 로컬 구현·검증 완료 ([검증](../verification/display-411-hdmi-frame-output-20261108.md)). 기존 HDMI 세션의 kiosk가 `renderer: guest_frames`일 때 Chromium 대신 `frame_presenter`를 실행한다. 상태 파일 형식이 같아 `display_mode`는 그대로 동작한다. 남은 것: 보드 실물 확인, HDMI 1대 판정(379/380/390), mobile 모드의 프레임 중계.
3. **guest data relay 배포** — `192.168.77.1` bind, 출발지 제한 규칙, 토큰 발급·회전.
4. **실보드 B안 검증 (HDMI 1대)** — CPU·RAM(dom0·domU 따로), fps, 표시 지연, 끊김·domU 재시작·HDMI↔휴대폰 모드 전환 복귀, 10분 연속. 휴대폰 직접 렌더링 검증은 기존 352·392·393 그대로.

## 8. 검증

로컬 결과는 [`docs/verification/display-410-local-path-20261108.md`](../verification/display-410-local-path-20261108.md)에 있다. 요약: 새 시험 전부 통과, Xvfb 가상 화면 → `guest_sender` → dom0 수신기 → relay 종단에서 5.17 fps, 화면 변경 반영 확인, 송신 중단 즉시 `unavailable`. **Xen·보드·실제 차량 UI·S24 검증은 아니다.**
