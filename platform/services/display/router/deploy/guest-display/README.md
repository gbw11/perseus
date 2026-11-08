# guest display — HDMI A안 (S15P31E110-414) / B안 보류 (410·411·413)

HDMI 선택지의 **현재 구조는 A안**이다. 웹 UI(vm-screens + 화면 앱 서버)는 표시 domU(VM3)에서 제공하고, **dom0의 기존 Chromium kiosk가 그 화면을 열어 HDMI 1대에 그린다.** 이 디렉터리의 파일은 아직 이미지에 설치되거나 enable되지 않는다. 설계: [`docs/design/display-hdmi-plan-a-domu-ui.md`](../../../../../../docs/design/display-hdmi-plan-a-domu-ui.md).

- 상태 수집·정리·화면 결정(policy)은 dom0에 둔다. VM3는 정리된 데이터를 읽기 전용으로 받아 웹 UI에 넘긴다.
- 휴대폰 선택지는 폰이 직접 렌더링한다 (이 경로와 무관).
- B안(VM3가 그리고 dom0는 프레임 중계)은 **보류**. 코드(`guest_session`, `guest_sender`, `frame_presenter`, relay)는 남겨 두지만 A안에서는 쓰지 않는다.

## A안 구성

| 위치 | 서비스 → 프로세스 | 역할 |
| --- | --- | --- |
| VM3 | `xenova-guest-display-app.service` → `xenova_display.guest_app` | `192.168.77.20:8086`, **dom0(192.168.77.1)만 접근 허용**. vm-screens 빌드 파일 + `GET /api/v1/display/presentation` 하나만 dom0 guest data로 전달 |
| dom0 | `xenova-display-guest-relay.service` → `xenova_display.guest_frames` (`guest: null`) | `192.168.77.1:8085` 읽기 전용 guest data만 (프레임 중계 없음) |
| dom0 HDMI | 기존 `xenova-display-hdmi.service` → kiosk | `ui_origin`이 있으면 `http://192.168.77.20:8086/display/mobile`을 연다. 이 화면에는 dom0 feed 세션을 주지 않는다 |

데이터 흐름: dom0 feed(127.0.0.1:8082) → dom0 guest data(192.168.77.1:8085, 토큰) → VM3 화면 앱 서버 → dom0 Chromium(HDMI).

## 설정 파일

| 위치 | 파일 | 예제 |
| --- | --- | --- |
| dom0 | `/etc/xenova/display-runtime.json` | [`examples/single-hdmi-plan-a-runtime.json`](../../examples/single-hdmi-plan-a-runtime.json) (HDMI 1대, `ui_origin`, `page: /display/mobile`, EDID는 실제 값으로) |
| dom0 | `/etc/xenova/guest-display.json` | [`guest-display-data-only.json.example`](guest-display-data-only.json.example) |
| VM3 | `/etc/xenova/guest-display-domu.json` | [`domu/guest-display-domu.json.example`](domu/guest-display-domu.json.example) |

`ui_origin`을 빼면 kiosk는 기존처럼 dom0 feed 화면을 연다(되돌리기).

## 토큰 (A안은 하나)

| 위치 | 파일 | 소유·권한 | 읽는 쪽 |
| --- | --- | --- | --- |
| dom0 | `/etc/xenova/guest-display/guest-data.token` | root, 0600 | guest data 경로 |
| VM3 | `/etc/xenova/guest-display/guest-data.token` | xenova-display, 0600 | `guest_app` (dom0와 같은 값) |

토큰 값은 Git·문서·로그에 넣지 않는다. 시험 중에는 임시로 손으로 배포하고, 이미지에 넣을 항목은 [`BUILD-CHECKLIST.md`](BUILD-CHECKLIST.md)에 정리한다.

## B안 파일 (보류, 참고용)

`domu/xenova-guest-display-session.service`, `domu/xenova-guest-display-sender.service`, `guest-display.json.example`(relay 포함), `examples/single-hdmi-guest-frames-runtime.json`. B안으로 돌아갈 때만 쓴다.
