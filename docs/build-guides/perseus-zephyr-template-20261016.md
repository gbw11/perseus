# Zephyr RTOS 템플릿 추가와 64GB 재계산 — 2026-10-16

## 완료 범위와 기준

사용자 선택에 따라 Alpine 게스트 확장 지점을 Zephyr로 교체했다. 공식 소스 다운로드·체크섬 검증·Xen ARM64 펌웨어 빌드·새 불변 템플릿 게시·세 템플릿을 반영한 조립 계획까지 수행했다. 최종 SD 이미지 조립, 실제 VM/seed 생성·실행, SD 마운트/기록/포맷/파티션 변경, Yocto 재빌드, git add/commit/push는 수행하지 않았다.

기준 HEAD는 `9f685e9fd1d6cce65aea5ae8a655fb2b7a357492`, 브랜치는 `feat/platform/yocto-build-setup`이다. 이전 미커밋 수정은 `scripts/prepare-perseus-guests.py`, `docs/build-guides/perseus-first-install.md`, 신규 boot-partition 테스트와 Ubuntu/Debian 생성 보고서였다. 시작 상태와 diff를 보존했다. 이전 준비 도구 diff·신규 테스트·보고서의 내용이 그대로 유지됨을 검사했고, 공통 가이드에는 RTOS 정책만 추가했다.

## 버전과 호환성 근거

- Zephyr **4.4.0**, 공식 `v4.4.0` tag object `4f50f0ba8905f27b2f60123d0ee0934fda6fe134`, commit `684c9e8f32e4373a21098559f748f06915f950c9`.
- [공식 릴리스](https://github.com/zephyrproject-rtos/zephyr/releases/tag/v4.4.0)와 [릴리스 지원 정책](https://docs.zephyrproject.org/latest/releases/index.html)의 stable 버전을 선택했다. Zephyr SDK **1.0.0 / GNU AArch64 14.3.0**, 공식 `xenvm` GICv2 보드와 `samples/synchronization`을 사용했다. 최신/개발 브랜치로 입력을 선택하지 않았다.
- [고정 버전 xenvm 문서](https://github.com/zephyrproject-rtos/zephyr/blob/v4.4.0/boards/xen/xenvm/doc/index.rst)는 ARMv8 Xen 게스트의 PV 콘솔·event channel·grant table 지원과 정적 DTS의 하드웨어별 조정 필요성을 명시한다. XenBus/PV 네트워크·블록은 미지원이다.
- 빌드된 `.config`는 ARM64, Xen, GICv2, PV HVC 콘솔, timer frequency runtime read, RAM `0x40000000`/16MiB를 갖는다. 실행 설정은 1 vCPU·16MiB이며 기본 자동 시작 대상으로 등록하지 않는다. Pi 4의 실제 Xen DTB·IRQ·grant-table 배치와 일치하는지는 **아직 실부팅 미검증**이다.

현재 성공 산출물의 package manifest에서 `xen-tools-xl`은 `4.21+stable0+1c72306b1f`다. 이 리비전의 공식 Xen `arch-arm.h`/`libxl_arm.c`와 Zephyr의 정적 설정을 추가 대조했다. GIC distributor `0x03001000`, CPU interface `0x03002000`, grant table base `0x38000000`, RAM base `0x40000000`, virtual timer IRQ 27, event-channel IRQ 31이 대응한다. `platform-xen-packages.txt`, `xen-arch-arm.h`, `xen-libxl_arm.c`를 보존했다. 이는 주소·IRQ 번호의 소스 대조이며, Xen DTB와 Zephyr의 interrupt cell encoding/trigger flags까지 실제 보드에서 일치한다고 검증한 결과는 아니다. 실보드 DTB dump·부팅이 남아 있다.

## 원본 검증과 실제 빌드

공식 Git 원본을 별도 경로로 clone하고 `git fsck --full --strict`가 성공했다. GitHub API가 해당 tag의 서명 검증을 `verified=true`로 보고한 응답을 저장했다. 로컬 GPG 서명을 검증했다고 주장하지 않는다.

공식 릴리스 SPDX의 SHA-256은 `1307753cf6fcb3d9a07d3eac0dd029b9411c8217f84216f57263a0ffd676f1d4`다. 다운로드를 이 값과 비교한 뒤 공식 SPDX가 제공한 **59,501개 원본 파일의 SHA-1**을 전부 대조했다. 누락 0, 불일치 0. source tar 전체에 대한 공식 SHA-256이 제공된 것으로 표현하지 않는다.

도구체인 archive SHA-256은 `4015c637d21348db36bb31e849a00bcbccc02304feef519b897d0b83ce759760`이며 공식 릴리스 API digest와 `sha256.sum` 모두와 일치했다. 소스/도구체인/문서/응답은 `/home/ssafy/perseus-zephyr-template-20261016-153845/`에 보존했다. 호스트 Python·CMake·Ninja·West 등은 전용 `host-tools/` 경로에만 설치/추출했다. 시스템 dpkg 상태는 유지했고 기존 Yocto Layer/캐시는 수정하지 않았다. PyPI resolved versions와 download hashes는 `host-python-install.json`에 있다.

최초 standalone 구성은 모듈 생성 파일 누락으로 실패했다. 해당 로그를 보존하고, 외부 모듈을 다운로드하지 않는 전용 local West workspace를 생성한 뒤 구성·컴파일이 각각 exit 0으로 완료됐다. upstream 소스를 수정하거나 빈 생성 파일을 끼워 넣지 않았다. Dtc 미발견 경고는 있었으며 실제 Zephyr Python devicetree 처리와 빌드는 성공했다. 빌드 결과의 RAM 사용 표시는 308KiB/16MiB이며 하드웨어 실측은 아니다.

## 실제 새 템플릿

경로: `/home/ssafy/perseus-zephyr-template-20261016-153845/templates/zephyr-4.4.0-xenvm/`

| 파일 | bytes |
| --- | ---: |
| `zephyr.bin` | 57,632 |
| `zephyr.elf` | 1,278,776 |
| `zephyr.config` | 30,907 |
| `zephyr.dts` | 4,304 |

`xen.cfg.in`, `LICENSE`, `template.json`도 포함하며 총 7개 파일은 모두 mode 0444다. 총 논리 크기 **1,385,296 bytes**, 실제 디렉터리 점유량 **1,409,024 bytes (약 1.34MiB)**. metadata SHA-256과 각 파일 digest는 `zephyr-template-verified.json`, `TEMPLATE-SHA256SUMS`에 있다. ARM64 Image 헤더·ELF machine/entry 범위·필수 빌드 옵션·출처·모든 payload hash·권한 검사가 통과했다. placeholder root 디스크나 initrd는 만들지 않았다.

### Linux와 RTOS의 차이

세 템플릿은 Ubuntu·Debian의 Linux rootfs 두 개와 Zephyr의 콘솔 펌웨어 한 개다. Zephyr는 `root/1234` SSH·DHCP·PV 블록 디스크·Linux 패키지 설치 환경이 아니다. 기본 자동 시작은 `ubuntu-dev`, `debian-dev` 두 개를 유지한다. 새 RTOS 앱은 펌웨어 재빌드가 필요하며 쓰기 가능한 RTOS 데이터 디스크·정상 종료 제어·Linux VM 관리 명령 통합은 구현하지 않았다.

향후 SD에는 펌웨어와 Xen 설정 원본을 `/srv/perseus/rtos-templates/zephyr-4.4.0-xenvm/`에 보관한다. Pi에서 원본 PC 없이 독립 펌웨어 사본과 이름·UUID가 다른 Xen 설정을 만들 수 있는 입력을 갖춘다. 수동 `xl create`/콘솔 경로이며 기본 자동 시작이나 Linux 인스턴스 레지스트리에 몰래 추가하지 않는다. 이 경로의 Pi 실부팅은 다음 단계 검증이다.

## 64GB 용량

실제 연결 카드의 metadata를 `Get-Disk`로 조회한 크기는 **63,864,569,856 bytes**다. SD의 현재 파일 여유 공간을 측정하거나 변경한 것은 아니다.

| 실제 템플릿 점유량 | bytes |
| --- | ---: |
| Ubuntu 번들 | 2,143,076,352 |
| Debian 번들 | 1,082,150,912 |
| Zephyr 번들 | 1,409,024 |
| 세 번들 합계 | **3,226,636,288 (약 3.01GiB)** |

Ubuntu/Debian 각각의 8GiB 템플릿 원본과 기본 VM 각각의 독립 8GiB root, 총 32GiB 논리 용량을 계속 예약한다. RTOS의 실제 보관 파일 **1,385,296 bytes**를 payload와 5% metadata 여유 계산에 추가했다. RTOS에 불필요한 8GiB root를 배정하지 않았다.

- 선택 플랫폼 비압축 크기: 4,428,345,344 bytes.
- 세 템플릿 계획의 최소 카드 / 예상 비압축 최종 이미지 크기: **43,272,634,368 bytes (43.273GB)**.
- 카드 용량 기준 계획상 남는 공간: **20,591,935,488 bytes (20.592GB)**.
- RTOS 추가분이 기존 4MiB 정렬 여유 안에 들어가 최종 이미지 크기는 이전과 동일하다. 추가 payload가 누락된 것이 아니며 plan의 `retained_firmware_bytes`에 표시된다.
- metadata 여유 2,004,877,312 bytes, 운영 여유 2GiB를 포함했다. 두 Linux VM의 남은 성장 예산 14,118,584,320 bytes를 보존했다.
- 후속 조립 WSL scratch 상한: 90,973,614,080 bytes. 실제 VM 디스크/최종 ext4는 아직 생성하지 않았으므로 그 사용량·free space는 추정이며 후속 actual free-space gate가 남아 있다.

`three-template-plan.command/.json/.log/.exit-code`를 보존했다. plan exit 0, template_count 3, vm_count 2. 입력 파일 metadata 유지 및 출력/작업 디렉터리 부재를 확인했다. plan은 실제 WIC의 새 소스 byte gate나 하드웨어 검증 완료를 뜻하지 않는다.

## 수정과 검증

- `platform/guests/alpine/`의 두 파일을 제거하고 `platform/guests/zephyr/`의 고정 출처·검증 모듈·Xen 설정·계약 문서로 교체.
- `scripts/prepare-perseus-zephyr.py`: 완료 빌드·공식 원본/도구체인 검증, 기본 계획, 명시적 execute에서 새 불변 템플릿만 게시. 기존 출력/입력 덮어쓰기 금지.
- `platform/first-install/assemble.py`, `first_install_contract.py`, `example.config.json`: 선택적 `rtos_templates`의 검사·용량·복사·재검사·manifest 보관. 기본 두 Linux seed/인스턴스와 분리.
- 공유 Linux 준비/VM 모듈 세 곳의 Alpine 확장 예정 안내를 Linux/RTOS 계약에 맞게 수정했다. 기존 Linux 동작·비밀번호 정책은 바꾸지 않았다.
- README와 현행 빌드 가이드의 Alpine 확장 안내를 갱신했다. 과거 감사/구현 보고서는 당시 기록으로 보존했다.
- `tests/platform/test_perseus_zephyr.py`: 11개 신규 검사. 변조/링크/권한/추가 파일/잘못된 출처/미지원 기능 주장/다른 ELF·Image·timer/기존 출력/독립 복사/추가 용량/계획 무생성을 검사.
- `bash scripts/check-perseus-platform.sh`: **140 tests OK + 기존 17개 boot/init/network PASS**. 실제 systemd loader 정상 의존성 exit 0, 주입 cycle exit 1. 전체 로그·종료 코드 0을 보존했다.
- 기존 build 산출물 25개 checksum OK, 게시 WIC·BUILD-RESULT·release manifest checksum OK. 두 Linux 원본 digest와 템플릿 hash도 유지했다.

## 남은 승인·실행 항목

1. 이전 단계의 **Linux seed SSH 검사 `-G -C` 오류와 chroot 가상 filesystem 준비** 보완 및 회귀 검사. 이번 요청에 포함하지 않았고 해결됐다고 보고하지 않는다.
2. 수정된 공유 모듈/용량 계약을 포함한 dom0 재빌드와 새 완료 WIC 게시. 현재 성공 WIC는 보존 기준이며 source-byte gate를 우회하지 않는다.
3. 실제 세 템플릿 데이터 영역 조립·최종 파일/용량/free-space gate 검사. 이번에는 plan만 수행했다.
4. 별도 승인 후 SD 기록과 64GB/128GB 확장·Linux 두 VM의 보존 시험. Zephyr는 실보드 Xen DTB의 memory/GIC/IRQ/grant-table/실제 timer frequency와 정적 설정을 대조한 뒤 별도 수동 부팅·PV 콘솔 예제를 검증한다. 종료/네트워크/영구 데이터 경로는 추가 구현·검증 없이 지원으로 표시하지 않는다.
