# Raspberry Pi 4 Yocto/Xen 빌드 버전 기록

빌드 구성별로 버전과 소스 목록을 보존한다. 프로젝트 커밋은 기록 식별용이며, 실행 절차는 [재구성 문서](yocto-rpi4-xen-reproduce.md)를 따른다. 새 구성은 아래 두 항목으로 추가한다.

## 2026-10-15 기준 구성

- 기준 소스: `c3f0e9e90549104543fde6c853b264d2b309c4de`.
- 확인 상태: 별도 Linux 경로의 환경·파싱 검증 완료. 기존 환경의 이미지 생성 증거는 [감사 기록](yocto-rpi4-xen-audit-20261015.md)에 있으며, 현재 기준의 새 환경 전체 빌드·실보드 부팅은 미검증이다.

### 1. 빌드 구성 및 호환성 버전

호스트 값은 기록일의 관측값이다. 타깃 값은 같은 잠금 구성의 BitBake 해석값·기존 산출물에서 확인했으며, 아래 조합을 최소 요구 버전이나 모든 기능의 호환 보증으로 해석하지 않는다.

| 구성요소 | 버전·설정 |
| --- | --- |
| Windows | Windows 11 Enterprise x64 / `10.0.26200.9711` |
| WSL | WSL2 / 프로그램 `3.0.3.0` |
| WSL 호스트 커널 | `6.18.54.1-microsoft-standard-WSL2` |
| Ubuntu | `24.04.5 LTS` / `x86_64` |
| Python / Git | `3.12.3` / `2.43.0` |
| 호스트 GCC / GNU Make | `13.3.0` / `4.3` |
| 호스트 GNU tar / glibc | `1.35` / `2.39` |
| 타깃 GCC / binutils | `15.3.0` / `2.46.1` |
| 타깃 glibc / systemd | `2.43+git0+1c9988e525` / `259.5` |
| Yocto / Poky | `Wrynose` / `6.0.4` |
| BitBake | `2.18.0` |
| 보드 / MACHINE | Raspberry Pi 4 / `raspberrypi4-64` |
| 타깃 아키텍처 / 튜닝 | `aarch64` / `cortexa72-nocrypto` |
| DISTRO / INIT_MANAGER | `poky` / `systemd` |
| DISTRO_FEATURES 추가값 | `virtualization xen` |
| Xen | `4.21.0+stable` |
| Linux | `6.18.33+git` / `linux-raspberrypi` / `bcm2711_defconfig` |
| U-Boot | `2026.01` / `rpi_arm64_config` |
| 레이어 공통 호환 기준 | `wrynose` — 등록 레이어 9개가 지원 |
| dom0 메모리 / 병렬도 | `1024M` / `BB_NUMBER_THREADS=4`, `PARALLEL_MAKE=-j 4` |

외부 저장소 URL·정확한 SHA의 기준은 [이 구성의 layers.lock](https://lab.ssafy.com/s15-final/S15P31E110/-/blob/c3f0e9e90549104543fde6c853b264d2b309c4de/platform/yocto/layers.lock)이다. Xen·Linux·U-Boot의 소스 리비전은 해당 고정 레이어의 레시피가 결정한다.

### 2. 레이어·파일·폴더 목록

외부 저장소 6개 중 BitBake는 `$YOCTO_WORKSPACE/layers/bitbake/`에 두는 빌드 도구다. 나머지 외부 저장소와 자체 레이어가 아래 9개 레이어를 제공한다. `layers/`는 `$YOCTO_WORKSPACE/layers/`, `platform/`은 프로젝트 루트 기준이다.

| 등록 레이어 | 경로 | 관리 방식 |
| --- | --- | --- |
| core | `layers/openembedded-core/meta/` | 외부 잠금 |
| yocto (meta-poky) | `layers/meta-yocto/meta-poky/` | 외부 잠금 |
| openembedded-layer | `layers/meta-openembedded/meta-oe/` | 외부 잠금 |
| meta-python | `layers/meta-openembedded/meta-python/` | 외부 잠금 |
| networking-layer | `layers/meta-openembedded/meta-networking/` | 외부 잠금 |
| filesystems-layer | `layers/meta-openembedded/meta-filesystems/` | 외부 잠금 |
| raspberrypi | `layers/meta-raspberrypi/` | 외부 잠금 |
| virtualization-layer | `layers/meta-virtualization/` | 외부 잠금 |
| perseus | `platform/yocto/meta-perseus/` | 프로젝트 Git |

프로젝트가 직접 관리하는 빌드 입력·검사 도구:

```text
platform/yocto/
├── layers.lock
├── config/local.conf.fragment
└── meta-perseus/
    ├── conf/layer.conf
    ├── recipes-bsp/xen-rpi-u-boot-scr/xen-rpi-u-boot-scr.bbappend
    ├── recipes-core/
    │   ├── images/
    │   │   ├── perseus-xen-bringup-image.bb
    │   │   └── perseus-domu-initramfs.bb
    │   ├── perseus-bringup/
    │   │   ├── perseus-bringup_1.0.bb
    │   │   └── files/
    │   │       ├── perseus-domu.cfg
    │   │       ├── perseus-bridge-up
    │   │       ├── perseus-bridge.service
    │   │       ├── perseus-dhcp.service
    │   │       ├── dnsmasq-bringup.conf
    │   │       └── perseus-bringup-check
    │   └── perseus-domu-init/
    │       ├── perseus-domu-init_1.0.bb
    │       └── files/
    │           ├── init
    │           └── udhcpc.script
    └── recipes-support/dnsmasq/dnsmasq_%.bbappend
scripts/
├── setup-yocto.sh
└── check-xen-bringup-prereqs.sh
tests/platform/test_yocto_setup.py
```

기본 이미지 `xen-image-minimal.bb`와 Xen 부트 템플릿 `boot.cmd.xen.in`은 외부 `meta-virtualization`에서 제공한다. 커널·U-Boot·펌웨어 원본과 기본 설정도 고정 외부 레시피·소스에서 받으며, 프로젝트 변경은 위 자체 레이어와 fragment로 적용한다.

생성 경로(Git 추적 제외): `$YOCTO_BUILD_DIR/conf/local.conf`·`conf/bblayers.conf`, `tmp/deploy/images/raspberrypi4-64/`, `tmp/`·`downloads/`·`sstate-cache/`. 산출 타깃은 `xen-image-minimal`, `perseus-xen-bringup-image`, `perseus-domu-initramfs`다.
