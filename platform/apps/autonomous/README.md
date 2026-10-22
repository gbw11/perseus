# 자율주행 앱 — Zephyr

자율주행 VM(Zephyr, [RC카 시나리오](../../../docs/scenario-rc-car.md) §2)에서 도는 앱입니다 (S15P31E110-259).
젯슨(또는 PC 인지 대역 [`../perception`](../perception/README.md))의 `perception.result` 로 차선을 따라가고 장애물 앞에서 멈추는 `drive.command` 를 50 Hz 로 냅니다.
구조는 [수동주행 앱 Zephyr 판](../manual/zephyr/README.md)과 같고, MQTT 는 C 라이브러리 [`xenova-mqtt-c`](../common/xenova-mqtt-c/README.md)를 씁니다.

| 방향 | 토픽 | `type` | 비고 |
| --- | --- | --- | --- |
| 구독 | `xenova/v1/apps/perception/events` | `perception.result` | latest-only, 300 ms 지나면 정지 (시나리오 F6·D5) |
| 구독 | `xenova/v1/vehicle/control` | `vehicle.control` | retained, `generation`·`active_app` |
| 발행 | `xenova/v1/apps/autonomous/events` | `drive.command` | 20 ms 주기 (50 Hz), QoS 0, 대기 중에도 발행 |
| 발행 | `xenova/v1/apps/autonomous/events` | `app.heartbeat` | 1초, `state` = `idle`/`active`/`standby` |
| 발행 | `xenova/v1/apps/autonomous/events` | `perception.event` | 활성일 때 판단이 바뀌면 (아래 표). QoS 1 |

## 판단 규칙 (`zephyr/src/autonomous.c`)

| 상황 | `v_mps` | `steer_rad` |
| --- | --- | --- |
| 인지 결과 없음·300 ms 넘게 오래됨·형식 틀림 | 0 | 0 |
| `stop` = true, 또는 차 앞 통로 안 장애물이 0.8 m 이내 | 0 | 차선 추종 값 |
| 통로 안 장애물이 0.8 ~ 1.6 m | 아래 속도 × (거리 − 0.8) / 0.8 | 차선 추종 값 |
| 그 밖 | 0.5 × (1 − 0.4 × \|steer\| / 0.4) | 차선 추종 값 |

- 차선 추종: `steer = 1.0 × heading_err_rad − 1.5 × lane_offset_m`, ±0.4 rad 로 자른다. `lane_offset_m` + 는 차가 왼쪽에 있다는 뜻이라 오른쪽(−)으로, `heading_err_rad` + 는 차선이 왼쪽으로 돈다는 뜻이라 왼쪽(+)으로 꺾는다 (계약 5.4 부호).
- 차 앞 통로: `|y| ≤ 0.15 + w/2` 이고 `x > 0` 인 장애물. 0.15 m 와 0.8 m 는 인지 대역 `StopZone`(차폭 0.20 m, 여유 0.05 m, 거리 0.8 m)과 같다.
- 인지가 끊겨도 정지 명령을 계속 내므로 게이트웨이는 수동으로 넘기지 않는다 (D5). VM 이 죽거나 멈출 때만 명령이 끊긴다.
- 값은 실차 측정 전 제안값이다 (`auto_cfg_default`).

`perception.event` 의 `kind` (활성일 때만, 시작 직후 첫 인지는 알리지 않음):

| `kind` | `detail` | 언제 |
| --- | --- | --- |
| `obstacle` | `stop 80 cm` | 장애물로 정지 시작 |
| `obstacle_cleared` | `resume` | 장애물 정지에서 다시 출발 |
| `perception_lost` | `stop` | 인지 결과가 끊겨 정지 |
| `perception_restored` | `resume` | 인지가 돌아옴 |

## 상태 (2026-10-22)

| 확인 | 결과 |
| --- | --- |
| 로직 호스트 시험 (`zephyr/tests/`) | 24 통과 (차선 추종 부호·자르기, 통로·감속·정지, 인지 끊김·형식 오류, 세대·상태, 장애물 8개 봉투가 수신 버퍼 1280 B·토큰 한도 안에서 풀림, XenStore vm 경로) |
| C 라이브러리 (`xenova-mqtt-c`) | `perception.result` 디코드·`perception.event` 인코드 추가, 121 통과 |
| `native_sim` + Mosquitto + 인지 대역 `approach` | 13초 649건(49.9 Hz). 빈 길 0.45 ~ 0.49 m/s → 장애물 1.56 m 에서 감속 → 0.8 m 에서 정지(`obstacle`, `stop 80 cm`) → 치우면 재출발(`obstacle_cleared`) → 인지 종료 0.3 초 뒤 정지(`perception_lost`) |
| 게이트웨이(-98) + 자율·수동 `native_sim` | 자율 kill → 100 ms 에 `MANUAL`(첫 수동 전달 153 ms) → 재시작 3초 뒤 `AUTO_READY` → 콘솔 재개 → 3초 뒤 `AUTO` (세대 1 → 2 → 3 → 4) |
| `xenvm` 빌드 (Zephyr 4.4.0 `684c9e8`, SDK 1.0.0 aarch64) | `zephyr.bin` 188 KB, RAM 484 KB / 16 MB |
| **실보드** | 수동 앱과 같이 브로커 경로(vchan + dom0 중계, -239·-240) 뒤 |

## 실차 전환 시 바꿀 것

이 앱은 `perception.result` 를 읽고 `drive.command` 를 낼 뿐, 시뮬레이터를 모른다. 데모 기본(시뮬레이터 구성, [시뮬레이터 시연 시나리오](../../../docs/scenario-simulator.md))에서 실차 확장(§7-1 E1 ~ E3)으로 갈 때 **코드는 그대로 두고** 아래만 확인·조정한다.

| 단계 | 바뀌는 것 | 이 앱에서 할 일 |
| --- | --- | --- |
| E1 실제 ESP32·차체 | 차가 진짜로 움직인다 (인지는 대역 그대로) | `cruise_mps` 를 0.2 ~ 0.3 m/s 로 낮춰 시작. 흔들리면 `k_offset`·`k_heading` 을 줄인다 |
| E2 젯슨·D435f | 인지가 진짜 카메라 결과가 된다 | `stop_m`·`corridor_half_m` 을 젯슨 정지 영역과 맞춘다. 젯슨 실제 주기를 재서 `AUTO_PERCEPTION_TIMEOUT_MS`(300 ms) 가 4주기 이상인지 확인 |
| E3 실차 D1 ~ D9 | 전체 | 리허설로 위 값 확정, `auto_cfg_default` 주석의 "측정 전 값" 을 측정값으로 |

| 값 (`auto_cfg_default`) | 지금 (시뮬레이터 기준) | 실차에서 볼 것 |
| --- | --- | --- |
| `cruise_mps` | 0.5 | 트랙 폭·조명에서 안전한 속도. 전환 중 이동 거리 목표(≤ 7.5 cm @ 0.5 m/s) |
| `k_offset` / `k_heading` | 1.5 / 1.0 | 직선에서 좌우로 흔들리면 줄이고, 곡선에서 바깥으로 밀리면 늘린다 |
| `max_steer_rad` | 0.4 | 서보 실제 최대각 |
| `stop_m` / `slow_m` | 0.8 / 1.6 | 젯슨 `StopZone.distance_m` 과 같게, 감속 구간은 제동 거리 보고 |
| `corridor_half_m` | 0.15 | 실차 차폭/2 + 여유 |
| `AUTO_PERCEPTION_TIMEOUT_MS` | 300 | 젯슨 발행 주기 × 4 이상 |

- 값을 바꾸면 `zephyr/tests/test_autonomous.c` 의 기대값도 같이 고친다 (규칙 시험이라 값에 묶여 있다).
- 시뮬레이터에서 같은 값으로 D1 ~ D9 를 다시 돌려 기본 시연이 깨지지 않았는지 확인한다 — 두 구성이 같은 코드·같은 값을 쓴다.

## 빌드·실행

Zephyr 4.4.0 작업 공간이 있는 Linux(또는 Docker)에서, `zephyr/` 폴더 기준:

```bash
export ZEPHYR_BASE=/path/to/zephyr        # v4.4.0 (684c9e8f32e4373a21098559f748f06915f950c9)

# PC 시험: 로컬 Mosquitto(127.0.0.1:1883, 익명). 인지 대역을 같이 띄우면 주행 명령이 나온다
(cd ../../perception && python -m xenova_perception --source synthetic --scenario approach --username '') &
./run-native-sim.sh 12

# Xen 게스트 펌웨어
ZEPHYR_TOOLCHAIN_VARIANT=cross-compile CROSS_COMPILE=/path/aarch64-zephyr-elf/bin/aarch64-zephyr-elf- \
  cmake -S . -B build-xenvm -GNinja -DBOARD=xenvm && ninja -C build-xenvm

# 로직 시험 (Zephyr 없이 gcc)
cmake -S tests -B build-host && cmake --build build-host && ctest --test-dir build-host --output-on-failure
```

- native_sim 을 직접 띄울 때는 `zephyr.exe -seed-random` 을 붙인다. 기본 시드가 고정이라 매번 `boot_id` 가 같고, 그러면 재시작 뒤 게이트웨이가 새 `seq` 를 옛 실행의 중복으로 버려 `AUTO_READY` 가 이전 실행 길이만큼 늦어진다. xenvm 은 부팅 시각(사이클 카운터)을 섞는 타이머 난수원이라 재부팅마다 달라질 것으로 보지만, 실보드에서 확인해야 한다.
- 브로커 계정·비밀번호는 수동 앱과 같은 방식 (`CONFIG_XENOVA_AUTONOMOUS_BROKER_*`, 계정 `autonomous`).
