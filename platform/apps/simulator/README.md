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

게이트웨이 SerialSink 는 지금 `serial.Serial(port)` 로만 열기 때문에 TCP 로 보내려면 둘 중 하나가 필요합니다.

- dom0 에서 가상 시리얼: `socat pty,link=/tmp/ttyXENOVA,raw,echo=0 tcp:<PC IP>:7600` 후 게이트웨이 `--sink serial --serial-port /tmp/ttyXENOVA`
- 게이트웨이가 `serial.serial_for_url()` 을 쓰도록 바꾸고 `--serial-port socket://<PC IP>:7600` (S15P31E110-269, 게이트웨이를 최근 고친 박신영 님 리뷰)

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
| `sim.pose` | `xenova/v1/sim/pose` | 20 Hz, QoS 0, 계정·`source_id` `simulator`. `{x, y, yaw, v_mps, steer_rad, world_revision, surface}` (트랙 고정 좌표) |
| `sim.world` | `xenova/v1/sim/world` | 바뀔 때, QoS 1 retained. `{revision, track, tracks, car, time_scale, obstacles, estop}`. 정상 종료 시 retained 삭제 |
| `sim.command.result` | `xenova/v1/sim/result` | 명령마다 한 번. `{request_id, op, ok, code, message, gap_m, need_m}` |

시뮬레이터 전용 토픽은 [MQTT 계약](../../../docs/contracts/mqtt-contract-v1.md) 12장입니다. 관람 화면(모니터 1 세 번째 칸·관리 콘솔)이 백엔드
REST-30·WS-06 을 거쳐 그리고, 관리 콘솔 버튼은 REST-31~37 → `xenova/v1/sim/command`(구독)로 시뮬레이터를 조작합니다
(장애물 놓기·옮기기·치우기, E-stop, 차 밀기, 트랙 바꾸기, 다시 출발). 배치 검사는 차와 겹침(`ON_CAR`)·벽 밖(`OUT_OF_TRACK`)만 합니다.
**젯슨 `perception` 계정은 브로커 ACL 로 `sim/*` 를 읽을 수 없습니다** — 젯슨은 카메라 화면만 봅니다.

## 차량·트랙 모델

| 항목 | 값 (`config.py`, 실차 측정 후 맞춤) |
| --- | --- |
| 트랙 | 실제 서킷 10개 ([MQTT 계약](../../../docs/contracts/mqtt-contract-v1.md) v1.4 §11, 관리 콘솔 mock 과 같은 중심선·벽). 차선 폭 0.6 m, 연석 0.06 m, 연석 밖 벽까지 잔디(코너 바깥은 흙). 출발선 = 가장 긴 직선의 30 % 지점. 기본 `spielberg` |
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
| `s` | 트랙·위치·차로 중심 이탈·노면·한 바퀴 진행·바퀴 rpm·엔코더·속도·조향·세대·플래그·프레임 수 한 줄 |
| `r` | 출발선으로 (속도 0) |
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

- 콘솔 mock 의 `NO_GAP`·`TOO_DENSE`·`TOO_CLOSE`(회피 주행 미리 돌려 보기)·출발 신호·랩(`sim.pose.race`)은 하지 않습니다. 지금 자율 앱은 장애물을 피하지 않고 섭니다.
- 인지 대역(색 기반)의 차선 정확도 (레드불링·헝가로링·몬자 중심선 위 표본, 실제 트랙 값과 비교): 오프셋 오차 p95 0.14 m,
  방향 오차 p95 0.25 rad, 급한 코너 최대 0.54 rad. 60° 카메라에 바깥 선 하나만 보이는 코너에서 크다. 폐루프는 오프셋 보정으로 버틴다.
- 연석은 실제처럼 빨강·흰 줄이 아니라 파랑입니다 (색 기반 인지가 장애물·차선으로 착각하지 않게). 벽은 바닥 색으로만 그립니다.

- 평평한 지면, 미끄럼 없음, 조명·그림자·흔들림 없는 깨끗한 화면입니다. 실제 카메라보다 인지가 쉽습니다.
- ESP32 → 게이트웨이 상태 프레임(엔코더·적용 확인)은 형식이 없어(-241) 보내지 않습니다. `vehicle.status` 를 MQTT 로 직접 냅니다.
- `mqtt` 입력은 게이트웨이의 출력 허가를 모릅니다. 게이트웨이가 출력을 막아도 차가 움직일 수 있으니 시연은 `frames` 로 합니다.
