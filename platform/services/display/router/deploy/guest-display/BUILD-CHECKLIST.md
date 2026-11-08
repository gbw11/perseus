# HDMI A안 — 이미지 빌드 때 챙길 항목 (S15P31E110-414)

지금은 보드 시험을 위해 **임시로 손으로 배포**한다(박신영 확인, 2026-11-09). 아래는 나중에 dom0 이미지·VM3 템플릿을 다시 만들 때 빠지면 안 되는 항목이다. 담당 칸은 해당 코드·설정의 소유자이며, 다른 사람 영역은 요청으로만 진행한다.

## 1. dom0 (Yocto 이미지)

| ✔ | 항목 | 위치·값 | 담당 |
| --- | --- | --- | --- |
| ☐ | `xenova_display` 최신 패키지 (414 이후, `guest_frames` 포함) | `/usr/lib/python3.*/site-packages/xenova_display/` | 주소정(코드)·recipe 담당 |
| ☐ | guest data 서비스 unit | `xenova-display-guest-relay.service` (feed 다음 기동) | 주소정·recipe 담당 |
| ☐ | guest data 설정 (A안, 프레임 중계 없음) | `/etc/xenova/guest-display.json` ← `guest-display-data-only.json.example` | 주소정 |
| ☐ | HDMI 런타임 설정: HDMI 1대, `ui_origin`, `page` | `/etc/xenova/display-runtime.json` ← `examples/single-hdmi-plan-a-runtime.json`, **EDID는 설치할 때 실제 값** | 주소정 |
| ☐ | **guest data 토큰** | `/etc/xenova/guest-display/guest-data.token`, root 0600. **설치마다 새로 생성**, 이미지·Git에 넣지 않음 | 첫 부팅 생성 방식은 박신영과 협의 |
| ☐ | 방화벽 | `192.168.77.1:8085`는 출발지 `192.168.77.20`만, dom0 → `192.168.77.20:8086` 허용 | 배포 담당 |

## 2. VM3 (Debian 템플릿)

| ✔ | 항목 | 위치·값 | 담당 |
| --- | --- | --- | --- |
| ☐ | 새 apt 패키지 | **없음** (python3만 사용, 이미 있음) | — |
| ☐ | 계정 | 시스템 계정 `xenova-display` (로그인 셸 없음) | 박신영 |
| ☐ | Python 모듈 (표준 라이브러리만) | `/opt/xenova/display-guest/xenova_display/`: `__init__.py`, `capture_limits.py`, `mjpeg.py`, `guest_sender.py`(토큰 읽기), `guest_app.py` | 주소정(파일)·박신영(설치) |
| ☐ | 화면 빌드 결과 | `/opt/xenova/display-web` (`index.html`, `assets/`), **빌드 커밋·해시 기록** | 프론트 담당(빌드)·박신영(설치) |
| ☐ | 설정 | `/etc/xenova/guest-display-domu.json` ← `domu/guest-display-domu.json.example` | 주소정 |
| ☐ | unit·target | `xenova-guest-display-app.service`, `xenova-guest-display.target` enable | 박신영 |
| ☐ | **guest data 토큰** | `/etc/xenova/guest-display/guest-data.token`, xenova-display 0600, **dom0와 같은 값**. 지금 Linux 게스트 자격 공급 채널은 agent만 지원 → 채널 추가 필요 | 박신영 |
| ☐ | 템플릿 버전·lock·`defaults.json`·첫 설치 이미지 재생성 | 저장소는 1.0.4, 보드 기록은 1.0.5 — 확인 필요 | 박신영 |

## 3. 빌드 뒤 확인

| ✔ | 확인 |
| --- | --- |
| ☐ | VM3 `xenova-guest-display-app` active, dom0에서 `http://192.168.77.20:8086/display/mobile` 200 |
| ☐ | 다른 domU에서 같은 주소 요청 → 403 |
| ☐ | dom0 `192.168.77.1:8085`는 토큰 없으면 401 |
| ☐ | `display_mode hdmi` → HDMI 1대 준비 완료, 화면이 가로로 표시 |
| ☐ | VM3 재시작 중 HDMI 화면: 연결 끊김 표시 → 복귀 |
| ☐ | 토큰·설정이 이미지·로그·Git에 남지 않음 |

## 4. 지금(임시) 손으로 배포한 것 기록

보드 시험 때 실제로 넣은 파일·해시·서비스 상태와 되돌리는 방법을 여기에 남긴다. 이미지 빌드로 대체되면 지운다.

| 날짜 | 위치 | 내용 | 되돌리기 |
| --- | --- | --- | --- |
| | | | |
