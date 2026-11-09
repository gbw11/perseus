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

## 아직 확인하지 않은 것

- dom0 kiosk가 HDMI 실물에서 VM3 화면을 여는 것. 모니터가 필요하다.
- VM3 재시작 중 표시와 복귀.
- 부팅 직후 guest data 서비스가 열리기까지 약 30초 동안 화면이 대기 상태로 보이다가 복귀하는지.
