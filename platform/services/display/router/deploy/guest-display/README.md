# guest display — HDMI A안 (S15P31E110-414) / B안 보류 (410·411·413)

HDMI 선택지의 **현재 구조는 A안**이다. 웹 UI(vm-screens + 화면 앱 서버)는 표시 domU(VM3)에서 제공하고, **dom0의 기존 Chromium kiosk가 그 화면을 열어 HDMI 1대에 그린다.** dom0 relay unit과 데이터 전용 설정 예제는 `xenova-display` recipe에 연결했다. 활성 설정·토큰 공급과 실제 이미지 설치는 별도이며, B안 unit은 설치하지 않는다. 설계: [`docs/design/display-hdmi-plan-a-domu-ui.md`](../../../../../../docs/design/display-hdmi-plan-a-domu-ui.md).

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

## VM3 전달용 토큰

| 위치 | 파일 | 소유·권한 | 읽는 쪽 |
| --- | --- | --- | --- |
| dom0 | `/etc/xenova/guest-display/guest-data.token` | root, 0600 | guest data 경로 |
| VM3 | `/etc/xenova/guest-display/guest-data.token` | xenova-display, 0600 | `guest_app` (dom0와 같은 값) |

위 표의 토큰은 dom0↔VM3 전용이다. 기존 loopback feed 토큰은 `/etc/xenova/display.env`의 `XENOVA_DISPLAY_TOKEN`을 relay가 읽으며 VM3에 전달하지 않는다. **업데이트·재기동 때 두 토큰을 다시 생성하지 않는다.** 활성 설정과 비밀은 recipe/추가 설치 번들에 넣지 않는다. 새 보드의 최초 토큰 공급은 별도 설치 절차이며 기존 보드 갱신과 구분한다.

## 재부팅 후 기동과 이미지 없는 갱신

정규 서비스는 `/usr/lib/systemd/system/xenova-display-guest-relay.service`다. 내부 주소를 준비하는 `xenova-bridge.service`의 성공 뒤 시작하고, feed를 함께 요청한다. feed 응답이 없으면 읽기 요청에 기존 503을 반환한다. relay 실패가 feed·VM 관리·HDMI를 함께 정지시키는 의존성은 추가하지 않는다. `/etc/xenova/guest-display.json`이나 `display.env`가 없으면 condition으로 시작을 건너뛴다. 파일을 나중에 공급한 경우 명시적으로 start한다.

새 이미지는 recipe가 모듈과 unit을 설치하고 `multi-user.target`에서 enable한다. `/usr/share/xenova/display/examples/guest-display-data-only.json.example`은 예제이며 활성 `/etc` 파일을 덮지 않는다.

구형 이미지에서 모듈이 빠졌다면 저장소 루트에서 작은 추가 설치 번들을 만든다.

```sh
python3 platform/scripts/package-guest-display-relay.py --output /tmp/guest-relay-bundle
```

산출물은 `rootfs/`, `manifest.json`, `payload.tar.gz`다. 정규 RPM을 생성하는 명령은 아니다. manifest의 파일·SHA-256·0644 모드를 대조해 새 파일만 설치한다. 기존 feed/HDMI의 site-packages 대신 `/usr/lib/xenova-display-guest-relay/<cohort>/`에 작은 사본을 두고 **relay 전용** `10-standalone-runtime.conf`로 import 경로를 고정한다. 다른 서비스의 `PYTHONPATH`는 바꾸지 않는다. 동작에 필요한 FastAPI·Pydantic·Uvicorn은 기존 이미지 제공본을 확인하며 pip로 업그레이드하지 않는다.

전환 전에는 config·토큰의 내용/소유·권한 보존과 기존 서비스 상태를 기록한다. 임시 relay만 정지한 뒤 `daemon-reload`와 `systemctl enable --now xenova-display-guest-relay.service`를 실행한다. enable 링크, 올바른 import 경로, restart 뒤 8085 무인증 401과 VM3 경유 presentation 200을 확인한다. 화면 렌더링·실제 재부팅 검증과는 구분한다. 실패하면 새 relay만 stop/disable하고 추가 설치한 파일만 되돌려 기존 임시 실행을 복구한다. 토큰·활성 설정은 삭제하지 않는다.

나중에 정규 패키지로 교체할 때는 해당 프로세스를 drain하고 설치 모듈을 확인한 다음 compatibility drop-in을 제거한다. **오버라이드가 남아 있으면 정규 패키지보다 사본을 먼저 읽는다.** 사본과 drop-in은 자동 삭제하지 않으며 설치 이력에서 관리한다. build·설치 확인 항목과 과거 임시 제공 이력은 [`BUILD-CHECKLIST.md`](BUILD-CHECKLIST.md)에 둔다.

## B안 파일 (보류, 참고용)

`domu/xenova-guest-display-session.service`, `domu/xenova-guest-display-sender.service`, `guest-display.json.example`(relay 포함), `examples/single-hdmi-guest-frames-runtime.json`. B안으로 돌아갈 때만 쓴다.
