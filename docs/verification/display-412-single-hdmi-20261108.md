# 412 HDMI 1대 출력 구성 — 로컬 검증 (2026-11-08)

관련: [S15P31E110-412](https://ssafy.atlassian.net/browse/S15P31E110-412)

## 결정

표시 선택지는 **HDMI 모니터 1대** 또는 **휴대폰 직접 렌더링** 둘 중 하나다. HDMI에는 휴대폰 화면과 같은 화면을 가로 비율에 맞춰 띄운다. 화면 자체는 프론트 담당이 만들고 있다.

## 바꾼 것

| 파일 | 변경 |
| --- | --- |
| `xenova_display/outputs.py` | 출력 1개(`monitor-1`)를 완전한 구성으로 허용한다. 기존 2개(`monitor-1`·`monitor-2`) 설정도 그대로 유효하다. 출력별 `page`를 추가했다. feed가 제공하는 `/display/monitor-1`, `/display/monitor-2`, `/display/combined`, `/display/mobile` 중에서만 고를 수 있고, 기본값은 `/display/<output_id>`다 |
| `xenova_display/kiosk.py` | Chromium이 `page`에 지정된 화면을 연다. 토큰은 경로·인자에 넣지 않는다 |
| `xenova_display/display_mode.py` | HDMI 준비 판정: 상태 파일의 출력이 `{monitor-1}` 또는 `{monitor-1, monitor-2}`이고, 그 출력이 모두 준비돼야 한다 |
| `xenova_display/viewer.html` | 원격 시연 화면이 실제로 보고된 스트림만 그린다. 출력이 1개면 한 칸 전체 폭에 "화면 연결됨"을 표시하고, 2개면 기존과 같다 |
| `examples/single-hdmi-runtime.json` | HDMI 1대 예제. `page: /display/mobile`이고, EDID는 설치 때 실제 값으로 바꿔야 한다 |

바꾸지 않은 것:
- `policy.py`의 계기판·인포테인먼트 **논리 영역** 2개 (한 화면 안의 내용 구분).
- 프론트 화면.
- 팀원 소유 배포 파일 `deploy/native/native-x11-runtime.json`(출력 2개 예제). 보드에 HDMI 1대를 적용하려면 `/etc/xenova/display-runtime.json`을 단일 출력으로 바꿔야 한다. 이 예제 파일을 바꿀지는 담당자와 정한다.

## 시험

```bash
cd platform/services/display/router
python -m pytest -p no:cacheprovider -rs tests
```

결과: **464 passed, 1 skipped**. skip은 playwright가 설치돼 있지 않은 기존 `test_viewer_stall.py`다.

새 시험(`test_single_hdmi.py`, `test_display_mode.py` 추가분):
- **설정**
  - 출력 1개 설정과 예제 파일이 유효하다.
  - 기존 2개 설정도 유효하다.
  - 빈 목록, `monitor-2`만 있는 경우, 중복 id, 3개는 거부한다.
- **화면 경로**: `page`는 허용된 화면만 받는다. 다른 경로, 상위 경로, 외부 URL, 쿼리는 거부한다.
- **kiosk 상태**: 출력 1개 구성에서 상태 파일 출력이 정확히 `monitor-1` 하나다.
- **모드 전환**: 출력 1개만 준비돼도 `display_mode hdmi`가 통과한다. 빈 집합, `monitor-2`만, 모르는 출력이 섞인 경우는 준비가 아니다.
- **원격 시연 스트림**: 출력 1개 구성에서 스트림이 1개만 보고되고, `monitor-2` 스트림은 404다.

## 원격 시연 화면 확인 (앱 내 브라우저, 가짜 프레임)

| 설정 | 결과 |
| --- | --- |
| 출력 1개 | "화면 연결됨", 모니터 1만 전체 폭으로 수신 |
| 출력 2개 | "두 화면 연결됨", 모니터 1·2 모두 수신 (기존과 같음) |

## 아직 확인하지 않은 것

- 보드 물리 HDMI 1대에서 EDID 매핑, kiosk, `display_mode hdmi↔mobile` 왕복 (380/390).
- HDMI에서 `/display/mobile`(또는 팀원이 만드는 새 화면)이 1280×720 가로로 제대로 보이는지. 화면 완성 후 확인한다.
