# xenova-perception — 젯슨 대역 인식 프로그램

젯슨(Jetson Nano + D435f)이 붙기 전까지 PC 에서 젯슨 자리를 대신하는 인식 프로그램입니다 (S15P31E110-156).
노트북 웹캠·샘플 영상·합성 시나리오에서 장애물(과 가능하면 차선)을 찾아
[MQTT 메시지 계약 v1](../../../docs/contracts/mqtt-contract-v1.md) v1.1 그대로 `perception.result` 를 냅니다.
자율주행 VM(-155)과 시연은 진짜 젯슨이 오기 전에도 이 프로그램으로 인지 입력을 받을 수 있습니다.

> **-156 재정의 사유.** 원래 -156 은 "Debian 안전감시 VM 이 웹캠·샘플 영상으로 전방 장애물 탐지" 였습니다.
> 10-19 시나리오 변경([RC카 시나리오](../../../docs/scenario-rc-car.md) §1)으로 장애물 인식은 외부 젯슨이 맡게 되어,
> 팀장 결정으로 -156 을 "젯슨이 붙기 전까지 쓰는 PC 대역" 으로 바꿨습니다. 진짜 젯슨 코드는 `vehicle/` 에 둡니다.

| 방향 | 토픽 | `type` | 비고 |
| --- | --- | --- | --- |
| 발행 | `xenova/v1/apps/perception/events` | `perception.result` | 15 Hz, QoS 0, retain X, 봉투 `source_id: jetson`, `app_id: perception` |

payload (계약 5.4 의 네 필드만, 다른 필드는 싣지 않습니다):

```json
{"lane_offset_m": 0.032, "heading_err_rad": -0.061, "obstacles": [{"x": 0.665, "y": 0.0, "w": 0.25}], "stop": true}
```

봉투·seq·QoS 는 공용 라이브러리 [`xenova_mqtt`](../../services/vm-agent/mqtt-client) 가 붙입니다 (수동 앱·시뮬레이터와 같음).
`app.heartbeat`(6.1)는 앱 VM 용이라 내지 않습니다. 젯슨 끊김은 받는 쪽이 `perception.result` 가 끊긴 것으로 판정합니다 (시나리오 F6).

## 실행

Python 3.11 이상, paho-mqtt 1.6 또는 2.x. webcam·video 는 OpenCV 가 필요합니다 (`pip install -e .[camera]`).
synthetic 과 테스트는 OpenCV 없이 돕니다.

```bash
cd platform/apps/perception
export PYTHONPATH=.:../../services/vm-agent/mqtt-client      # Windows Git Bash 는 ';' 구분
export XENOVA_PERCEPTION_BROKER_PASSWORD=...                  # 비밀번호는 환경변수로 (저장소에 넣지 않는다)

# 1) 합성 시나리오 — 카메라 없이 (시험·CI·데모 백업)
python -m xenova_perception --source synthetic --scenario approach

# 2) 노트북 웹캠 — 빨간 콘·종이를 카메라 앞에 놓는다
python -m xenova_perception --source webcam --device 0 --show

# 3) 샘플 영상 — 끝나면 처음부터 반복, 파일 FPS 로 재생
python -m xenova_perception --source video --video road.mp4 --show
```

| 옵션 | 기본 | 설명 |
| --- | --- | --- |
| `--source` | `synthetic` | `synthetic` / `webcam` / `video` |
| `--device` | `0` | 웹캠 장치 번호 |
| `--video` | — | 영상 파일 (video 필수) |
| `--scenario` | `approach` | 합성 시나리오 (아래 표) |
| `--show` | 끔 | 탐지 박스·정지 영역·수평선 미리보기 창 (`q`/Esc 로 종료). webcam·video 만 |
| `--camera-height` / `--camera-pitch` / `--hfov` | 0.15 m / 15° / 60° | 카메라 높이, 아래로 숙인 각, 수평 화각 |
| `--stop-distance` / `--vehicle-width` / `--margin` | 0.8 m / 0.20 m / 0.05 m | 정지 영역 |
| `--duration` | 없음 | 이 시간(초) 뒤 종료 (시험용) |
| `--host` `--port` `--username` `--password` | `127.0.0.1` `1883` `perception` — | 브로커. 무인증 브로커면 `--username ""` |

환경변수(명령행이 우선): `XENOVA_PERCEPTION_BROKER_HOST`, `_BROKER_PORT`, `_BROKER_USERNAME`, `_BROKER_PASSWORD`, `_LOG_LEVEL`.

### 합성 시나리오

| 이름 | 주기 | 내용 |
| --- | --- | --- |
| `approach` | 12 s | 0~2 s 빈 길 → 3 m 앞 콘이 0.5 m/s 로 다가와 0.5 m 에서 멈춤 (6.4 s 부터 `stop`) → 8 s 치움 |
| `cut-in` | 10 s | 0~2 s 빈 길 → 0.6 m 앞, 왼쪽 0.8 m 에서 콘이 0.25 m/s 로 끼어듦 (약 4.1 s 부터 `stop`) → 6 s 치움 |
| `clear` | 6 s | 장애물 없음 |

모든 시나리오에서 차선은 ±5 cm, 6 s 주기로 흔들립니다. `stop` 은 시나리오가 정하지 않고 실제 입력과 같은 정지 영역 판정을 거칩니다.

## 좌표계·단위 (가정 — 계약에 아직 없음)

계약 5.4 는 필드 이름과 타입만 정하고 원점·축·부호를 정하지 않았습니다. 이 프로그램은 아래로 가정했고,
자율주행 VM(-155)과 진짜 젯슨이 같은 가정을 쓰도록 계약에 적어야 합니다.

| 값 | 단위 | 가정 |
| --- | --- | --- |
| 원점 | — | 카메라 바로 아래 지면 (차 앞범퍼가 아님) |
| `obstacles[].x` | m | 전방 거리. 장애물이 지면에 닿은 아랫변 가운데까지 |
| `obstacles[].y` | m | 가로 위치, **왼쪽 +** (x 전방·y 왼쪽·z 위 오른손 좌표) |
| `obstacles[].w` | m | 아랫변의 좌우 폭 |
| `obstacles` 순서 | — | 가까운(x 작은) 순, 최대 8개 |
| `lane_offset_m` | m | 차선 중심에 대한 차의 가로 위치, **+ = 차가 중심보다 왼쪽** |
| `heading_err_rad` | rad | 차의 진행 방향에서 본 차선 방향, **+ = 차선이 왼쪽으로 돈다** (조향 `steer_rad` + 와 같은 쪽) |
| `stop` | bool | 정지 영역(0 ≤ x < 0.8 m, \|y\| − w/2 < 차폭/2 + 여유 = 0.15 m)에 장애물이 조금이라도 걸침 |

값은 소수 셋째 자리까지 반올림하고, NaN·무한대는 0 으로 냅니다.

## 인식 방법

학습 모델 없이 색 임계값만 씁니다. 설명하기 쉽고 PC 에서 가볍게 돕니다.

- **장애물**: HSV 빨강·진한 주황(H 0~15, 165~179, S ≥ 120, V ≥ 70) 마스크 → 열기·닫기 → 연결 요소 중 화면의 0.1 % 이상인 덩어리.
  박스 아랫변의 가운데·양 끝을 지면에 투영해 x·y·w 를 냅니다.
- **지면 투영**: 평평한 바닥, 롤 0, 정사각 픽셀 핀홀 카메라. 초점거리는 수평 화각에서 구합니다. 수평선 위 픽셀은 버립니다.
- **차선**: 흰색(S ≤ 40, V ≥ 200)·노란색(H 20~35) 마스크를 수평선 아래 4 행 간격으로 뽑아 지면에 투영,
  전방 0.15~2.5 m 의 점을 왼쪽(y > 0)·오른쪽(y < 0)으로 나눠 직선 y = a + b·x 를 맞춥니다.
  두 선의 가운데를 차선 중심으로 보고(한쪽만 보이면 차로 폭 0.35 m 의 절반만큼 옮김) `lane_offset_m = −a`, `heading_err_rad = atan(b)`.
  **선을 못 찾으면 두 값 모두 0** 으로 냅니다 (계약에 "못 찾음" 을 나타낼 필드가 없음).

## 발행 주기

프레임 처리와 발행을 떼어 놓았습니다.

- 발행은 마감 시각을 누적하는 15 Hz 고정 주기입니다 (10 s 에 150 건).
- 프레임이 15 fps 보다 느리면 **최신 결과를 다시 냅니다** (새 `seq`). 받는 쪽은 latest-only 라 해가 없고, 주기가 일정해야 끊김 판정이 쉽습니다.
- 최신 결과가 **0.5 s 보다 오래되면 내지 않습니다.** 카메라가 멈췄는데 옛 결과를 계속 내면 자율주행 VM 이 젯슨 끊김(F6 → 정지)을 알아채지 못하기 때문입니다.
- synthetic 은 발행할 때마다 그 시각의 결과를 만들므로 늘 신선합니다.

## 한계

- 색 기반이라 조명·배경에 약합니다. 빨간 옷·벽, 흰 벽·반사광이 장애물·차선으로 잡힐 수 있습니다. 시연은 빨간 콘·흰 테이프처럼 색이 분명한 것을 씁니다.
- 거리는 평평한 바닥과 설정한 카메라 높이·각도가 맞을 때만 맞습니다. 노트북을 책상에 둔 웹캠은 실제 장착값과 달라 거리가 크게 틀립니다 (위치·부호·`stop` 변화를 보는 시연용).
- 차선은 차가 대략 차로 가운데 있을 때만 맞습니다 (y 부호로 왼쪽·오른쪽 선을 나누기 때문). 곡선은 직선으로 근사합니다.
- 장애물 종류·속도·추적은 없습니다. 프레임마다 따로 찾습니다.
- D435f 깊이는 쓰지 않습니다 (진짜 젯슨 몫).

## 브로커 계정

계정 `perception` (계약 9장, [브로커 구성](../../deploy/mosquitto/README.md)). ACL `pattern write xenova/v1/apps/%u/events` 로
자기 토픽에만 발행할 수 있습니다. 로컬 개발 브로커의 비밀번호는 `platform/deploy/mosquitto/local/.env` 의 `XENOVA_MQTT_DEV_PASSWORD` 입니다.

## 테스트

```bash
python -m pytest        # 투영·정지 판정·합성 시나리오 순서·payload 필드와 타입·봉투·15 Hz(가짜 시계)·오래된 결과 건너뜀
```

OpenCV 가 설치돼 있으면 numpy 로 그린 합성 프레임(빨간 사각형, 흰 선)에서 실제 탐지 경로도 시험합니다. 없으면 그 시험만 skip 합니다.
