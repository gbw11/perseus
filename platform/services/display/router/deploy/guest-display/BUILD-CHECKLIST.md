# HDMI A안 — 이미지 빌드 때 챙길 항목 (S15P31E110-414)

현재 소스는 dom0 relay unit·enable·설정 예제를 `xenova-display` recipe에 연결했다. 구형 이미지를 위한 추가 설치 번들은 [`README.md`](README.md#재부팅-후-기동과-이미지-없는-갱신)에 설명한다. **recipe 연결, 로컬 설치 재생, 보드 적용, 실제 재부팅 수락은 서로 다르다.** 아래 체크는 실제 이미지/설치본에서 확인한다. 4절의 임시 제공 내용은 당시 이력이다.

## 1. dom0 (Yocto 이미지)

| ✔ | 항목 | 위치·값 | 담당 |
| --- | --- | --- | --- |
| ☐ | `xenova_display` 최신 패키지 (414 이후, `guest_frames` 포함) | `/usr/lib/python3.*/site-packages/xenova_display/` | 주소정(코드)·recipe 담당 |
| ☐ | guest data 서비스 unit | `xenova-display-guest-relay.service`: bridge 성공 후 기동, feed 요청, multi-user enable. recipe 연결 완료; 실제 이미지에서 확인 | 주소정·recipe 담당 |
| ☐ | guest data 설정 (A안, 프레임 중계 없음) | `/etc/xenova/guest-display.json` ← `guest-display-data-only.json.example` | 주소정 |
| ☐ | HDMI 런타임 설정: HDMI 1대, `ui_origin`, `page` | `/etc/xenova/display-runtime.json` ← `examples/single-hdmi-plan-a-runtime.json`, **EDID는 설치할 때 실제 값** | 주소정 |
| ☐ | **guest data 토큰** | `/etc/xenova/guest-display/guest-data.token`, root 0600. 새 보드 최초 공급만 별도 절차. **업데이트·재부팅은 기존 값 보존**, 이미지·Git에 넣지 않음 | 첫 부팅 생성 방식은 박신영과 협의 |
| ☐ | 방화벽 | `192.168.77.1:8085`는 출발지 `192.168.77.20`만, dom0 → `192.168.77.20:8086` 허용 | 배포 담당 |
| ☐ | **HDMI KMS (Pi4)** | 10-28 방식대로 `/boot/overlays/vc4-kms-v3d-pi4.dtbo`를 넣고 `/boot/config.txt`에 `dtoverlay=vc4-kms-v3d-pi4,cma-64`를 쓴다. 2026-11-09 이미지에는 이 설정이 빠져 있다. 그래서 `/dev/dri`, HDMI 커넥터, EDID가 없고 `xenova-display-hdmi`를 시작할 수 없다 | 박신영(이미지) |
| ☐ | HDMI 설정 파일 | `/etc/xenova/display-runtime.json`(실제 EDID), `display-Xorg.conf`, `display-kiosk.env`. 2026-11-09 보드에는 셋 다 없다 | 주소정(값)·배포 담당 |
| ☐ | dom0 CPU 여유 | A안은 dom0에서 Chromium을 돌린다. 2026-11-09 측정: 평소 load 약 10, Chromium 실행 중 load 약 21, idle 1%. GPU가 없는 fbdev에서는 화면을 다 그리지 못했다 | 박신영과 협의 |

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
| ☐ | 템플릿 버전·lock·`defaults.json`·첫 설치 이미지 재생성 | Debian 1.0.6 재빌드·별도 catalog 게시 완료. lock/defaults/조립 예제는 1.0.6과 아래 manifest hash로 정합화. 기존 VM은 자산 유지한 화면 파일만 갱신. 전체 이미지 재조립은 별도 | 박신영 |

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
| ☐ | relay enable 링크·bridge Requires/After·전용 import 경로, restart 뒤 data 200/무인증 401. 기존 feed PID·토큰·활성 설정 보존 |
| ☐ | 실제 재부팅 후 relay 자동 기동 (enable·restart 확인만으로 이 항목을 완료하지 않음) |

## 4. 과거 임시 제공 이력 — 현재 설치 지시 아님

아래는 최초 수동 제공 당시 기록이며 후속 갱신의 삭제 지시가 아니다. 토큰·설정을 재생성하거나 지우는 절차로 사용하지 않는다. 후속 Debian 1.0.6 검증은 `xenova-test-docs/2026-11-09-debian-interaction-1.0.6-a612bcbc/result.md`에 보존한다. dom0 relay 영구 전환의 실제 적용 결과는 별도 갱신 기록을 따른다.

| 날짜 | 위치 | 내용 | 되돌리기 |
| --- | --- | --- | --- |
| 2026-11-09 | VM3 | 시스템 계정 `xenova-display`. `/opt/xenova/display-guest`: 모듈 5개, 커밋 `c6978ced`. `/opt/xenova/display-web`: vm-screens 빌드, 커밋 `c509b733`, `index.html` sha256 `d33ec04774c7…`, `index-Dnfi9W5P.js` sha256 `5ec231c39fe5…`. 설정 `/etc/xenova/guest-display-domu.json`. 토큰 `/etc/xenova/guest-display/guest-data.token`(xenova-display 0600, 임시 값). unit은 `/etc/systemd/system/xenova-guest-display-app.service`이고 **enable하지 않음**: VM3가 재시작되면 `systemctl start`를 다시 해야 한다. 표시 파일: `/opt/xenova/display-guest/.s15p31e110-414-temporary` | `systemctl stop xenova-guest-display-app` → unit 파일 삭제, `daemon-reload` → `/opt/xenova/display-guest`, `/opt/xenova/display-web`, `/etc/xenova/guest-display-domu.json`, `/etc/xenova/guest-display` 삭제 → `userdel xenova-display` |
| 2026-11-09 | dom0 | `/opt/xenova/display-414`: `xenova_display`, 커밋 `c6978ced`, 별도 PYTHONPATH, 시스템 site-packages는 그대로. 설정 `/etc/xenova/guest-display.json`(데이터 전용). 토큰 `/etc/xenova/guest-display/guest-data.token`(root 0600, VM3와 같은 임시 값). 서비스는 `systemd-run`으로 띄운 임시 unit `xenova-display-guest-data-414`라 **재부팅하면 사라진다**. feed 토큰은 `/etc/xenova/display.env`를 EnvironmentFile로 읽는다. kiosk·HDMI 설정은 바꾸지 않았다 | `systemctl stop xenova-display-guest-data-414` → `/opt/xenova/display-414`, `/etc/xenova/guest-display.json`, 토큰 파일 삭제 → 비어 있으면 `/etc/xenova/guest-display` 삭제 |

## 5. 2026-11-09 후속 제공 — dom0 relay 영구 서비스

Debian 첫 설치 선택은 `linux/debian-13-20261015/1.0.6`이다. manifest SHA-256은 `144ebb333832e2a3c56c08bdc0d178462160b532fae295bb0a900c9344a133e9`, template archive SHA-256은 `272f7dd34ead962adddc8779b8c76e52e4f1c65948bbb175e1c9aad4a3a3e731`이다. 화면 자산은 frontend `447d0d06dea1e0e27eafcbd0b80788c1fe97266f`의 vm-screens 빌드이며 tree SHA-256은 `b3bff1d0bd36705820d00d5243fffd4fc2437ed5124f1d5d976dcbb32eb36b9f`다. recipe를 다시 돌려 새 산출물을 만들면 hash를 재확인한다. 버전 문자열만 같다고 다른 산출물을 이 lock으로 통과시키지 않는다.

이미지 재빌드 없이 board `100.108.226.65`에 정규 unit과 relay 전용 추가 번들 14개 파일(24,058 bytes 압축)을 설치했다. runtime은 `/usr/lib/xenova-display-guest-relay/58e471353d0f0b55/`이며 기존 site-packages를 덮지 않는다. unit은 `enabled/active`, bridge `Requires/After`와 `multi-user.target.wants` 링크를 실제 PID1에서 확인했다. 기존 임시 서비스는 inactive다.

서비스 restart 전후 토큰·활성 설정의 내용/inode/소유/권한, feed PID, HDMI 상태가 유지됐다. 8085 무인증 401, 인증 요청과 VM3 경유 presentation 200, VM3 UI 200을 확인했다. 최초 상태는 사용자 재부팅 뒤 relay 부재/VM3 presentation 503이었고, 설치 뒤 200으로 회복했다. 토큰 재발급·VM 재기동은 하지 않았다.

recipe do_install 재생·별도 설치 경로 import·기존 데이터 경로 회귀 47개를 로컬에서 통과했다. 로컬 unit 로더는 systemd 255.4이며 실제 보드 PID1은 259.5다. **새 RPM/전체 이미지 빌드와 이 설치 이후의 실제 재부팅 시험은 미수행**이다. 결과 원본: `xenova-test-docs/2026-11-09-dom0-guest-relay/result.md`. 설치 receipt와 원상 복구 자료는 보드 `/root/xenova-updates/dom0-guest-relay-20261109/`에 보존한다.
