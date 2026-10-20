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
| `Kconfig` | 브로커 주소(기본 dom0 `192.168.77.1`)·계정(`manual`)·vm_id |
| `boards/native_sim_native_64.conf` | PC 시험: 호스트 소켓(NSOS)으로 `127.0.0.1` 브로커 |
| `boards/xenvm.conf` | Xen 게스트 펌웨어 |

## 상태 (2026-10-20)

| 확인 | 결과 |
| --- | --- |
| 로직 호스트 시험 (`tests/`) | 25 통과 (Python `test_keymap`·`test_app` 과 같은 표) |
| `native_sim` + Mosquitto | 4초 199건(50 Hz), heartbeat, retained 세대 5 → 50 ms 안 `active`, 키 입력 다음 명령(≤ 20 ms)에 반영, 입력 끊기면 정지. 낸 봉투 304건 모두 Python `envelope.validate` 통과, seq 역전 0 |
| `xenvm` 빌드 (Zephyr 4.4.0 `684c9e8`, SDK 1.0.0 aarch64) | `zephyr.bin` 171 KB, RAM 464 KB / 16 MB |
| **실보드** | ❌ Zephyr 템플릿에 PV 네트워크가 없어 아직 브로커에 닿지 못함 — 경로 결정 대기 ([요청](../../../../docs/requests/2026-10-20-zephyr-drive-network-platform.md)) |

아직 없는 것: vm_id를 XenStore에서 읽기(지금은 Kconfig 값, 설계 D5), 브로커 비밀번호 주입, 펌웨어 번들 게시·VM 자동 시작(S15P31E110-238).

## 빌드·실행

Zephyr 4.4.0 작업 공간이 있는 Linux(또는 Docker)에서:

```bash
export ZEPHYR_BASE=/path/to/zephyr        # v4.4.0 (684c9e8f32e4373a21098559f748f06915f950c9)

# PC 시험: 로컬 Mosquitto(127.0.0.1:1883)에 붙여 N초 실행하고 받은 메시지를 센다
./run-native-sim.sh 4

# Xen 게스트 펌웨어 (platform/guests/zephyr/README.md 와 같은 도구체인)
ZEPHYR_TOOLCHAIN_VARIANT=cross-compile CROSS_COMPILE=/path/aarch64-zephyr-elf/bin/aarch64-zephyr-elf- \
  cmake -S . -B build-xenvm -GNinja -DBOARD=xenvm && ninja -C build-xenvm

# 로직 시험 (Zephyr 없이 gcc)
cmake -S tests -B build-host && cmake --build build-host && ctest --test-dir build-host
```

native_sim 은 Zephyr 소켓을 호스트 소켓으로 넘기므로(`CONFIG_NET_NATIVE_OFFLOADED_SOCKETS`) 브로커는 실행하는 호스트에서 보여야 합니다.
