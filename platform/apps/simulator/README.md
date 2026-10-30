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

- 위치(x, y, 방향)는 계약에 없는 값이라 발행하지 않고 터미널 `s` 명령으로만 봅니다.
- 화면은 따로 없습니다. 백엔드가 `vehicle/status`를 받아 `/ws/v1/drive/state`로 넘기므로 모니터 주행 화면(`frontend/apps/vm-screens`)에 속도·조향·플래그가 그대로 나옵니다.

## 실행

Python 3.11 이상, paho-mqtt 1.6 또는 2.x.

```bash
cd platform/apps/simulator
export PYTHONPATH=.:../../services/vm-agent/mqtt-client      # Windows Git Bash 는 ';' 구분
python -m xenova_simulator --host 127.0.0.1 --port 1883      # 브로커 계정 gateway (계약 9장)
python -m xenova_simulator --port 1883 --username ""         # 무인증 개발 브로커
```

실행 중 명령 (입력 후 Enter):

| 명령 | 하는 일 |
| --- | --- |
| `o [거리m]` | 진행 방향 앞에 장애물 (기본 0.8 m) |
| `c` | 장애물 모두 치우기 |
| `e` | 비상정지 켜기/끄기 |
| `s` | 위치·속도·세대·플래그 한 줄 |
| `r` | 위치·속도 초기화 |
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
