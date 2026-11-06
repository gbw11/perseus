# xenova-simulator — 가상 RC카

실차(ESP32·모터·서보·카메라)가 없을 때 주행 앱을 시연·시험하기 위한 가상 RC카입니다 (S15P31E110-47, -268).
**게이트웨이 뒤의 ESP32와 차체, 차 앞 카메라**를 흉내 냅니다. 모드 전환(제어권 판정)은 하지 않습니다 — dom0 게이트웨이의 일입니다.

```text
시뮬레이터 전방 카메라 ──HTTP MJPEG──▶ 젯슨(인지) ──perception.result──▶ Zephyr 자율 앱 ──drive.command──▶ 게이트웨이
        ▲                                                                                            │ 시리얼 프레임
        └──────────── 바퀴 회전수·전륜각 → 차체 이동 ◀── 가상 ESP32 (이 시뮬레이터) ◀──────────────────┘
```

젯슨에는 **차 앞 카메라 화면만** 갑니다. 트랙 모양·차 위치·장애물 좌표는 주지 않습니다 (실차와 같은 조건).
차를 목표 속도로 바로 옮기지 않고, ESP32 가 받은 목표 속도를 바퀴 회전수로 바꿔 모터·엔코더가 돌고,
목표 전륜각을 서보가 따라간 만큼 자전거 모델로 움직입니다.

## 입력: ESP32 가 받는 명령 (`--input`)

| 입력 | 받는 것 | 언제 |
| --- | --- | --- |
| `frames` | 게이트웨이가 ESP32 로 보내는 **시리얼 프레임**을 TCP 로 (`--frame-listen`, 기본 `0.0.0.0:7600`) | 실제 시연 경로. 게이트웨이의 출력 허가·재무장·정지 프레임이 그대로 반영된다 |
| `mqtt` (기본) | 앱의 `drive.command` 를 MQTT 에서 직접 | 게이트웨이 없이 앱만 시험할 때. 게이트웨이의 출력 허가·재무장은 **보이지 않는다** |

**frames** — 프레임 형식은 [게이트웨이 README](../../services/gateway/README.md#esp32-시리얼-프레임-초안)와 코덱 `xenova_gateway/frame.py` 를 그대로 씁니다
(COBS + CRC-16, drive `{generation, counter, v mm/s, steer mrad}`, stop). ESP32 쪽 규칙:

| 상황 | 차 | 플래그 |
| --- | --- | --- |
| 아직 프레임 없음 | 움직이지 않음 (세션 전) | — |
| drive 프레임 | 목표 속도 → 바퀴 회전수, 목표 전륜각 → 서보 | — |
| stop 프레임 | 정지 (게이트웨이 `SAFE_STOP`·종료) | — |
| CRC·길이 틀림 | 버리고 다음 `0x00` 부터 다시 맞춤 | — |
| 같은 세대에서 counter 중복·역전, 더 낮은 세대 | 버림 | — |
| 유효 프레임이 300 ms 없음 | 정지 | `watchdog` |

게이트웨이 SerialSink 는 `serial.serial_for_url()` 로 열므로 `--serial-port socket://<PC IP>:7600` 으로 바로 붙습니다 (S15P31E110-269, 게이트웨이 README).

- 보조: dom0 에서 가상 시리얼 `socat pty,link=/tmp/ttyXENOVA,raw,echo=0 tcp:<PC IP>:7600` 후 게이트웨이 `--sink serial --serial-port /tmp/ttyXENOVA`
- 게이트웨이의 write-only(legacy DRIVE·STOP 프레임) 출력만 흉내 냅니다. feedback 프로필(HELLO·COMMAND 프레임)은 받지 않습니다.
- 게이트웨이 연결이 2 초 동안 아무것도 보내지 않으면(FIN 없이 끊긴 연결) 닫고 다음 연결을 기다립니다.

**mqtt** — 구독하는 토픽:

| 토픽 | `type` | 비고 |
| --- | --- | --- |
| `xenova/v1/vehicle/control` | `vehicle.control` | retained. `generation`·`active_app`·`active_vm_id` |
| `xenova/v1/vms/{vm_id}/apps/{autonomous,manual}/events` | `drive.command` | 공통 A/B 펌웨어(verified). `active_vm_id` 의 활성 앱 것만 |
| `xenova/v1/apps/{autonomous,manual}/events` | `drive.command` | legacy 분리 앱 |

같은 토픽의 `app.heartbeat`·`app.readiness` 는 무시합니다. 세대가 다른 명령·대기 앱 명령은 버리고, 300 ms 끊기면 `watchdog` 입니다.

## 출력

| 무엇 | 어디 | 비고 |
| --- | --- | --- |
| 전방 카메라 | `GET http://<PC>:8090/camera/stream.mjpg`, `/camera/snapshot.jpg` (`--camera`, `off` 면 끔) | 320×240, 15 fps, JPEG. 마지막 프레임이 0.5 s 넘게 오래되면 snapshot 은 503 |
| `vehicle.status` | `xenova/v1/vehicle/status` | 20 Hz, QoS 0, `source_id: dom0-gateway` (게이트웨이 상태 중계가 생기기 전 대역). `v_mps` 는 엔코더 count 로 잰 속도, `steer_rad` 는 서보가 만든 각 |
| `sim.pose` | `xenova/v1/sim/pose` | 20 Hz, QoS 0, 계정·`source_id` `simulator`. `{x, y, yaw, v_mps, steer_rad, world_revision, surface, race}` (트랙 고정 좌표). `race` = 출발 신호·랩 (아래) |
| `sim.world` | `xenova/v1/sim/world` | 바뀔 때, QoS 1 retained. `{revision, track, tracks, car, time_scale, obstacles, estop}`. 정상 종료 시 retained 삭제 |
| `sim.command.result` | `xenova/v1/sim/result` | 명령마다 한 번. `{request_id, op, ok, code, message, gap_m, need_m}` |

시뮬레이터 전용 토픽은 [MQTT 계약](../../../docs/contracts/mqtt-contract-v1.md) 12장입니다. 관람 화면(모니터 1 세 번째 칸·관리 콘솔)이 백엔드
REST-30·WS-06 을 거쳐 그리고, 관리 콘솔 버튼은 REST-31~37 → `xenova/v1/sim/command`(구독)로 시뮬레이터를 조작합니다
(장애물 놓기·옮기기·치우기, E-stop, 차 밀기, 트랙 바꾸기, 다시 출발).

**배치 검사** (`placement.py`, 관리 콘솔 mock `simEngine.ts` checkPlacement 와 같은 식·순서. 무작위 300개 배치에서 mock 과 결과가 모두 같음을 확인):

| 코드 | 거부하는 자리 |
| --- | --- |
| `OUT_OF_TRACK` | 장애물이 벽 밖으로 조금이라도 나감 (\|off\| > 벽 − 폭/2) |
| `ON_CAR` | 차 몸과 겹침 |
| `NO_GAP` | 장애물 앞뒤(차 길이만큼)에서 가장 넓은 틈 < 0.26 m (차 폭 0.20 + 양쪽 0.03). 결과에 `gap_m`·`need_m` |
| `TOO_DENSE` | 틈은 있지만 기준 회피 주행(mock 자율 규칙)을 묶음 4.8 m 앞에서 돌려 보면 서거나 닿음 |
| `TOO_CLOSE` | 출발 뒤(`race.phase = go`) 달리는 차 바로 앞(−0.3 ~ 4.8 m)이라 지금 상태로 돌려 봐도 못 지나감 |

`TOO_DENSE`·`TOO_CLOSE` 의 "기준 회피 주행"은 mock 의 자율 규칙과 mock 차 모델이다. 지금 Zephyr 자율 앱은 장애물 앞에서 서기만 하므로
실제 주행과 같은 판정은 아니고, 콘솔 화면이 mock 과 같은 자리를 같은 이유로 거부하게 하는 것이다. 검사 한 번에 보통 0.2 초, 길면 0.8 초.

**출발 신호·랩** (`race.py`, 시나리오 P7, mock `newRace`·`advance` 와 같음): 다시 출발(`race.start`·`track.select`·터미널 `r`·시작) 0.7 초 뒤부터
빨간 불이 1 초마다 켜지고(5개), 0.2 ~ 3.0 초 뒤 모두 꺼지면 `go`. 출발 신호 중에는 수동이 아닌 활성 앱(자율)의 전진을 막아 출발선에 둔다
(자율 앱은 신호를 보지 않으므로). 수동은 막지 않으며, 불이 켜진 뒤 전진하면 부정 출발 → 첫 바퀴 +5 초.
`frames` 입력은 활성 앱을 모르므로 늘 막고 부정 출발은 판정하지 않는다. 랩은 반 바퀴를 지난 뒤 출발선을 넘을 때. `--no-start-lights` 면 바로 `go`.

**노면**: 연석 밖(잔디·흙)에서는 0.3 m/s 를 넘으면 1.2 m/s² 로 줄어 0.3 m/s 에 머문다 (mock physics 와 같음, 판정 점 = 뒷바퀴 축 가운데).
**젯슨 `perception` 계정은 브로커 ACL 로 `sim/*` 를 읽을 수 없습니다** — 젯슨은 카메라 화면만 봅니다.

## 차량·트랙 모델

| 항목 | 값 (`config.py`, 실차 측정 후 맞춤) |
| --- | --- |
| 트랙 | 실제 서킷 10개 ([MQTT 계약](../../../docs/contracts/mqtt-contract-v1.md) §12, 관리 콘솔 mock 과 같은 중심선·벽). 차선 폭 0.6 m, 연석 0.06 m, 연석 밖 벽까지 잔디(코너 바깥은 흙). 출발선 = 가장 긴 직선의 30 % 지점. 기본 `spielberg` |
| 구동 | 후륜, 바퀴 반지름 33 mm, 엔코더 1320 count/바퀴, 모터 시정수 0.12 s, 최고 450 rpm, 가속 2 m/s², 감속 4 m/s² |
| 조향 | 가상 전륜각 ±0.45 rad, 서보 5 rad/s, 축간거리 0.26 m. 실제 각도 센서는 없다고 본다 |
| 카메라 | 후륜 축 앞 0.20 m, 높이 0.15 m, 15° 숙임, 수평 화각 60° — **인지 대역 기본값과 같아야 거리가 맞는다** |
| 근거리 감지 | 앞바퀴 축에서 전방 0.5 m·±30° 안 장애물이면 전진을 끊는다 (ESP32 ToF 흉내) → `obstacle` |

## 실행

Python 3.11 이상, paho-mqtt 1.6 또는 2.x, numpy. 카메라는 OpenCV (`pip install -e .[camera]`).

```bash
cd platform/apps/simulator
export PYTHONPATH=.:../../services/vm-agent/mqtt-client:../../services/gateway   # Windows Git Bash 는 ';' 구분

# 중간점검: 게이트웨이 시리얼 프레임 + 전방 카메라
python -m xenova_simulator --input frames --frame-listen 0.0.0.0:7600 --camera 0.0.0.0:8090 --track spielberg --host <브로커> --password "$PW"
# 게이트웨이 없이 앱만 (무인증 개발 브로커)
python -m xenova_simulator --input mqtt --host 127.0.0.1 --port 1883 --username "" --sim-username ""
# 브로커 계정: vehicle.status 는 gateway(--password), sim/* 는 simulator(--sim-password). 전용 토픽을 끄려면 --no-sim-topics
```

젯슨(또는 PC 의 인지 대역)은 카메라 주소만 압니다:

```bash
python -m xenova_perception --source stream --url http://<시뮬레이터 PC>:8090/camera/stream.mjpg --show
```

실행 중 명령 (입력 후 Enter):

| 명령 | 하는 일 |
| --- | --- |
| `o [거리m] [cone\|box\|tires]` | 진행 방향 앞에 장애물 (기본 1.5 m, 콘). 폭은 종류로 정한다: 콘 0.18·박스 0.30·타이어 0.26 m (계약 v1.4) |
| `t [트랙id]` | 트랙 바꾸기 (장애물 치우고 출발선으로). id 없이 치면 목록 |
| `c` | 장애물 모두 치우기 |
| `e` | 비상정지 켜기/끄기 |
| `s` | 트랙·위치·차로 중심 이탈·노면·한 바퀴 진행·바퀴 rpm·엔코더·속도·조향·세대·플래그·출발 신호·랩·프레임 수 한 줄 |
| `r` | 출발선으로 (속도 0), 출발 신호 처음부터 |
| `q` | 종료 |

### 보드 없이 폐루프 리허설

```bash
python tools/closed_loop.py --track spielberg --laps 1   # 렌더 → 인지 Detector → 자율 계산(autonomous.c 대역) → 시리얼 프레임 → 바퀴
python tools/closed_loop.py --all-tracks --laps 1        # 10개 트랙 모두 (10분쯤)
python tools/closed_loop.py --seconds 15 --obstacle 2.0
```

PYTHONPATH 에 `../perception` 도 넣습니다. MQTT·Zephyr·게이트웨이 상태 기계는 거치지 않으므로
"카메라 화면만으로 인지·자율 계산이 트랙을 도는가"만 보여 줍니다.

2026-10-29 결과 (0.5 m/s): 10개 트랙 모두 한 바퀴, 차로(±0.3 m) 밖 0회, 차로 중심 최대 이탈 0.115~0.142 m
(레드불링 83 m 177 s ~ 스파 135 m 284 s). 직선 2 m 앞 장애물은 0.75 m 앞에서 선다.

트랙 데이터는 관리 콘솔 mock(`frontend/apps/admin-console/src/mocks/tracks.ts`)에서 `python tools/export_tracks.py` 로
`xenova_simulator/tracks.json` 에 옮깁니다. 벽 계산은 mock `simEngine.ts` buildTrack 과 같은 식이며 10개 트랙 모두 값이 같은지 대조했습니다.

브로커 계정·ACL은 [브로커 구성](../../deploy/mosquitto/README.md).

## 테스트

```bash
python -m pytest        # 바퀴·엔코더·서보·자전거 모델, 트랙, 시리얼 프레임 규칙·TCP, verified 토픽, 와치독·비상정지,
                        # 카메라 → 인지 거리·부호, MJPEG HTTP, 인지 stream 입력, 폐루프 한 바퀴·장애물 정지
```

카메라·폐루프 시험은 OpenCV 가 없으면 건너뜁니다.

## 한계

- 배치 검사(`NO_GAP` 등)는 mock 의 기준 회피 주행으로 미리 돌려 보지만, 지금 자율 앱 자체는 장애물을 피하지 않고 섭니다.
- 인지 대역(색 기반)의 차선 정확도는 10개 트랙 한 바퀴에서 오프셋 오차 p95 0.035 m·방향 오차 p95 0.060 rad (S15P31E110-320,
  `../perception/README.md` 결과표). 곡률이 바뀌는 곳·머리핀에서 방향 오차가 0.3~0.75 rad 까지 튄다.
- 연석은 실제처럼 빨강·흰 줄이 아니라 파랑입니다 (색 기반 인지가 장애물·차선으로 착각하지 않게). 벽은 바닥 색으로만 그립니다.

- 평평한 지면, 미끄럼 없음, 조명·그림자·흔들림 없는 깨끗한 화면입니다. 실제 카메라보다 인지가 쉽습니다.
- ESP32 → 게이트웨이 상태 프레임(엔코더·적용 확인)은 형식이 없어(-241) 보내지 않습니다. `vehicle.status` 를 MQTT 로 직접 냅니다.
- `mqtt` 입력은 게이트웨이의 출력 허가를 모릅니다. 게이트웨이가 출력을 막아도 차가 움직일 수 있으니 시연은 `frames` 로 합니다.
