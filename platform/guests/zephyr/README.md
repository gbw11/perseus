# Zephyr Xen ARM64 펌웨어 템플릿

기본 펌웨어는 [Starter App 1.0.0](starter-app/README.md)이다. 고정 Zephyr 4.4.0·`xenvm` GICv2 프로필로 안내를 한 번 출력한 뒤 무기한 수면한다. 기존 `samples/synchronization` 예제와 성공 번들은 진단용 불변 원본으로 보존한다. Agent·SSH·협조형 종료 기능은 추가하지 않는다.

- 고정 출처: [Zephyr v4.4.0](https://github.com/zephyrproject-rtos/zephyr/releases/tag/v4.4.0), commit `684c9e8f32e4373a21098559f748f06915f950c9`. 도구체인은 Zephyr SDK 1.0.0 AArch64 GNU 14.3.0.
- 공식 릴리스 API가 제공한 SPDX SHA-256을 검증한 뒤 SPDX의 59,501개 원본 파일 SHA-1과 Git tag/commit을 검사한다. SDK는 공식 SHA-256과 비교한다. 고정값은 `source.lock.json`에 있다. 소스 tar 전체의 공식 SHA-256이 제공된 것으로 표현하지 않는다. GitHub 서버의 tag 서명 검증과 로컬 GPG 검증도 구분한다.
- `zephyr.bin`, `zephyr.elf`, `zephyr.config`, `zephyr.dts`, `xen.cfg.in`, `LICENSE`, `template.json`을 mode 0444로 게시한다. 빈 `root.raw`나 가짜 Linux initrd를 만들지 않는다.
- Xen 게스트 RAM 16MiB, 1 vCPU, GICv2, RAM base `0x40000000`. 카운터 주파수는 `CONFIG_TIMER_READS_ITS_FREQUENCY_AT_RUNTIME=y`로 실제 ARM timer register에서 읽는다. 기본 `.config`의 8.32MHz 숫자를 Pi의 실측값으로 간주하지 않는다.
- **미검증**: Raspberry Pi 4 실부팅, Xen이 만드는 GIC/메모리/IRQ/grant-table 배치와 정적 DTS의 일치. DTB dump와 Xen 로그로 확인하기 전에는 하드웨어 검증 완료로 표시하지 않는다.

## Linux 템플릿과 다른 계약

공식 [xenvm 문서](https://github.com/zephyrproject-rtos/zephyr/blob/v4.4.0/boards/xen/xenvm/doc/index.rst)에 따르면 PV 콘솔·event channel·grant table을 지원하며 XenBus/PV 네트워크·블록 드라이버는 지원하지 않는다. 이 번들은 콘솔용이며 DHCP·SSH·root/1234·앱 설치용 영구 루트 디스크를 제공하지 않는다. Linux dom0/Ubuntu/Debian의 인증·작업 보존 계약은 유지한다.

공통 SD에는 `/srv/zenova/rtos-templates/zephyr-4.4.0-xenvm/`으로 보관한다. Linux의 `/srv/zenova/templates/`와 인스턴스 레지스트리를 오인하게 하지 않는다. 조립 설정에 `rtos_templates`를 명시하면 조립기가 검증·용량 계산·복사·최종 입력 재검사를 수행한다. 공통 영구 레지스트리·생명주기·단일 자동 시작 서비스를 사용한다. 새 Starter 최초 설치의 `zephyr-dev`는 자동 시작 on·보드 종료 force-allowed이며 기존 설치/진단 seed의 정책은 보존한다.

Pi에서는 `zenova-vm create <name> --template-id zephyr-xenvm-starter-arm64 --template-version 1.0.0 --vcpus 1 --memory-mib 16 --idempotency-key <key> --execute --json`로 독립 사본을 만든다. 공통 start/status/console/autostart-set을 사용하고, stop/reboot는 게스트 협조형 종료가 없어 명시적 --force만 허용한다. 펌웨어 교체는 정지 상태의 firmware-replace 경로다. IP·root.raw·SSH 고유화는 요구하지 않는다. dom0에 보관한 파일은 게스트 앱의 쓰기 가능한 영구 저장소가 아니다.

현재 프로필은 정적 펌웨어 검사 결과이며 실보드 검증은 아니다. Zephyr IRQ flags 2는 LEVEL, 4는 EDGE다. Xen 표준 DT의 flags와 인코딩이 다르며, event channel의 edge 대 Xen 소스 level-low 차이는 실보드 DTB/드라이버 대조가 남아 있다. 원본 펌웨어를 임의 수정하지 않았다. [공통 CLI·번들 배포·실제 DTB 수집 절차](../../../docs/build-guides/zenova-persistent-platform.md#2026-10-17-공통-관리펌웨어-배포-계약)를 따른다.

## 진단용 synchronization 번들의 재현 가능한 준비

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
python3 -B scripts/prepare-zenova-zephyr.py \
  --source /absolute/work/source/zephyr --build-dir /absolute/work/build-xenvm \
  --spdx /absolute/work/downloads/zephyr-v4.4.0.spdx \
  --toolchain-archive /absolute/work/downloads/toolchain_gnu_linux-x86_64_aarch64-zephyr-elf.tar.xz \
  --output /absolute/new-templates/zephyr-4.4.0-xenvm --execute
```

새 Starter 최초 설치 이미지의 조립 항목(기존 진단 템플릿 선택도 명시적으로 지원한다):

```json
"rtos_templates": [
  {
    "template_dir": "/absolute/verified-templates/zenova-zephyr-starter-1.0.0",
    "template_manifest_sha256": "REPLACE_WITH_ACTUAL_TEMPLATE_JSON_SHA256"
  }
]
```

원본과 독립 seed 사본, 메타데이터의 바이트를 함께 예약한다. RTOS에 Linux의 8GiB root 디스크를 임의 배정하지 않는다. 생성한 RTOS VM 사본이나 다른 새 Linux VM은 카드의 운영 여유에서 공간을 사용하며 무제한 생성은 보장하지 않는다.

현재 dom0 완료 WIC는 이전 소스 기준이므로 source-byte gate에 맞는 후속 재빌드가 필요하다. Linux seed SSH 옵션과 전용 chroot 런타임 보완은 공유 소스에 반영했으며 회귀/실제 guest sshd 검사를 통과했다. 이를 포함한 새 dom0 재빌드가 필요하다. 이번 단계에서 dom0 재빌드·최종 조립·SD 기록은 실행하지 않는다.

Starter의 앱 출처·버전·소스 해시는 `starter.lock.json`과 새 `firmware.json`에 별도로 기록한다. 진단용 `source.lock.json`이나 이전 template.json을 재사용/수정하지 않는다. 펌웨어 교체는 자동 시작·UUID·보드 종료 정책을 바꾸지 않는다. 후속 운영과 실제 검증 한계는 [2026-10-18 수락 절차](../../../docs/build-guides/zenova-acceptance-20261018.md)를 따른다.

생성 결과의 vm_id는 영구 UUID다. 기본 Starter는 정규화 카탈로그 게시 후 생성한다.
기존 synchronization 번들의 출처는 보존하며 Starter ID로 바꾸어 게시하지 않는다.
