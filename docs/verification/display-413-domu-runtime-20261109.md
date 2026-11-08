# 413 표시 domU 실행 구성 — 로컬 검증 (2026-11-09)

관련: [S15P31E110-413](https://ssafy.atlassian.net/browse/S15P31E110-413) · 요청서: [2026-11-09-display-domu-renderer-platform.md](../requests/2026-11-09-display-domu-renderer-platform.md)

## 범위

HDMI 선택지에서 **표시 domU가 화면을 그리는 데 필요한 domU 쪽 구성**을 만들었다. 상태 수집·정리·화면 결정은 dom0에 그대로 둔다. **로컬 PC(WSL)에서만** 확인했다. VM3, Xen, 보드, HDMI 모니터는 쓰지 않았다.

## 만든 것

| 파일 | 역할 |
| --- | --- |
| `xenova_display/guest_app.py` | domU 화면 앱 서버 (`127.0.0.1:8086`, 표준 라이브러리만) |
| `xenova_display/guest_session.py` | Xvfb `:0` 실행 → `-displayfd`로 준비 확인 → Chromium kiosk로 화면 앱을 연다. 둘 중 하나라도 종료되면 세션 실패로 처리해 systemd가 다시 시작한다. root로는 실행하지 않는다 |
| `deploy/guest-display/domu/*` | 서비스 3개(app·session·sender), `xenova-guest-display.target`, 설정 예제 |
| `examples/single-hdmi-guest-frames-runtime.json` | dom0 HDMI 1대 + `renderer: guest_frames` 예제 |
| `docs/requests/2026-11-09-display-domu-renderer-platform.md` | 게스트 템플릿 담당 요청 |

`guest_app.py`가 하는 일과 막는 것:
- vm-screens 빌드 파일을 제공한다.
- `GET /api/v1/display/presentation` 하나만 dom0 guest data(410)로 전달한다. 토큰은 서버 안에서만 붙이고, 페이지에는 노출하지 않는다.
- 나머지 경로, POST, 쿼리 문자열, 경로 탈출은 모두 거부한다.

## 시험

```bash
cd platform/services/display/router
python -m pytest -p no:cacheprovider -rs tests
```

결과: **486 passed, 1 skipped**. skip은 playwright가 설치되지 않은 기존 `test_viewer_stall.py`다.

새 시험:
- `test_guest_app.py`
  - 설정 기본값과 검증: 상대 경로, index 없음, 공인·와일드카드·loopback 데이터 주소, 허용하지 않은 화면, 원격 X 표시, 범위 밖 크기, 알 수 없는 키를 거부한다.
  - dom0 응답이 401, 503, HTML, 2 MiB 초과, 연결 거부일 때 고정 코드를 반환한다.
  - 고정 경로와 토큰으로만 요청한다.
  - loopback으로만 bind한다.
  - 화면, assets, 데이터만 응답하고 나머지 경로·방법은 404/405로 거부한다.
  - 실제 소켓으로 dom0 역할 서버를 띄워 왕복을 확인했다.
- `test_guest_session.py`
  - Xvfb·Chromium 명령 구성을 확인한다.
  - Xvfb가 준비된 뒤에 Chromium을 시작하고, 종료할 때는 Chromium을 먼저 멈춘다.
  - Chromium 환경 변수에 토큰이 없다.
  - Chromium이 종료되면 세션 전체가 실패한다.
  - 다른 디스플레이 번호, 준비 시간 초과, 준비 전 종료를 오류로 처리한다.

## 실제 브라우저 확인 (앱 내 브라우저, 1280×720)

흐름: vm-screens 빌드 결과 → `guest_app`(domU 역할) → `/api/v1/display/presentation` → `create_guest_data_app`(dom0 guest data, 410) → 합성 데이터 feed.

| 항목 | 결과 |
| --- | --- |
| `/display/mobile` 가로 1280×720 | 계기판과 인포테인먼트를 한 화면에 표시. 오른쪽 위 "표시 연결됨" |
| 데이터 | VM1 주 구동, VM2 백업·미러링, VM3 Debian, "자율 모드 선택 · 구동 준비 중"이 dom0 데이터 경로로 표시됨 |

참고:
- 화면 파일은 기존 빌드(`display-349` worktree, 11/04)를 썼다. 팀원이 만드는 새 화면은 빌드되면 `web_root`만 바꿔서 같은 방식으로 확인한다.
- 이 확인에는 Chromium kiosk와 Xvfb가 들어 있지 않다(WSL에 Chromium이 없음). 그 부분은 `guest_session` 단위 시험과 VM3 실보드에서 확인한다.

## 아직 확인하지 않은 것

- VM3에 패키지, 계정, unit, 토큰을 넣고 Chromium + Xvfb + ffmpeg를 실행하기 (템플릿 요청 후).
- VM3 메모리(512 MiB)·CPU 실측.
- dom0 relay → HDMI 프레임 출력 실물 (모니터 연결 후).
