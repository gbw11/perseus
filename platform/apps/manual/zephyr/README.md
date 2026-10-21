# 수동주행 앱 — Zephyr 판

수동주행 VM(Zephyr, [RC카 시나리오](../../../../docs/scenario-rc-car.md) §2)에서 도는 실제 앱입니다.
동작은 Python 참조 구현([`../xenova_manual`](../README.md))과 같고, MQTT는 C 라이브러리 [`xenova-mqtt-c`](../../common/xenova-mqtt-c/README.md)를 씁니다.

| 방향 | 토픽 | `type` | 비고 |
| --- | --- | --- | --- |
| 구독 | `xenova/v1/apps/manual/input` | `drive.input` | latest-only, 200 ms 지나면 정지 |
| 구독 | `xenova/v1/vehicle/control` | `vehicle.control` | retained, `generation`·`active_app` |
| 발행 | `xenova/v1/apps/manual/events` | `drive.command` | 20 ms 주기 (50 Hz), QoS 0, 대기 중에도 발행 |
| 발행 | `xenova/v1/apps/manual/events` | `app.heartbeat` | 1초, `state` = `idle`/`active`/`standby` |

| 파일 | 내용 |
| --- | --- |
| `src/manual.c` | 키 변환·입력 끊김·세대·상태 (OS 무관, `tests/`에서 gcc로 시험) |
| `src/main.c` | 한 스레드가 클라이언트를 소유: 20 ms마다 계산·발행, 남는 시간에 수신·keepalive·재연결 |
| `Kconfig` | 브로커 주소(기본 dom0 `192.168.77.1`)·계정(`manual`)·비밀번호(기본 빈 값 = 익명)·vm_id 대체값 |
| vm_id | xenvm: XenStore 자기 도메인 `vm`(= `/vm/<uuid>`, xen.cfg 의 uuid = 플랫폼 vm_id)에서 읽고, 못 읽으면 Kconfig 값. native_sim: Kconfig 값 |
| `boards/native_sim_native_64.conf` | PC 시험: 호스트 소켓(NSOS)으로 `127.0.0.1` 브로커 |
| `boards/xenvm.conf` | Xen 게스트 펌웨어 |

## 상태 (2026-10-20)

| 확인 | 결과 |
| --- | --- |
| 로직 호스트 시험 (`tests/`) | 35 통과 (Python `test_keymap`·`test_app` 과 같은 표 + XenStore vm 경로 해석) |
| Python ↔ C 교차 (`tests/cross_check.py`) | 5/5: Python `vehicle.control`·`drive.input` → C 반영(세대·active/standby·키·끊김 정지), C `drive.command` 50.4 Hz → Python `XenovaClient` 봉투·seq 검사 통과 |
| `native_sim` + Mosquitto | 4초 199건(50 Hz), heartbeat, retained 세대 5 → 50 ms 안 `active`, 키 입력 다음 명령(≤ 20 ms)에 반영, 입력 끊기면 정지. 낸 봉투 304건 모두 Python `envelope.validate` 통과, seq 역전 0 |
| 계정·ACL 브로커 (2026-10-21) | 로컬 Mosquitto(`platform/deploy/mosquitto` 설정, dom0 와 같은 ACL)에 `manual` 계정으로 4초 199건·heartbeat 3. 틀린 비밀번호는 연결 거부(5) 후 재시도, 브로커에 들어간 메시지 0 |
| `xenvm` 빌드 (Zephyr 4.4.0 `684c9e8`, SDK 1.0.0 aarch64) | `zephyr.bin` 179 KB (XenStore 포함), RAM 476 KB / 16 MB |
| **실보드** | ❌ Zephyr 템플릿에 PV 네트워크가 없어 아직 브로커에 닿지 못함 — 경로 결정 대기 ([요청](../../../../docs/requests/2026-10-20-zephyr-drive-network-platform.md)) |

아직 없는 것: XenStore vm_id 읽기의 실보드 확인, 펌웨어 번들 게시·VM 자동 시작(S15P31E110-238).

## 빌드·실행

Zephyr 4.4.0 작업 공간이 있는 Linux(또는 Docker)에서:

```bash
export ZEPHYR_BASE=/path/to/zephyr        # v4.4.0 (684c9e8f32e4373a21098559f748f06915f950c9)

# PC 시험: 로컬 Mosquitto(127.0.0.1:1883, 익명)에 붙여 N초 실행하고 받은 메시지를 센다
./run-native-sim.sh 4

# 계정·ACL 이 있는 브로커 (platform/deploy/mosquitto/local): 같은 스크립트에 환경변수로 넘긴다
XENOVA_MQTT_HOST=192.168.65.254 XENOVA_MQTT_PASSWORD="$XENOVA_MQTT_DEV_PASSWORD" ./run-native-sim.sh 4

# Xen 게스트 펌웨어 (platform/guests/zephyr/README.md 와 같은 도구체인)
ZEPHYR_TOOLCHAIN_VARIANT=cross-compile CROSS_COMPILE=/path/aarch64-zephyr-elf/bin/aarch64-zephyr-elf- \
  cmake -S . -B build-xenvm -GNinja -DBOARD=xenvm && ninja -C build-xenvm

# Python XenovaClient ↔ Zephyr 앱 양방향 교차 확인 (zephyr.exe 를 먼저 띄운 상태, 같은 브로커)
python3 tests/cross_check.py ../../../services/vm-agent/mqtt-client

# 로직 시험 (Zephyr 없이 gcc)
cmake -S tests -B build-host && cmake --build build-host && ctest --test-dir build-host
```

### 브로커 계정 주입

비밀번호는 저장소에 쓰지 않고 **빌드할 때** Kconfig 로 넣습니다. 계정 이름은 `app_id`(`manual`), ACL 은 `xenova/v1/apps/manual/events` 쓰기·`xenova/v1/#` 읽기입니다.

```bash
cmake -S . -B build-xenvm -GNinja -DBOARD=xenvm   -DCONFIG_XENOVA_MANUAL_BROKER_PASSWORD="\"$XENOVA_MQTT_PASSWORD\""   # 문자열 Kconfig 라 따옴표를 같이 넘긴다
```

- `run-native-sim.sh` 는 `XENOVA_MQTT_HOST`·`_PORT`·`_USERNAME`·`_PASSWORD` 를 같은 방식으로 넘깁니다. `_HOST` 는 IPv4 주소만 됩니다 (Docker 안이면 `host.docker.internal` 의 주소).
- 비밀번호는 빌드 디렉터리의 `.config` 와 `zephyr.bin` 에 평문으로 들어갑니다. 빌드 디렉터리·펌웨어를 저장소에 올리지 않습니다. 실보드 펌웨어 번들은 dom0 브로커 계정을 받을 때 만듭니다 (-238).
- 비밀번호가 비어 있으면 익명으로 붙습니다. 계정 브로커는 이것을 거부(CONNACK 5)하고 앱은 재연결을 반복합니다.

native_sim 은 Zephyr 소켓을 호스트 소켓으로 넘기므로(`CONFIG_NET_NATIVE_OFFLOADED_SOCKETS`) 브로커는 실행하는 호스트에서 보여야 합니다.
