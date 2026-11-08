# 표시 domU(VM3) 웹 UI 구성 — 박신영(platform·guests) 요청

> 작성: 주소정, 2026-11-09 · 갱신: 2026-11-09 **A안으로 변경** (박신영 회신 반영) · 관련: S15P31E110-413, S15P31E110-414 · 설계 [display-hdmi-plan-a-domu-ui.md](../design/display-hdmi-plan-a-domu-ui.md)

## 0. 요약

| 대상 | 해야 할 것 | 막혀 있나 |
| --- | --- | --- |
| 박신영 (`platform/guests/`, Debian 템플릿) | 지금은 없음. 보드 시험은 **VM3에 임시로 손으로 설치**한다(박신영 확인). 나중에 템플릿에 넣을 항목은 [빌드 체크리스트](../../platform/services/display/router/deploy/guest-display/BUILD-CHECKLIST.md) §2 | 🟢 시험은 막히지 않음. 영구 반영만 템플릿 작업 필요 |

## 1. 박신영 회신 (2026-11-09)

1. Debian이면 VM3에 임시로 직접 설치해서 시험해도 된다. VM3는 가상 네트워크로 연결돼 있다(웹 콘솔도 됨).
   - 확인: dom0↔VM3 브리지 연결은 정상이다. 인터넷 경로(기본 경로·DNS)만 없다. A안은 새 패키지가 필요 없어서 이 점이 문제가 되지 않는다.
2. Chromium을 왜 VM3에 넣느냐. 웹 UI를 VM3에 두는 것과 Chromium을 넣는 것은 다르다.
   - **반영: A안으로 변경.** 웹 UI는 VM3, 그리기는 dom0의 기존 Chromium이 한다. VM3에는 GUI·영상 송출이 필요 없다.
3. 다른 기능을 방해하지 않는 dom0 기능 추가는 상관없다.
   - 반영: dom0에는 guest data 경로 서비스 하나만 추가한다(프레임 중계 없음).
4. 토큰은 임시로 배포해서 쓰되, 나중에 빌드할 때 챙길 부분은 체크리스트로 만든다.
   - 반영: [BUILD-CHECKLIST.md](../../platform/services/display/router/deploy/guest-display/BUILD-CHECKLIST.md)

## 2. A안에서 VM3에 들어가는 것 (임시 설치 → 나중에 템플릿)

| 항목 | 값 |
| --- | --- |
| apt 패키지 | **없음** (python3 사용) |
| 계정 | `xenova-display` (시스템 계정) |
| Python 모듈 | `/opt/xenova/display-guest/xenova_display/`: `__init__.py`, `capture_limits.py`, `mjpeg.py`, `guest_sender.py`, `guest_app.py` (표준 라이브러리만) |
| 화면 빌드 결과 | `/opt/xenova/display-web` |
| 설정 | `/etc/xenova/guest-display-domu.json` (bind 192.168.77.20:8086, dom0만 허용) |
| unit | `xenova-guest-display-app.service`, `xenova-guest-display.target` |
| 토큰 | `/etc/xenova/guest-display/guest-data.token` (xenova-display 0600, dom0와 같은 값) |

## 3. 나중에 확인할 것

- 템플릿 버전: 저장소 lock은 1.0.4, 보드 기록(`display-251-post-update-20261105.md`)은 1.0.5.
- 토큰을 첫 부팅 때 넣는 방법: 지금 Linux 게스트 자격 공급 채널은 agent만 지원한다.
- VM3 메모리(512 MiB)는 A안이면 그대로 둬도 되는지 실측해서 확인한다.

## 4. 이력

- 2026-11-09 처음 요청(B안): VM3에 xvfb·chromium·ffmpeg·fonts-noto-cjk와 서비스 3개를 넣어 VM3가 화면을 그리는 구성 → 박신영 회신 후 A안으로 대체.
