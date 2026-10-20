# xenova-manual — 수동주행 앱 (Python 참조 구현)

> **배포하지 않는 참조 구현입니다.** 수동주행 VM은 Zephyr로 정해졌고([RC카 시나리오](../../../docs/scenario-rc-car.md) §2),
> 실제 앱은 Zephyr C 판으로 만듭니다 (C MQTT 라이브러리 S15P31E110-236 위에서). 이 Python 판은
> 동작 기준(키 변환·200 ms 정지·세대·대기 중 발행·heartbeat)과 Zephyr 판의 교차 시험 상대로 남기고,
> `tools/`의 게이트웨이·입력 목과 브라우저 테스트 화면은 Zephyr 판 시험에도 그대로 씁니다.

콘솔 키 입력(`drive.input`)을 주행 명령(`drive.command`)으로 바꿔 50 Hz로 발행합니다.
규칙은 [MQTT 메시지 계약 v1](../../../docs/contracts/mqtt-contract-v1.md) 5·7장을 따릅니다.

| 방향 | 토픽 | `type` | 비고 |
| --- | --- | --- | --- |
| 구독 | `xenova/v1/apps/manual/input` | `drive.input` | 백엔드가 키를 누르는 동안 20 Hz로 보냄 |
| 구독 | `xenova/v1/vehicle/control` | `vehicle.control` | 게이트웨이, retained. `generation`만 쓴다 |
| 발행 | `xenova/v1/apps/manual/events` | `drive.command` | 50 Hz, QoS 0. `{generation, v_mps, steer_rad}` |
| 발행 | `xenova/v1/apps/manual/events` | `app.heartbeat` | 1초, QoS 0. `{state, uptime_s}` — 앱 프로세스 생존·상태 (계약 6.1) |

- 활성이든 대기든 계속 발행합니다. 게이트웨이가 대기 앱 명령의 신선도로 승격을 정합니다 (계약 5.2).
- 입력이 200 ms 동안 없으면 `v_mps = 0`을 보냅니다. 최종 정지는 ESP32 와치독(300 ms)이 맡습니다.
- `vehicle/control`을 아직 못 받았으면 `generation 0`으로 보냅니다. ESP32는 세대가 다르면 버리므로 차는 움직이지 않습니다.
- `app.heartbeat`의 `state`는 `vehicle/control` 기준 `active`(활성 앱이 manual) / `standby` / `idle`(아직 못 받음)입니다. 제어권 전환 판정에는 쓰지 않습니다.
- 봉투·QoS·재연결은 [xenova_mqtt](../../services/vm-agent/mqtt-client/README.md)가 맡습니다.

## 실행

Python 3.11 이상, paho-mqtt 1.6 또는 2.x.

```bash
export PYTHONPATH=/opt/xenova/manual:/opt/xenova/mqtt-client   # 개발 PC: platform/apps/manual 과 mqtt-client 경로
export XENOVA_MANUAL_VM_ID=<vm_id>            # 없으면 /etc/xenova/instance-id
export XENOVA_MANUAL_BROKER_PASSWORD=<비밀번호>
python3 -m xenova_manual
```

| 환경변수 | 기본값 | 설명 |
| --- | --- | --- |
| `XENOVA_MANUAL_VM_ID` | `/etc/xenova/instance-id` | 봉투의 `vm_id` |
| `XENOVA_MANUAL_BROKER_HOST` / `_PORT` | `192.168.77.1` / `1883` | dom0 Mosquitto |
| `XENOVA_MANUAL_BROKER_USERNAME` / `_PASSWORD` | `manual` / 없음 | 브로커 ACL은 username = app_id |
| `XENOVA_MANUAL_MAX_SPEED_MPS` | `1.0` | `up` 속도 |
| `XENOVA_MANUAL_MAX_REVERSE_MPS` | `0.5` | `down` 속도 |
| `XENOVA_MANUAL_MAX_STEER_RAD` | `0.4` | `left`·`right` 조향각 |
| `XENOVA_MANUAL_INPUT_TIMEOUT_MS` | `200` | 입력 끊김 판정 |
| `XENOVA_MANUAL_LOG_LEVEL` | `INFO` | |

발행 주기 50 Hz는 계약값이라 환경변수로 바꾸지 않습니다.

## 게이트웨이·백엔드 없이 시험

```bash
docker run --rm -d --name xenova-mq-test -p 18830:1883 eclipse-mosquitto:2.0.21 mosquitto -c /mosquitto-no-auth.conf
XENOVA_MANUAL_VM_ID=22222222-2222-4222-8222-222222222222 XENOVA_MANUAL_BROKER_HOST=127.0.0.1 \
  XENOVA_MANUAL_BROKER_PORT=18830 python -m xenova_manual &

python tools/mock_gateway.py --port 18830 --generation 7 --mode MANUAL   # vehicle.control retained
python tools/mock_input.py --port 18830 --keys up,left --seconds 1      # drive.input 20 Hz
docker exec xenova-mq-test mosquitto_sub -t xenova/v1/apps/manual/events -v
```

Git Bash에서는 `docker run`·`docker exec` 앞에 `MSYS_NO_PATHCONV=1`을 붙입니다 (`/mosquitto-no-auth.conf` 경로가 바뀌는 것을 막음).

## 테스트 화면 (브라우저)

`tools/web/index.html`은 브라우저가 MQTT over WebSocket으로 브로커에 붙어 백엔드(`drive.input`)·게이트웨이(`vehicle.control`)를
흉내 내고 앱의 `drive.command`(값·수신 주기·세대 불일치·최근 10초 그래프)를 보여 주는 개발용 화면입니다.

```bash
# WebSocket 리스너가 있는 익명 개발 브로커 (1883·9001 이 이미 쓰이면 앞쪽 포트를 바꾼다)
MSYS_NO_PATHCONV=1 docker run --rm -d --name xenova-mq-ui -p 11883:1883 -p 19001:9001 \
  -v "$PWD/tools/web/mosquitto-dev.conf:/mosquitto/config/mosquitto.conf:ro" eclipse-mosquitto:2.0.21
XENOVA_MANUAL_VM_ID=22222222-2222-4222-8222-222222222222 XENOVA_MANUAL_BROKER_HOST=127.0.0.1 \
  XENOVA_MANUAL_BROKER_PORT=11883 python -m xenova_manual &
python -m http.server 8765 --bind 127.0.0.1 --directory tools/web
# 브라우저: http://localhost:8765/?broker=ws://localhost:19001
```

방향키·WASD·Space(브레이크) 또는 화면 버튼으로 입력하고, 게이트웨이 목 버튼으로 모드와 세대를 바꿉니다.
MQTT 라이브러리는 unpkg CDN(`mqtt@5.10.1`)에서 받으므로 인터넷 연결이 필요합니다.

## 테스트

```bash
python -m pytest                                   # 단위 테스트 (브로커 테스트는 건너뜀)
XENOVA_MQTT_TEST_BROKER=127.0.0.1:18830 XENOVA_MQTT_TEST_CONTAINER=xenova-mq-test python -m pytest
```

브로커 테스트는 50 Hz 발행, 세대 반영, 키 입력·해제 후 정지(≤ 270 ms), 브로커 재시작 후 재개를 확인합니다.

## 다른 파트에 요청한 것

Zephyr 주행 VM의 MQTT 경로(박신영·주소정), 게이트웨이 판정 신호(제다빈), CI 단계(정수환)는 [docs/requests](../../../docs/requests/README.md)에 정리했습니다.
