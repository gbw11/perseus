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
| `make-bundle.sh` | xenvm 빌드 → dom0 에 가져갈 펌웨어 번들 (`dist/`) |

## 상태 (2026-10-20)

| 확인 | 결과 |
| --- | --- |
| 로직 호스트 시험 (`tests/`) | 35 통과 (Python `test_keymap`·`test_app` 과 같은 표 + XenStore vm 경로 해석) |
| Python ↔ C 교차 (`tests/cross_check.py`) | 5/5: Python `vehicle.control`·`drive.input` → C 반영(세대·active/standby·키·끊김 정지), C `drive.command` 50.4 Hz → Python `XenovaClient` 봉투·seq 검사 통과 |
| `native_sim` + Mosquitto | 4초 199건(50 Hz), heartbeat, retained 세대 5 → 50 ms 안 `active`, 키 입력 다음 명령(≤ 20 ms)에 반영, 입력 끊기면 정지. 낸 봉투 304건 모두 Python `envelope.validate` 통과, seq 역전 0 |
| 계정·ACL 브로커 (2026-10-21) | 로컬 Mosquitto(`platform/deploy/mosquitto` 설정, dom0 와 같은 ACL)에 `manual` 계정으로 4초 199건·heartbeat 3. 틀린 비밀번호는 연결 거부(5) 후 재시도, 브로커에 들어간 메시지 0 |
| `xenvm` 빌드 (Zephyr 4.4.0 `684c9e8`, SDK 1.0.0 aarch64) | `zephyr.bin` 179 KB (XenStore 포함), RAM 476 KB / 16 MB |
| 펌웨어 번들 (2026-10-21, 컨테이너) | `make-bundle.sh` → `zephyr.bin`·`xenova-manual.cfg`·`manifest.json`·`firmware/`. manifest sha256 10건 일치, `firmware/` 는 플랫폼 검사(`xenova_zephyr.bundle_info`·`payload_ok`) 통과. 비밀번호는 `zephyr.bin`·`zephyr.elf` 에만 있고 manifest·cfg·`zephyr.config` 에는 없음 |
| **실보드** | ❌ Zephyr 템플릿에 PV 네트워크가 없어 아직 브로커에 닿지 못함 — 경로 결정 대기 ([요청](../../../../docs/requests/2026-10-20-zephyr-drive-network-platform.md)) |

아직 없는 것 (S15P31E110-238 은 펌웨어 번들까지로 닫고, 아래는 vchan 작업 -239·-240 뒤 별도 이슈):

- 브로커 경로: xenvm 에는 PV 네트워크가 없어 지금 번들은 실보드에서 브로커에 닿지 못한다. 계약 v1 §4.1 대로 vchan + dom0 중계로 붙인다 (-239·-240).
- VM 자동 시작 (xl 자동 기동·`autostart-set`), 실보드 domU 부팅·XenStore vm_id 읽기 확인. 실보드가 생기면 한다.

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
- 비밀번호는 빌드 디렉터리의 `.config` 와 `zephyr.bin` 에 평문으로 들어갑니다. 빌드 디렉터리·펌웨어를 저장소에 올리지 않습니다. 실보드 펌웨어 번들은 아래 [펌웨어 번들](#펌웨어-번들)로 만듭니다.
- 비밀번호가 비어 있으면 익명으로 붙습니다. 계정 브로커는 이것을 거부(CONNACK 5)하고 앱은 재연결을 반복합니다.

native_sim 은 Zephyr 소켓을 호스트 소켓으로 넘기므로(`CONFIG_NET_NATIVE_OFFLOADED_SOCKETS`) 브로커는 실행하는 호스트에서 보여야 합니다.

## 펌웨어 번들

`make-bundle.sh` 가 xenvm 으로 빌드하고 dom0 에 가져갈 번들을 만듭니다. 계정 환경변수는 `run-native-sim.sh` 와 같고, 기본 브로커는 dom0 `192.168.77.1:1883`·`manual` 입니다.

```bash
export ZEPHYR_BASE=/path/to/zephyr CROSS_COMPILE=/path/aarch64-zephyr-elf/bin/aarch64-zephyr-elf-
XENOVA_MQTT_PASSWORD="$XENOVA_MQTT_MANUAL_PASSWORD" ./make-bundle.sh            # → dist/xenova-manual-<버전>/ 와 .tar.gz
# 컨테이너 안 worktree 처럼 git 을 못 읽으면 XENOVA_GIT_COMMIT=$(git rev-parse HEAD) 를 같이 넘긴다
```

```
dist/xenova-manual-0.1.0+g<커밋>/
  zephyr.bin           Xen arm64 게스트 커널 (손으로 xl create 할 때)
  xenova-manual.cfg    xl 설정 예시: 1 vCPU·16 MiB·GICv2 (platform/guests/zephyr/xen.cfg.in 과 같은 값), vif/disk 없음
  manifest.json        앱·버전·git 커밋, Zephyr 버전·커밋·보드·도구체인, 빌드 시각, 파일별 sha256, 브로커 host/port/username, password_embedded
  firmware/            플랫폼 팀 앱 번들 그대로 (platform/scripts/package-xenova-firmware.py 출력: firmware.json + zephyr.bin/elf/config/dts/version.h, xen.cfg.in, LICENSE)
dist/xenova-manual-0.1.0+g<커밋>.tar.gz
```

- `firmware/` 는 [Starter 앱의 불변 번들](../../../guests/zephyr/starter-app/README.md)과 같은 형식이라 플랫폼 CLI 가 그대로 받습니다. 파일을 더 넣으면 `xenova_zephyr.bundle_info` 가 거부하므로 manifest·cfg 는 바깥에 둡니다.
- 비밀번호는 `manifest.json`·`xenova-manual.cfg` 에 넣지 않고 `password_embedded` 만 적습니다. `firmware/zephyr.config` 의 비밀번호 줄은 `"(redacted)"` 로 가립니다. **`zephyr.bin`·`zephyr.elf` 에는 평문으로 들어가므로** 번들은 저장소·공유 폴더에 올리지 않습니다 (`dist/` 는 `.gitignore`).
- 게시: tar.gz 를 dom0 로 복사해 풉니다. 플랫폼 경로는 Zephyr 템플릿으로 VM 을 만들고 펌웨어만 바꿉니다. `--sha256` 은 스크립트가 마지막에 찍는 `firmware/firmware.json` 해시(manifest 의 `deploy.firmware_replace.sha256` 과 같다)입니다.

```bash
xenova-vm create manual-drive --template-id zephyr-xenvm-starter-arm64 --template-version 1.0.0   --vcpus 1 --memory-mib 16 --idempotency-key manual-drive-1 --execute --json
xenova-vm firmware-replace manual-drive --bundle /srv/xenova/apps/<번들>/firmware --sha256 <해시>            # 계획만
xenova-vm firmware-replace manual-drive --bundle /srv/xenova/apps/<번들>/firmware --sha256 <해시> --execute
xenova-vm start manual-drive && xenova-vm console manual-drive
```

- 손으로 띄울 때는 `xenova-manual.cfg` 의 `kernel` 경로(기본 `/srv/xenova/apps/<번들>/zephyr.bin`, `XENOVA_BUNDLE_DEST` 로 바꿈)를 확인하고 `xl create -c xenova-manual.cfg`. 자동 시작은 넣지 않았습니다.
- 확인: `cd dist/<번들> && python3 -c 'import json,hashlib;m=json.load(open("manifest.json"));print([k for k,v in m["files"].items() if hashlib.sha256(open(k,"rb").read()).hexdigest()!=v["sha256"]])'` 가 `[]`.
- 한계: 실보드 부팅은 확인하지 않았습니다 (`hardware_boot_tested: false`). xenvm 에 PV 네트워크가 없어 vchan 중계(-239·-240) 전에는 콘솔 출력까지만 기대할 수 있습니다. vchan 이 붙으면 계정은 중계가 쓰므로(계약 §4.1) 펌웨어의 비밀번호 주입은 빠질 수 있습니다.
