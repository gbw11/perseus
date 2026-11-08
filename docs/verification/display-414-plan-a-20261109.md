# 414 HDMI A안 전환 — 로컬 검증 (2026-11-09)

관련: [S15P31E110-414](https://ssafy.atlassian.net/browse/S15P31E110-414) · 설계: [display-hdmi-plan-a-domu-ui.md](../design/display-hdmi-plan-a-domu-ui.md) · 빌드 체크리스트: [BUILD-CHECKLIST.md](../../platform/services/display/router/deploy/guest-display/BUILD-CHECKLIST.md)

## 범위

박신영 회신(2026-11-09)에 따라 HDMI를 A안으로 바꿨다. **웹 UI는 VM3, 그리기는 dom0 Chromium**이다. **로컬 PC(WSL)에서만** 확인했다. VM3, 보드, HDMI 실물은 쓰지 않았다.

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

## 아직 확인하지 않은 것

- VM3 임시 설치: 모듈, 화면 파일, 설정, 토큰, unit.
- dom0 guest data 서비스 설치.
- dom0 kiosk가 HDMI 실물에서 VM3 화면을 여는 것.
- VM3 재시작 중 표시와 복귀.
