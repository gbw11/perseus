# Zephyr Xen ARM64 펌웨어 템플릿

Alpine 확장 지점을 Zephyr 4.4.0으로 교체한다. 공식 `xenvm` GICv2 보드의 `samples/synchronization` 예제를 빌드한다. 이는 두 스레드의 semaphore 동작을 출력하는 원본 예제이며 Agent나 heartbeat를 구현하지 않는다.

- 고정 출처: [Zephyr v4.4.0](https://github.com/zephyrproject-rtos/zephyr/releases/tag/v4.4.0), commit `684c9e8f32e4373a21098559f748f06915f950c9`. 도구체인은 Zephyr SDK 1.0.0 AArch64 GNU 14.3.0.
- 공식 릴리스 API가 제공한 SPDX SHA-256을 검증한 뒤 SPDX의 59,501개 원본 파일 SHA-1과 Git tag/commit을 검사한다. SDK는 공식 SHA-256과 비교한다. 고정값은 `source.lock.json`에 있다. 소스 tar 전체의 공식 SHA-256이 제공된 것으로 표현하지 않는다. GitHub 서버의 tag 서명 검증과 로컬 GPG 검증도 구분한다.
- `zephyr.bin`, `zephyr.elf`, `zephyr.config`, `zephyr.dts`, `xen.cfg.in`, `LICENSE`, `template.json`을 mode 0444로 게시한다. 빈 `root.raw`나 가짜 Linux initrd를 만들지 않는다.
- Xen 게스트 RAM 16MiB, 1 vCPU, GICv2, RAM base `0x40000000`. 카운터 주파수는 `CONFIG_TIMER_READS_ITS_FREQUENCY_AT_RUNTIME=y`로 실제 ARM timer register에서 읽는다. 기본 `.config`의 8.32MHz 숫자를 Pi의 실측값으로 간주하지 않는다.
- **미검증**: Raspberry Pi 4 실부팅, Xen이 만드는 GIC/메모리/IRQ/grant-table 배치와 정적 DTS의 일치. DTB dump와 Xen 로그로 확인하기 전에는 하드웨어 검증 완료로 표시하지 않는다.

## Linux 템플릿과 다른 계약

공식 [xenvm 문서](https://github.com/zephyrproject-rtos/zephyr/blob/v4.4.0/boards/xen/xenvm/doc/index.rst)에 따르면 PV 콘솔·event channel·grant table을 지원하며 XenBus/PV 네트워크·블록 드라이버는 지원하지 않는다. 이 번들은 콘솔용이며 DHCP·SSH·root/1234·앱 설치용 영구 루트 디스크를 제공하지 않는다. Linux dom0/Ubuntu/Debian의 인증·작업 보존 계약은 유지한다.

공통 SD에는 `/srv/perseus/rtos-templates/zephyr-4.4.0-xenvm/`으로 보관한다. Linux의 `/srv/perseus/templates/`와 인스턴스 레지스트리를 오인하게 하지 않는다. 조립 설정에 `rtos_templates`를 명시하면 조립기가 검증·용량 계산·복사·최종 입력 재검사를 수행한다. 현재 기본 자동 시작 인스턴스는 `ubuntu-dev`, `debian-dev` 두 개다. RTOS 자동 시작·Linux `perseus-vm` 생명주기 통합은 이번 구현에 포함하지 않는다.

Pi에는 새 RTOS VM을 만드는 데 필요한 완성 펌웨어와 설정 원본이 모두 남는다. 개발 PC의 원본이 없어도 새 전용 디렉터리에 펌웨어·설정을 복사해 이름·UUID·경로를 채운 후 수동 `xl create`가 가능한 형태다. 이는 디스크 없는 별도 수동 Xen 도메인이며 Linux 등록 VM으로 표시하지 않는다. 새 앱이 필요하면 Zephyr를 다시 빌드해야 한다. Linux식 패키지 설치·강제 제거 없는 정상 종료 경로·네트워크가 준비됐다고 주장하지 않는다.

## 재현 가능한 준비

소스와 호스트 도구는 저장소 밖의 새 전용 작업 경로에 준비한다. 기존 source/build/output은 덮어쓰지 않는다. Python 3.12+, CMake 3.20.5+, Ninja, Zephyr Python 빌드 도구와 AArch64 toolchain이 필요하다. 이번 사용 버전·다운로드 digest·실행 명령은 실행 보고서에 보존한다. 시스템 패키지 설치나 전역 SDK 등록은 하지 않는다.

```bash
# 별도 workspace에서 고정 tag를 받아 source.lock.json/SPDX로 검증한다.
# west init은 아직 workspace가 없는 새 경로에서 한 번만 실행한다.
west init -l /absolute/work/source/zephyr
export ZEPHYR_BASE=/absolute/work/source/zephyr
export ZEPHYR_TOOLCHAIN_VARIANT=cross-compile
export CROSS_COMPILE=/absolute/work/host-tools/aarch64-zephyr-elf/bin/aarch64-zephyr-elf-
export XDG_CACHE_HOME=/absolute/work/host-tools/cache
cmake -S "$ZEPHYR_BASE/samples/synchronization" \
  -B /absolute/work/build-xenvm -GNinja -DBOARD=xenvm \
  -DPython3_EXECUTABLE=/usr/bin/python3 -DWEST=/absolute/private-tools/bin/west
cmake --build /absolute/work/build-xenvm -j 4

# 기본은 읽기 전용 검사/계획이다. --execute가 새 번들 게시만 허용한다.
python3 -B scripts/prepare-perseus-zephyr.py \
  --source /absolute/work/source/zephyr --build-dir /absolute/work/build-xenvm \
  --spdx /absolute/work/downloads/zephyr-v4.4.0.spdx \
  --toolchain-archive /absolute/work/downloads/toolchain_gnu_linux-x86_64_aarch64-zephyr-elf.tar.xz \
  --output /absolute/new-templates/zephyr-4.4.0-xenvm --execute
```

조립 설정의 선택적 추가 항목:

```json
"rtos_templates": [
  {
    "template_dir": "/absolute/verified-templates/zephyr-4.4.0-xenvm",
    "template_manifest_sha256": "REPLACE_WITH_ACTUAL_TEMPLATE_JSON_SHA256"
  }
]
```

각 파일의 실제 바이트를 추가로 예약한다. RTOS에 Linux의 8GiB root 디스크를 임의 배정하지 않는다. 생성한 RTOS VM 사본이나 다른 새 Linux VM은 카드의 운영 여유에서 공간을 사용하며 무제한 생성은 보장하지 않는다.

현재 dom0 완료 WIC는 이전 소스 기준이므로 source-byte gate에 맞는 후속 재빌드가 필요하다. Linux seed SSH 옵션과 전용 chroot 런타임 보완은 공유 소스에 반영했으며 회귀/실제 guest sshd 검사를 통과했다. 이를 포함한 새 dom0 재빌드가 필요하다. 이번 단계에서 dom0 재빌드·최종 조립·SD 기록은 실행하지 않는다.
