# xenova-simulator — 가상 RC카

실차(ESP32·모터·서보)가 없을 때 주행 앱을 시연·시험하기 위한 가상 RC카입니다 (S15P31E110-47).
[MQTT 메시지 계약 v1](../../../docs/contracts/mqtt-contract-v1.md) v1.1 기준으로 **게이트웨이 뒤의 ESP32와 차체**를
MQTT 수준에서 흉내 냅니다. 모드 전환(제어권 판정)은 하지 않습니다 — 그건 dom0 게이트웨이(S15P31E110-98)의 일이고,
그 전까지는 [`mock_gateway.py`](../manual/tools/mock_gateway.py)로 `vehicle/control`을 정합니다.

| 방향 | 토픽 | `type` | 비고 |
| --- | --- | --- | --- |
| 구독 | `xenova/v1/vehicle/control` | `vehicle.control` | retained. `generation`·`active_app`만 쓴다 |
| 구독 | `xenova/v1/apps/autonomous/events`, `.../manual/events` | `drive.command` | 활성 앱 것만 적용. 같은 토픽의 `app.heartbeat` 등은 무시 |
| 발행 | `xenova/v1/vehicle/status` | `vehicle.status` | 20 Hz, QoS 0, `source_id: dom0-gateway`. `{applied_generation, v_mps, steer_rad, flags{estop, watchdog, obstacle}, batt_v}` |
| 발행 | `xenova/v1/sim/pose` | `sim.pose` | MQTT-15. 20 Hz, QoS 0, `source_id: simulator`. `{x, y, yaw, v_mps, steer_rad, world_revision}` (트랙 고정 좌표) |
| 발행 | `xenova/v1/sim/world` | `sim.world` | MQTT-16. 시작할 때와 바뀔 때, QoS 1 retained. `{revision, track{shape, a_m, b_m, lane_width_m}, obstacles[{id, x, y, w}], estop}`. 정상 종료 시 retained 삭제 |
| 구독 | `xenova/v1/sim/command` | `sim.command` | MQTT-17. 백엔드(콘솔 REST-31~35)가 보내는 `obstacle.add`·`obstacle.move`·`obstacle.remove`·`estop`·`push` |

`sim/*` 는 [MQTT 계약](../../../docs/contracts/mqtt-contract-v1.md) 11장 "시뮬레이터 전용" (실차에는 없다). `vehicle.status` 는 `gateway` 계정, `sim/*` 는 `simulator` 계정으로 따로 접속한다 (클라이언트 둘, 계약 9장). 게이트웨이 뒤(TCP)로 옮기는 것은 S15P31E110-260.

## 트랙

차선 중심선이 타원(가로 반지름 3.0 m, 세로 1.5 m, 6 m × 3 m)이고 차선 폭 0.4 m 다 ([시뮬레이터 시연 시나리오](../../../docs/scenario-simulator.md) P4). 좌표 원점은 타원 중심, x 오른쪽·y 위쪽, `yaw` 는 +x 에서 반시계. 차는 (3.0, 0) 에서 반시계 방향(+y)을 보고 시작한다. 트랙을 따라 도는 것은 자율주행 앱 몫이다 (인지 대역 -268 이 이 값으로 `perception.result` 를 만든다).

## 동작

| 상황 | 차 | 플래그 |
| --- | --- | --- |
| `vehicle/control` 아직 없음 | 움직이지 않음 (ESP32 세션 전) | — |
| `active_app` 없음 (`SAFE_STOP`) | 정지 (게이트웨이가 정지 프레임을 보내는 것과 같음) | — |
| 활성 앱의 현재 세대 `drive.command` | 목표 속도·조향을 따라감 (가속 2 m/s², 조향 3 rad/s 제한, 자전거 모델) | — |
| 대기 앱 명령, 세대가 다른 명령 | 버림 | — |
| 받은 명령이 300 ms 없음 | 정지 | `watchdog` |
| 전방 0.5 m·±30° 안에 장애물 | 전진만 막음, 후진은 됨 (ESP32 근거리 감지 흉내) | `obstacle` |
| 비상정지 (`e`) | 정지 | `estop` |

- 위치(x, y, 방향)는 실차 계약에는 없는 값이라 시뮬레이터 전용 토픽 `sim.pose` 로만 낸다 (터미널 `s` 로도 볼 수 있다).
- 화면은 따로 없습니다. 백엔드가 `vehicle/status`를 받아 `/ws/v1/drive/state`로 넘기므로 모니터 주행 화면(`frontend/apps/vm-screens`)에 속도·조향·플래그가 그대로 나옵니다.

## 실행

Python 3.11 이상, paho-mqtt 1.6 또는 2.x.

```bash
cd platform/apps/simulator
export PYTHONPATH=.:../../services/vm-agent/mqtt-client      # Windows Git Bash 는 ';' 구분
python -m xenova_simulator --host 127.0.0.1 --port 1883 --password "$PW" --sim-password "$PW"   # 계정 gateway + simulator (계약 9장)
python -m xenova_simulator --port 1883 --username "" --sim-username ""                          # 무인증 개발 브로커
python -m xenova_simulator --no-sim-topics                                                       # sim/* 없이 (예전 동작)
```

실행 중 명령 (입력 후 Enter):

| 명령 | 하는 일 |
| --- | --- |
| `o [거리m]` | 진행 방향 앞에 장애물 (기본 0.8 m, id `t1`, `t2`, …) |
| `c` | 장애물 모두 치우기 |
| `e` | 비상정지 켜기/끄기 |
| `p [dy m]` | 차를 진행 방향 옆으로 밀기 (+ 왼쪽, 기본 0.3 m, 최대 ±0.5 m) |
| `s` | 위치·속도·세대·플래그·world revision 한 줄 |
| `r` | 트랙 시작 위치 (3.0, 0) 으로, 속도 0 |

터미널 명령도 `sim.world` 를 바꾼다 (revision +1). 콘솔 버튼(-267)이 생기면 같은 동작을 `sim.command` 로 한다.
| `q` | 종료 |

### 수동주행 전 구간 시연

```bash
python ../manual/tools/mock_gateway.py --generation 1 --mode MANUAL       # vehicle/control (retained)
python -m xenova_simulator                                               # 가상 RC카
# 수동 앱: Zephyr native_sim 판 또는 Python 참조 판 (../manual/README.md)
# 입력: 관리 콘솔 /demo 키 입력(백엔드 경유) 또는 python ../manual/tools/mock_input.py --keys up --seconds 2
```

브로커 계정·ACL은 [브로커 구성](../../deploy/mosquitto/README.md). `gateway`가 `vehicle/status`를 쓰는 권한은 계약 v1.1 에서 생겼습니다.

## 테스트

```bash
python -m pytest        # 운동 모델·수용 규칙·와치독·장애물·비상정지·20 Hz·봉투 형식
```
