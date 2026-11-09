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
| ☐ | **HDMI KMS (Pi4)** | 10-28 방식대로 `/boot/overlays/vc4-kms-v3d-pi4.dtbo`를 넣고 `/boot/config.txt`에 `dtoverlay=vc4-kms-v3d-pi4,cma-64`를 쓴다. 2026-11-09 이미지에는 이 설정이 빠져 있다. 그래서 `/dev/dri`, HDMI 커넥터, EDID가 없고 `xenova-display-hdmi`를 시작할 수 없다. **11-09 보드에 수동으로 넣었고 재부팅 뒤 HDMI-A-1이 잡혔다(§4)** | 박신영(이미지) |
| ☐ | **휴대폰 USB 테더링 네트워크** | `/etc/systemd/network/20-xenova-usb-phone.network`: rndis_host·cdc_ncm·cdc_ether 링크는 DHCP로 IP만 받고 gateway·routes·DNS·NTP는 받지 않는다. 이 파일이 없으면 `80-wired.network`가 휴대폰 기본 경로를 metric 10으로 잡아 wlan0(600)보다 우선이 된다. 11-09 보드에 수동으로 넣었다(§4) | 박신영(이미지) |
| ☐ | **HDMI 모드 guard** | `/usr/lib/systemd/system/xenova-display-hdmi.service.d/60-display-mode.conf`. 레시피는 de4cdf69(11-05)부터 설치하지만 현재 이미지에는 없다. 없으면 `display_mode`가 `DISPLAY_MODE_CHECK_FAILED`로 멈춘다. 11-09 보드에 수동으로 넣었다(§4) | 박신영(이미지 재빌드) |
| ☐ | 휴대폰 접속 설정 | `/etc/xenova/display-mobile.json`(root 0600, `usb_interface`, `phone_ipv4`, `port`). **이미지에 넣지 않는다.** 설치할 때 실제 휴대폰 IP로 만든다 | 주소정(값) |
| ☐ | HDMI 설정 파일 | `/etc/xenova/display-runtime.json`(실제 EDID), `display-Xorg.conf`, `display-kiosk.env`. 2026-11-09 보드에는 셋 다 없다 | 주소정(값)·배포 담당 |
| ☐ | dom0 CPU 여유 | A안은 dom0에서 Chromium을 돌린다. 2026-11-09 측정: 평소 load 약 10, Chromium 실행 중 load 약 21, idle 1%. GPU가 없는 fbdev에서는 화면을 다 그리지 못했다 | 박신영과 협의 |

## 2. VM3 (Debian 템플릿)

2026-11-09 정수환이 Debian Interaction 템플릿(1.0.6)의 화면 설치 구성을 platform에 병합했다(`a612bcbc`, `platform/guests/common/guest_display_template.py`). 템플릿을 다시 빌드·배포한 뒤 아래 항목과 §3을 실제 보드에서 다시 확인한다.

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
| ☐ | 부팅 직후: dom0 guest data 서비스는 부하가 높으면 **약 30초 뒤에** 8085를 연다(2026-11-09 load 10~12에서 import 21초, 수신 29초). 그동안 화면은 연결 대기로 보이고, 열린 뒤 복귀해야 한다 |
| ☐ | 토큰·설정이 이미지·로그·Git에 남지 않음 |

## 4. 지금(임시) 손으로 배포한 것 기록

보드 시험 때 실제로 넣은 파일·해시·서비스 상태와 되돌리는 방법을 여기에 남긴다. 이미지 빌드로 대체되면 지운다.

| 날짜 | 위치 | 내용 | 되돌리기 |
| --- | --- | --- | --- |
| 2026-11-09 | VM3 | 시스템 계정 `xenova-display`. `/opt/xenova/display-guest`: 모듈 5개, 커밋 `c6978ced`. `/opt/xenova/display-web`: vm-screens 빌드, 커밋 `c509b733`, `index.html` sha256 `d33ec04774c7…`, `index-Dnfi9W5P.js` sha256 `5ec231c39fe5…`. 설정 `/etc/xenova/guest-display-domu.json`. 토큰 `/etc/xenova/guest-display/guest-data.token`(xenova-display 0600, 임시 값). unit은 `/etc/systemd/system/xenova-guest-display-app.service`이고 **enable하지 않음**: VM3가 재시작되면 `systemctl start`를 다시 해야 한다. 표시 파일: `/opt/xenova/display-guest/.s15p31e110-414-temporary` | `systemctl stop xenova-guest-display-app` → unit 파일 삭제, `daemon-reload` → `/opt/xenova/display-guest`, `/opt/xenova/display-web`, `/etc/xenova/guest-display-domu.json`, `/etc/xenova/guest-display` 삭제 → `userdel xenova-display` |
| 2026-11-09 | dom0 | `/opt/xenova/display-414`: `xenova_display`, 커밋 `c6978ced`, 별도 PYTHONPATH, 시스템 site-packages는 그대로. 설정 `/etc/xenova/guest-display.json`(데이터 전용). 토큰 `/etc/xenova/guest-display/guest-data.token`(root 0600, VM3와 같은 임시 값). 서비스는 `systemd-run`으로 띄운 임시 unit `xenova-display-guest-data-414`라 **재부팅하면 사라진다**. feed 토큰은 `/etc/xenova/display.env`를 EnvironmentFile로 읽는다. kiosk·HDMI 설정은 바꾸지 않았다 | `systemctl stop xenova-display-guest-data-414` → `/opt/xenova/display-414`, `/etc/xenova/guest-display.json`, 토큰 파일 삭제 → 비어 있으면 `/etc/xenova/guest-display` 삭제 |
| 2026-11-09 11:26 | dom0 `/boot` | **Pi4 KMS** (박신영 확인: 재부팅 없이 설정만 넣기). `/boot/overlays/vc4-kms-v3d-pi4.dtbo`(3913 B, sha256 `155ec071…`, 10-28 빌드와 같은 파일)를 추가했다. `/boot/config.txt`는 247번째 줄 `dtoverlay=vc4-kms-v3d`를 `dtoverlay=vc4-kms-v3d-pi4,cma-64`로 바꿨다. 결과 sha256은 `9f8dc214…`로 10-28 최종본과 같다. 사전 확인: 바꾸기 전 config(`d12f36ef…`)와 DTB(`75761b73…`)가 10-28 원본과 같았다. `boot.scr`은 `dom0_mem=3072M` 한 줄만 다르고, 펌웨어 DTB(`fdtcontroladdr`)를 그대로 쓴다. 11:45 재부팅(팀원 요청) 뒤 결과: `/dev/dri/card0`, `HDMI-A-1` connected(EDID `92a52d12…`, 1920×1080), VM 4대·서비스 정상. `v3d`는 probe에 실패해 3D 가속은 없다 | `cp /root/xenova-hdmi-backup/20261109-112607/config.txt /boot/config.txt && sync` 후 재부팅. 부팅이 안 되면 SD 카드에서 `/boot/config-before-kms-20261109.txt`로 config.txt를 덮어쓴다 |
| 2026-11-09 11:45 | dom0 `/etc/systemd/network` | `20-xenova-usb-phone.network`(0644, sha256 `1c1bc7190ee5…`): `[Match] Driver=rndis_host cdc_ncm cdc_ether`, `[Link] RequiredForOnline=no`, `[Network] DHCP=ipv4`, `LinkLocalAddressing=no`, `IPv6AcceptRA=no`, `[DHCPv4] UseGateway=no`, `UseRoutes=no`, `UseDNS=no`, `UseNTP=no`, `UseDomains=no`. 재부팅과 휴대폰 재연결 뒤에도 기본 경로가 wlan0으로 유지되는 것을 확인했다 | `rm` 파일 → `networkctl reload` |
| 2026-11-09 11:56 | dom0 `/etc/xenova` | `display-mobile.json`(root 0600): `usb_interface: usb0`, `phone_ipv4: 10.104.83.148`, `port: 8084`. 휴대폰 IP가 바뀌면 이 값을 고쳐야 한다. 보드 usb0 주소는 연결할 때마다 바뀔 수 있지만, 접속 서비스가 시작할 때 다시 읽는다 | `rm` 파일 |
| 2026-11-09 12:04 | dom0 `/usr/lib/systemd` | `xenova-display-hdmi.service.d/60-display-mode.conf`(sha256 `f9c20dc45cae…`): 저장소 `deploy/mobile/60-display-mode.conf`와 같은 내용에 주석 한 줄을 더했다. 그 뒤 `daemon-reload`했다. 결과: `display_mode mobile`이 `applied: true`를 반환했다 | `rm` 파일 → `systemctl daemon-reload` |
| 2026-11-09 11:45 | 재부팅 뒤 상태 | dom0 임시 unit `xenova-display-guest-data-414`는 사라졌다. VM3 `xenova-guest-display-app`은 enable하지 않아서 꺼져 있다. 휴대폰 모드 marker는 `/run`에 있어서 재부팅하면 HDMI 기본으로 돌아간다 | — |
