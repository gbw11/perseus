# 표시 domU(VM3) 화면 그리기 구성 — 박신영(platform·guests) 요청

> 작성: 주소정, 2026-11-09 · 관련: S15P31E110-413 (410·411·412 병합 완료) · 설계 [display-plan-b-domu-render-relay.md](../design/display-plan-b-domu-render-relay.md)

## 0. 요약

| 대상 | 해야 할 것 | 막혀 있나 |
| --- | --- | --- |
| 박신영 (`platform/guests/`, Debian 템플릿) | VM3 `debian-dev`(Debian Interaction) 이미지에 화면 그리기용 패키지·계정·서비스·설정·토큰이 들어가게 템플릿을 확장한다 | 🔴 HDMI B안 실보드 확인이 이것 없이 불가. 현재 템플릿은 패키지가 `python3-paho-mqtt`로 고정이고, overlay로 `etc/systemd/`·`etc/xenova/`를 쓸 수 없다 (`template_recipe.py` `RESERVED`) |
| 팀 결정 | VM3 메모리 (현재 512 MiB, vCPU 1, dom0와 pCPU {0,3} 공유) | 🟡 Chromium + Xvfb + ffmpeg 실측 후 결정. 우선 512 MiB로 시도하고 부족하면 1024 MiB 제안 |

## 1. 확정된 사실

| 항목 | 값 |
| --- | --- |
| 표시 선택지 | HDMI 1대 또는 휴대폰 직접 렌더링. **HDMI는 B안**: domU가 화면 앱을 실행·그리고, dom0는 완성 화면을 HDMI로 내보냄 |
| dom0에 남는 것 | 상태 수집·정리·화면 결정(policy). domU는 정리된 데이터만 읽기 전용으로 받음 |
| 표시 domU | VM3 `debian-dev`, IP 192.168.77.20 (`defaults.json` 예약), xenbr0 |
| dom0 쪽 구현 | 410·411·412 병합. relay `127.0.0.1:8084`, domU용 데이터 `192.168.77.1:8085`, HDMI 프레임 출력기 |
| domU 쪽 구현 (이번 MR) | `xenova_display.guest_app` / `guest_session` / `guest_sender` + unit 3개·target·설정 예제 (`platform/services/display/router/deploy/guest-display/domu/`) — 모두 **표준 라이브러리만** 사용 |

## 2. 요청 내용

### 2-1. 패키지 (Debian 13 arm64)

| 패키지 | 이유 |
| --- | --- |
| `xvfb` | domU 가상 화면 `:0` 1280×720 |
| `chromium` | 화면 앱 kiosk 렌더링 (`/usr/bin/chromium`) |
| `ffmpeg` | `guest_sender`의 x11grab → MJPEG |
| `fonts-noto-cjk` | 한글 표시 (없으면 화면 글자가 깨짐) |
| `python3` | 이미 있음 (추가 pip 패키지 없음) |

### 2-2. 계정·파일

| 항목 | 값 |
| --- | --- |
| 계정 | 시스템 계정 `xenova-display` (로그인 셸 없음) |
| Python 모듈 | `xenova_display` 패키지 중 domU가 쓰는 모듈: `__init__.py`, `capture_limits.py`, `mjpeg.py`, `guest_sender.py`, `guest_app.py`, `guest_session.py` (FastAPI 등 dom0 의존 모듈은 import하지 않음) |
| 화면 앱 | vm-screens 빌드 결과(`index.html`, `assets/`) → `/opt/xenova/display-web` (프론트 담당 빌드 산출물, 버전 고정) |
| 설정 | `/etc/xenova/guest-display-domu.json` (예제 `guest-display-domu.json.example`) |
| unit | `xenova-guest-display-app.service`, `xenova-guest-display-session.service`, `xenova-guest-display-sender.service`, `xenova-guest-display.target` (enable: target) |
| 토큰 | `/etc/xenova/guest-display/guest-stream.token`, `guest-data.token` — `xenova-display` 소유 0600. **값은 dom0 쪽 같은 이름 파일과 같아야 함.** 기존 provisioning(자격 공급) 방식으로 첫 부팅 때 넣는 것을 제안. Git·이미지 공개 산출물에 넣지 않음 |

### 2-3. 네트워크

- domU `192.168.77.20:8090` (sender) ← dom0가 당겨감.
- domU → dom0 `192.168.77.1:8085` (읽기 전용 데이터, 토큰 필요).
- 별도 방화벽 규칙은 배포 담당과 협의(출발지 제한 제안).

## 3. 수락 기준 (보드)

1. VM3 부팅 후 `xenova-guest-display.target` 하위 3개 서비스가 active.
2. dom0 relay(`/api/v1/guest-display`)에서 `status: live`, 1280×720, 약 5 fps.
3. VM3 메모리·CPU 사용량 기록 (Chromium 포함). 부족하면 메모리 결정으로 넘김.
4. VM3 재시작 시 dom0 HDMI에 "GUEST DISPLAY UNAVAILABLE"이 뜨고, 복귀 후 다시 live.

## 4. 내가 해 둔 것 / 할 것

| 내용 | 위치 | 상태 |
| --- | --- | --- |
| domU 화면 앱 서버·세션·송신기·unit·설정 예제 | `platform/services/display/router/` (S15P31E110-413 MR) | 로컬 시험 완료, 보드 미적용 |
| dom0 relay·HDMI 출력기·HDMI 1대 | 410·411·412 | 병합 완료, 보드 미설치 |
| `platform/guests/` 수정 | 하지 않음 | — |
