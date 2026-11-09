# VM3 Interaction 화면 앱 로컬 검증 (2026-11-09)

## 범위

Debian Interaction 템플릿에 설치하는 Plan A 구성의 로컬 검증이다. 실제 Debian 이미지,
systemd 부팅, Xen 브리지와 HDMI는 사용하지 않았다.

## 확인한 내용

- 임시 rootfs에 `xenova-display` 시스템 계정과 Plan A Python 모듈을 설치한다.
- 같은 rootfs에 `/display/mobile` 빌드 파일, VM3 설정, service와 target을 설치한다.
- 설치된 `/opt/xenova/display-guest/xenova_display/guest_app.py`를 직접 import한다.
- loopback에서 실제 HTTP 서버를 열어 다음을 확인한다.
  - `GET /display/mobile` → `200`, 설치된 `index.html`
  - `GET /assets/index-test.js` → `200`, 설치된 정적 자산
  - `GET /api/v1/display/presentation` → 가짜 dom0 응답 전달 및 Bearer 토큰 사용
  - 허용되지 않은 경로 → `404`
  - 쓰기 요청 → `405`

```text
python -m pytest -q platform/tests/test_xenova_display_guest_template.py
5 passed
```

## 실제 VM에서 남은 검증

- `xenova-guest-display.target`의 부팅 자동 시작
- `xenova-display` UID/GID로 서비스 실행
- `192.168.77.20:8086` 바인딩과 dom0 `192.168.77.1` 접근
- 다른 domU 요청 차단, 서비스 실패 후 재시작, VM 재부팅 후 복구
- dom0 Chromium과 단일 HDMI 출력

위 항목은 Debian 1.0.6 템플릿 재빌드·배포 후 실제 보드 E2E에서 확인한다.
