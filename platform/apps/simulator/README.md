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
| `sim.pose` | `xenova/v1/sim/pose` | 20 Hz, QoS 0, 계정·`source_id` `simulator`. `{x, y, yaw, v_mps, steer_rad, world_revision, surface, race}` (트랙 고정 좌표). `race` = 출발 신호·랩 (아래) |
| `sim.world` | `xenova/v1/sim/world` | 바뀔 때, QoS 1 retained. `{revision, track, tracks, car, time_scale, obstacles, estop}`. 새 Jetson 코스만 `track.layout` 추가. 공통 봉투 포함 UTF-8 64 KiB 이하. 정상 종료 시 retained 삭제 |
| `sim.command.result` | `xenova/v1/sim/result` | 명령마다 한 번. `{request_id, op, ok, code, message, gap_m, need_m}` |

시뮬레이터 전용 토픽은 [MQTT 계약](../../../docs/contracts/mqtt-contract-v1.md) 12장입니다. 관람 화면(모니터 1 세 번째 칸·관리 콘솔)이 백엔드
REST-30·WS-06 을 거쳐 그리고, 관리 콘솔 버튼은 REST-31~37 → `xenova/v1/sim/command`(구독)로 시뮬레이터를 조작합니다
(장애물 놓기·옮기기·치우기, E-stop, 차 밀기, 트랙 바꾸기, 다시 출발).

**기존 서킷 10개의 배치 검사** (`placement.py`, 관리 콘솔 mock `simEngine.ts` checkPlacement와 같은 식·순서. 기존 무작위 300개 배치에서 mock과 결과가 모두 같음을 확인):

| 코드 | 거부하는 자리 |
| --- | --- |
| `OUT_OF_TRACK` | 장애물이 벽 밖으로 조금이라도 나감 (\|off\| > 벽 − 폭/2) |
| `ON_CAR` | 차 몸과 겹침 |
| `NO_GAP` | 장애물 앞뒤(차 길이만큼)에서 가장 넓은 틈 < 0.26 m (차 폭 0.20 + 양쪽 0.03). 결과에 `gap_m`·`need_m` |
| `TOO_DENSE` | 틈은 있지만 기준 회피 주행(mock 자율 규칙)을 묶음 4.8 m 앞에서 돌려 보면 서거나 닿음 |
| `TOO_CLOSE` | 출발 뒤(`race.phase = go`) 달리는 차 바로 앞(−0.3 ~ 4.8 m)이라 지금 상태로 돌려 봐도 못 지나감 |

`TOO_DENSE`·`TOO_CLOSE` 의 "기준 회피 주행"은 mock 의 자율 규칙과 mock 차 모델이다. 지금 Zephyr 자율 앱은 장애물 앞에서 서기만 하므로
실제 주행과 같은 판정은 아니고, 콘솔 화면이 mock 과 같은 자리를 같은 이유로 거부하게 하는 것이다. 검사 한 번에 보통 0.2 초, 길면 0.8 초.

Jetson 코스는 전체 장애물 box가 도로·추가 지면의 합집합에 들어가는지(`OUT_OF_TRACK`)와 현재 차체 겹침(`ON_CAR`)만 검사합니다. 네 corner만 검사하지 않으며, 기존 단일 서킷용 `NO_GAP`·`TOO_DENSE`·`TOO_CLOSE` trial은 사용하지 않습니다. 이 수락과 초기 seed는 회피 가능성·완주·주차 성공의 증거가 아닙니다.

**출발 신호·랩** (`race.py`, 시나리오 P7, mock `newRace`·`advance` 와 같음): 다시 출발(`race.start`·`track.select`·터미널 `r`·시작) 0.7 초 뒤부터
빨간 불이 1 초마다 켜지고(5개), 0.2 ~ 3.0 초 뒤 모두 꺼지면 `go`. 출발 신호 중에는 수동이 아닌 활성 앱(자율)의 전진을 막아 출발선에 둔다
(자율 앱은 신호를 보지 않으므로). 수동은 막지 않으며, 불이 켜진 뒤 전진하면 부정 출발 → 첫 바퀴 +5 초.
`frames` 입력은 활성 앱을 모르므로 늘 막고 부정 출발은 판정하지 않는다. 랩은 반 바퀴를 지난 뒤 출발선을 넘을 때. `--no-start-lights` 면 바로 `go`.

**노면**: 연석 밖(잔디·흙)에서는 0.3 m/s 를 넘으면 1.2 m/s² 로 줄어 0.3 m/s 에 머문다 (mock physics 와 같음, 판정 점 = 뒷바퀴 축 가운데).
**젯슨 `perception` 계정은 브로커 ACL 로 `sim/*` 를 읽을 수 없습니다** — 젯슨은 카메라 화면만 봅니다.

## 차량·트랙 모델

| 항목 | 값 (`config.py`, 실차 측정 후 맞춤) |
| --- | --- |
| 트랙 | 실제 서킷 10개 ([MQTT 계약](../../../docs/contracts/mqtt-contract-v1.md) v1.4 §11, 관리 콘솔 mock 과 같은 중심선·벽). 차선 폭 0.6 m, 연석 0.06 m, 연석 밖 벽까지 잔디(코너 바깥은 흙). 출발선 = 가장 긴 직선의 30 % 지점. 기본 `spielberg` |
| 구동 | 후륜, 바퀴 반지름 33 mm, 엔코더 1320 count/바퀴, 모터 시정수 0.12 s, 최고 450 rpm, 가속 2 m/s², 감속 4 m/s² |
| 조향 | 가상 전륜각 ±0.45 rad, 서보 5 rad/s, 축간거리 0.26 m. 실제 각도 센서는 없다고 본다 |
| 카메라 | 후륜 축 앞 0.20 m, 높이 0.15 m, 15° 숙임, 수평 화각 60° — **인지 대역 기본값과 같아야 거리가 맞는다** |
| 근거리 감지 | 앞바퀴 축에서 전방 0.5 m·±30° 안 장애물이면 전진을 끊는다 (ESP32 ToF 흉내) → `obstacle` |

### Jetson 두 차로 시험 코스

기존 10개 서킷과 기본 `spielberg`를 유지하고 다음 6개 코스를 추가합니다. 반경은 **안쪽 차로 중심** 기준입니다. 모든 변형의 도로 외곽은 4.3 m × 2.8 m, world bounds는 4.5 m × 3.0 m로 고정합니다. 차량 최소 회전반경 0.55 m도 고정하며, 코스 반경과 같은 값으로 바꾸지 않습니다.

| 코스 ID | 화면 표시명 | 안쪽 차로 중심 반경 | 긴 직선 | 짧은 직선 |
| --- | --- | --- | --- | --- |
| `jetson-demo` | Jetson 기본 코스 (R60cm) | 0.60 m | 1.9 m | 0.4 m |
| `jetson-r45` | Jetson 비교 코스 (R45cm) | 0.45 m | 2.2 m | 0.7 m |
| `jetson-r55` | Jetson 비교 코스 (R55cm) | 0.55 m | 2.0 m | 0.5 m |
| `jetson-r65` | Jetson 비교 코스 (R65cm) | 0.65 m | 1.8 m | 0.3 m |
| `jetson-r75` | Jetson 비교 코스 (R75cm) | 0.75 m | 1.6 m | 0.1 m |
| `jetson-r80` | Jetson 비교 코스 (R80cm) | 0.80 m | 1.5 m | 0.0 m |

같은 방향 두 차로 각 0.4 m, 전체 도로 폭 0.8 m·연석 0입니다. 고정 외곽에서 반경 R에 따라 긴 직선은 `3.1 - 2*R`, 짧은 직선은 `1.6 - 2*R` m가 됩니다. R80은 짧은 직선이 없는 stadium 형태의 한계입니다. R100은 높이 3 m 조건에 들어가지 않아 R75로 대체했고, 음수 직선이 필요한 반경은 생성하지 않습니다. 반경만으로 해당 차로의 실행 가능·불가능을 판정하지 않습니다.

`track.layout`은 world 좌표 x 오른쪽·y 위쪽(m), yaw 반시계(rad)입니다. 공유 `centerline`은 두 차로 사이 분리선이며 화면의 세 번째 차로가 아닙니다. 여섯 코스 모두 출발선 중심은 `[2.56, 0.5]`, 후륜축 초기 pose는 `[2.81, 0.7, pi]`로 아래 직선 오른쪽에서 왼쪽을 봅니다. 초기 grid는 **앞범퍼를 출발선에 맞추며** 후륜축부터 앞범퍼까지 거리는 0.25 m입니다. 비교 조건을 맞추기 위해 다른 반경 코스도 기본 `jetson-demo`의 출발선·장애물 좌표로 통일했습니다. `sim.pose`는 계속 후륜축 기준입니다. 연결 마당과 두 주차칸은 `paved_areas`, 표시선은 `paint_lines`, 칸 윤곽은 `parking_slots`에 있습니다. 표시선은 지면이나 AUTO 허가를 대신하지 않습니다.

주차칸 2개는 모든 코스에서 전체 x 범위 2.7부터 3.5, 각 칸 0.4 m × 0.6 m입니다. R45/R55/R60/R65는 y=1.1부터 1.7, R75/R80은 도로와 겹치지 않도록 y=1.2부터 1.8에 둡니다. 각 칸의 네 변을 닫아 표시하되 입구선은 도색이므로 통행면은 유지합니다. 연결 마당은 y=0.885부터 각 주차 입구까지 이어지고 하단 직선의 안쪽 경계 y=0.9는 유지합니다. 오른쪽 연결선의 도로 쪽 끝점은 각 코스의 실제 안쪽 코너 경계에 맞추며 마당 포장 범위는 바꾸지 않습니다. 입구 왼쪽 끝은 왼쪽 진행 기준으로 앞범퍼 뒤 0.14 m·카메라 뒤 0.09 m에 있지만, 차체 전체 뒤에 있는 것은 아닙니다. 이 위치 관계와 실제 주차 진입 가능성은 구분합니다.

차량은 길이 0.30 m·폭 0.22 m·wheelbase 0.20 m·최소 회전반경 0.55 m이며 axle 앞뒤 돌출은 각각 0.05 m로 가정합니다. 카메라는 후륜축 앞 0.20 m·높이 0.12 m·아래 숙임 15도·수평 화각 69도입니다. **모두 미실측 profile**이며 실제 차량·카메라 보정값이 아닙니다. 코스를 선택하면 Python 차량·카메라와 화면 모델에 적용하고 기존 서킷으로 돌아가면 원 profile을 복원합니다. 카메라 해상도·발행 주기는 기존 설정을 유지합니다.

처음 실행하거나 코스를 선택할 때 콘 2개 `A`·`B`(폭·높이 각 0.18 m)를 두 차로에 번갈아 배치합니다. 여섯 코스 모두 A 중심은 `[1.92, 0.7]`, B 중심은 `[1.4, 0.3]`입니다. 도면의 0.16 m 표현 대신 기존 `cone` 종류의 0.18 m를 사용합니다. 출발선에 맞춘 앞범퍼와 첫 콘의 가까운 면 사이 x 간격은 0.55 m, 후륜축부터는 0.80 m입니다. 이 간격이 실제 제동·회피 가능성을 뜻하지는 않습니다. `c`로 치운 뒤 `r` 또는 `race.start`만 실행하면 콘을 복구하지 않습니다. 다시 코스를 선택하면 seed를 다시 적용합니다.

두 차로·마당·주차칸을 렌더하고 수동으로 진입할 수 있는 모의 지면을 추가한 것입니다. 이 변경에 자율주차 planner나 새 회피 주행을 넣지는 않습니다. 두 장애물 회피 완주, MANUAL 복귀·A/B 교체, 실제 차량 주차 성공을 확인했다는 뜻도 아닙니다. MQTT의 `real_m`은 새 코스 중심선 길이를 반올림한 표시용 정수이고 UI 배율은 1입니다. 기존 10개 payload는 소수 3자리 표현을 유지하며 새 코스만 공유 소수 6자리 좌표를 그대로 보냅니다.

## 실행

Python 3.11 이상, paho-mqtt 1.6 또는 2.x, numpy. 카메라는 OpenCV (`pip install -e .[camera]`).

```bash
cd platform/apps/simulator
export PYTHONPATH=.:../../services/vm-agent/mqtt-client:../../services/gateway   # Windows Git Bash 는 ';' 구분

# 중간점검: 게이트웨이 시리얼 프레임 + 전방 카메라
python -m xenova_simulator --input frames --frame-listen 0.0.0.0:7600 --camera 0.0.0.0:8090 --track spielberg --host <브로커> --password "$PW"
# 게이트웨이 없이 앱만 (무인증 개발 브로커)
python -m xenova_simulator --input mqtt --host 127.0.0.1 --port 1883 --username "" --sim-username ""
# 새 두 차로 코스. 제어·계정 설정은 위와 같고 기본 트랙은 여전히 spielberg
python -m xenova_simulator --input frames --track jetson-demo --camera 127.0.0.1:8090 --host <브로커> --password "$PW" --sim-password "$SIM_PW"
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
| `t [트랙id]` | 트랙 바꾸기 (기존 장애물 치우고 새 코스 seed 적용 후 출발선으로). id 없이 치면 목록 |
| `c` | 장애물 모두 치우기 |
| `e` | 비상정지 켜기/끄기 |
| `s` | 트랙·위치·차로 중심 이탈·노면·한 바퀴 진행·바퀴 rpm·엔코더·속도·조향·세대·플래그·출발 신호·랩·프레임 수 한 줄 |
| `r` | 출발선으로 (속도 0), 출발 신호 처음부터 |
| `q` | 종료 |

### 보드 없이 폐루프 리허설

```bash
python tools/closed_loop.py --track spielberg --laps 1   # 렌더 → 인지 Detector → 자율 계산(autonomous.c 대역) → 시리얼 프레임 → 바퀴
python tools/closed_loop.py --all-tracks --laps 1        # 기존 단일 차로 서킷 10개 회귀
python tools/closed_loop.py --seconds 15 --obstacle 2.0
```

PYTHONPATH 에 `../perception` 도 넣습니다. MQTT·Zephyr·게이트웨이 상태 기계는 거치지 않으므로
"카메라 화면만으로 인지·자율 계산이 트랙을 도는가"만 보여 줍니다.

2026-10-29 결과 (0.5 m/s): 10개 트랙 모두 한 바퀴, 차로(±0.3 m) 밖 0회, 차로 중심 최대 이탈 0.115~0.142 m
(레드불링 83 m 177 s ~ 스파 135 m 284 s). 직선 2 m 앞 장애물은 0.75 m 앞에서 선다.

트랙 데이터는 관리 콘솔 mock(`frontend/apps/admin-console/src/mocks/tracks.ts`)에서 `python tools/export_tracks.py` 로
`xenova_simulator/tracks.json` 에 옮깁니다. 벽 계산은 mock `simEngine.ts` buildTrack 과 같은 식이며 10개 트랙 모두 값이 같은지 대조했습니다.

새 6개는 `tools/generate_jetson_tracks.py` → 공유 `frontend/packages/ui/src/sim/jetson-tracks.json` → `tools/export_tracks.py` 순서입니다. 생성 파일은 직접 편집하지 않습니다. 갱신 없이 공유 geometry가 최신인지 확인하려면 `python tools/generate_jetson_tracks.py --check`를 사용합니다. 새 코스 추가가 위 2026-10-29 폐루프 결과의 검증 범위를 늘리지는 않습니다.

### 브로커 없이 정지 화면 미리 보기

카메라 의존성이 준비된 환경에서 simulator 디렉터리 기준으로 실행합니다.

```bash
python tools/preview_jetson.py --track jetson-demo --output ./jetson-preview
```

`top.png`, `start.png`, `parking.png`와 `world.json`, `preview.json`을 만듭니다. 탑뷰·출발 자세·오른쪽 주차칸을 정면에서 보는 별도 정지 자세를 렌더합니다. 출발점에서 주차 관측 자세까지 주행하거나 주차한 기록이 아닙니다. 다른 반경은 `--track jetson-r45` 등으로 선택합니다.

브로커 계정·ACL은 [브로커 구성](../../deploy/mosquitto/README.md).

## 테스트

```bash
python -m pytest        # 바퀴·엔코더·서보·자전거 모델, 트랙, 시리얼 프레임 규칙·TCP, verified 토픽, 와치독·비상정지,
                        # 카메라 → 인지 거리·부호, MJPEG HTTP, 인지 stream 입력, 폐루프 한 바퀴·장애물 정지
```

카메라·폐루프 시험은 OpenCV 가 없으면 건너뜁니다.

## 한계

- 기존 서킷 배치 검사(`NO_GAP` 등)는 mock의 기준 회피 주행으로 미리 돌려 보지만, 지금 자율 앱 자체는 장애물을 피하지 않고 섭니다. 새 Jetson 코스는 지면·차체 겹침 검사만 하며 그 회피 trial 결과도 없습니다.
- 인지 대역(색 기반)의 차선 정확도는 10개 트랙 한 바퀴에서 오프셋 오차 p95 0.035 m·방향 오차 p95 0.060 rad (S15P31E110-320,
  `../perception/README.md` 결과표). 곡률이 바뀌는 곳·머리핀에서 방향 오차가 0.3~0.75 rad 까지 튄다.
- 연석은 실제처럼 빨강·흰 줄이 아니라 파랑입니다 (색 기반 인지가 장애물·차선으로 착각하지 않게). 벽은 바닥 색으로만 그립니다.

- 평평한 지면, 미끄럼 없음, 조명·그림자·흔들림 없는 깨끗한 화면입니다. 실제 카메라보다 인지가 쉽습니다.
- ESP32 → 게이트웨이 상태 프레임(엔코더·적용 확인)은 형식이 없어(-241) 보내지 않습니다. `vehicle.status` 를 MQTT 로 직접 냅니다.
- `mqtt` 입력은 게이트웨이의 출력 허가를 모릅니다. 게이트웨이가 출력을 막아도 차가 움직일 수 있으니 시연은 `frames` 로 합니다.
