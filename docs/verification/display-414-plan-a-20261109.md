# 414 HDMI A안 전환 — 로컬 검증 (2026-11-09)

관련: [S15P31E110-414](https://ssafy.atlassian.net/browse/S15P31E110-414) · 설계: [display-hdmi-plan-a-domu-ui.md](../design/display-hdmi-plan-a-domu-ui.md) · 빌드 체크리스트: [BUILD-CHECKLIST.md](../../platform/services/display/router/deploy/guest-display/BUILD-CHECKLIST.md)

## 범위

박신영 회신(2026-11-09)에 따라 HDMI를 A안으로 바꿨다. **웹 UI는 VM3, 그리기는 dom0 Chromium**이다. 먼저 로컬 PC(WSL)에서 확인했다. 그다음 보드에 임시로 설치해 **VM3와 dom0 사이의 데이터 경로**를 확인했다(아래 "보드 확인"). HDMI 실물은 아직 확인하지 않았다.

## 시험

```bash
cd platform/services/display/router
python -m pytest -p no:cacheprovider -rs tests
```

결과: **492 passed, 1 skipped**. skip은 playwright가 설치되지 않은 기존 `test_viewer_stall.py`다.

새 시험 `test_plan_a.py`:
- `ui_origin` 예제가 유효하다. 페이지 주소가 `http://192.168.77.20:8086/display/mobile`이 된다.
- `ui_origin`이 https, 포트 없음, 공인 IP, 와일드카드, 호스트 이름, 인증 정보, 경로, 쿼리를 쓰면 거부한다. guest_frames 렌더러와 같이 쓰는 것도 거부한다.
- kiosk가 `ui_origin`이 있으면:
  - VM3 주소를 연다.
  - **dom0 feed 세션 POST를 하지 않는다.**
  - presentation 조회도 하지 않는다(`feed_status: not_used`).
- kiosk에 `ui_origin`이 없으면 기존처럼 세션과 조회를 한다.
- `guest_app`의 bind와 허용 클라이언트를 검증한다. 와일드카드, 공인 주소, 빈 목록을 거부한다. 허용되지 않은 클라이언트는 **403**을 받는다.
- dom0 데이터 전용 모드(`guest: null`)는 토큰 하나로 동작한다. 프레임 중계(B안)를 켜면 stream/relay 토큰이 필요하다.

## 브라우저 확인 (앱 내 브라우저 1280×720)

흐름: 앱 내 브라우저(dom0 Chromium 역할, 허용 클라이언트) → VM3 역할 `guest_app` → `/api/v1/display/presentation` → dom0 guest data(합성 feed).

결과: `/display/mobile`이 가로 한 화면에 그려지고 "표시 연결됨"이 뜬다. 화면 파일은 기존 빌드(11/04)를 썼다.

## 보드 확인 (2026-11-09, HDMI 모니터 없음)

설치한 것과 되돌리는 방법은 [BUILD-CHECKLIST.md](../../platform/services/display/router/deploy/guest-display/BUILD-CHECKLIST.md) §4에 적었다. **임시 설치**이며, kiosk와 HDMI 설정은 바꾸지 않았다.

### 1차 (01:38~01:42)

- VM3(Debian 13, Python 3.13.5):
  - 앱이 active이고 `192.168.77.20:8086`에서 수신한다.
  - VM3가 자기 자신에게 요청하면 403이다.
  - 앱 RSS는 21 MiB, VM3 사용 가능 메모리는 348 MiB다.
- dom0:
  - VM3 `/display/mobile`, JS, CSS가 200이다.
  - POST는 405, 없는 경로는 404다.
  - guest data 서비스는 active였지만 약 20초 안에 8085를 열지 않았다. 그래서 presentation이 503이었다.
- 그 뒤 보드가 08:03 무렵 Tailscale에서 사라졌다가 09:05에 새로 부팅됐다.
  - journal이 이번 부팅 것만 남아 있어서 꺼진 원인은 확인하지 못했다.
  - 재부팅 뒤 dom0 임시 서비스는 사라졌다. VM3 앱은 enable하지 않았으므로 꺼져 있었다.

### 2차 (09:24~09:36, 재부팅 뒤)

- VM3 앱을 `systemctl start`로 다시 띄웠다. active이고, VM3가 자기 자신에게 요청하면 403이다.
- dom0 사전 점검(리스너 없이, load 약 10):
  - fastapi 0.135.3, pydantic 2.12.5, uvicorn 0.44.0
  - import에 **20.8초**가 걸렸다.
  - 설정, 토큰 파일, 앱 생성은 정상이다.
- 같은 임시 서비스를 다시 띄웠다. 재시작은 120초에 3번으로 제한했다.
  - **29초 뒤 8085가 열렸다**(load 12). 재시작은 0회다.
  - **1차의 503은 오류가 아니라 시작이 느렸기 때문이다.**
- dom0에서 본 결과: **모두 OK**

| 확인 | 결과 |
| --- | --- |
| dom0 8085, 토큰 없음 / 잘못된 토큰 | 401 / 401 |
| dom0 8085, guest 토큰 | 200 (11.4 KB) |
| VM3 8086 `/display/mobile`, JS, CSS | 200 |
| VM3 8086 presentation (VM3 → dom0 8085 → feed) | 200, `state.vms`·`decisions`·`outputs` 포함 |
| VM3 8086 POST / 없는 경로 | 405 / 404 |
| VM3를 거친 presentation 20회 | 모두 200, 지연 p50 273 ms, 최대 505 ms |
| 기존 서비스 | feed·dom0-manager active, hdmi·mobile inactive. 설치 전후 같음 |

### 3차: HDMI 실물 표시 시도 (09:46~10:15, 모니터 연결)

- **정식 HDMI 경로를 쓸 수 없다.**
  - 현재 이미지의 `/boot/config.txt`는 기본값(`dtoverlay=vc4-kms-v3d`)이고 Pi4 overlay 파일이 없다.
  - 10-28에 넣은 KMS 설정과 백업이 모두 사라졌다.
  - 그래서 dom0에 `/dev/dri`, HDMI 커넥터, EDID가 없다. `xenova-display-hdmi`(native_session)는 실제 EDID가 있어야 해서 시작할 수 없다.
- **fbdev 진단으로 띄워 봤다.** 재부팅과 부팅 설정 변경은 없었고, 임시 unit 하나만 썼다.
  - 구성: 펌웨어 framebuffer `/dev/fb1`(1824×984)에 Xorg fbdev를 띄우고, 그 위에서 414 kiosk(`virtual_x11`, `ui_origin`)를 실행했다. native_session은 helper 함수만 재사용했다.
  - 사전 점검은 모두 통과했다.
  - Chromium은 떴지만 모니터에는 **흰 화면**이 나왔다.
  - kiosk 상태는 `renderer_starting`과 `renderer_stale`을 오갔다. Chromium PID가 바뀌었으니 재시작을 반복한 것이다.
  - 화면 샘플은 흰색과 앱 배경색(#0f141b) 사이를 오갔다.
- **원인: dom0 CPU 포화.**
  - Chromium 실행 중 load 약 21, idle 1%였다.
  - 화면을 GPU 없이 CPU로만 그리느라 페이지 로드가 15초 제한을 넘겼다. 그러면 kiosk가 실패로 보고 Chromium을 다시 띄웠다.
  - 10:15에 진단 세션을 끄고 파일을 지웠다. 콘솔은 tty1로 돌아왔다.
- **조치:** kiosk를 고쳤다.
  - 페이지 로드는 문서 commit까지만 기다린다. 제한은 30초다.
  - VM3 화면은 `--app`으로 이미 열린 경우 다시 불러오지 않는다.
  - renderer 오류 뒤에는 5초부터 최대 60초까지 기다렸다가 다시 띄운다.
  - 이미지 KMS 설정과 dom0 CPU 여유는 BUILD-CHECKLIST §1에 박신영 협의 항목으로 남겼다.

## 아직 확인하지 않은 것

- dom0 kiosk가 HDMI 실물에서 VM3 화면을 여는 것. 이미지 KMS 복구와 dom0 CPU 여유가 먼저 필요하다.
- VM3 재시작 중 표시와 복귀.
- 부팅 직후 guest data 서비스가 열리기까지 약 30초 동안 화면이 대기 상태로 보이다가 복귀하는지.
