# Raspberry Pi 4 + Xen — Yocto 6.0 Wrynose 수동 빌드 안내서

> Windows 11에서 WSL2 Ubuntu를 새로 준비하여, `setup-yocto.sh` 없이 Yocto 환경을 직접 구성하고 `xen-image-minimal`을 빌드하는 절차를 안내하는 문서입니다.
> 2026-10-13 빌드, WSL2 Ubuntu 24.04.5 / Poky 6.0.4 / BitBake 2.18.0 / Raspberry Pi 4 Model B 8GB  
> 실제 이미지 빌드 및 SD 이미지 정적 검사 성공. Raspberry Pi 4 실보드 부팅, domU 실행, HDMI는 미검증.

GitLab의 `scripts/setup-yocto.sh`를 이용해 동일한 빌드 환경을 자동 구성하려면 [자동화 가이드](./yocto-rpi4-xen-wsl2.md)를 참고하세요.

## 0. 목표 및 디렉터리

| 대상 | 선택한 구성 |
| --- | --- |
| 호스트 | Windows 11 → WSL2 → Ubuntu 24.04 LTS (`x86_64`) |
| Yocto / Poky | Wrynose 6.0 계열 (검증 당시 Poky 6.0.4) |
| BitBake | 2.18.0 |
| 타깃 | `raspberrypi4-64` / AArch64 |
| Xen dom0 커널 | `linux-raspberrypi` (`6.18.33+git` 확인) |
| init 시스템 | `systemd` |
| 최초 이미지 타깃 | `xen-image-minimal` |
| dom0 초기 메모리 | 1GiB (`RPI_DOM0_MEM = "1024M"`) |

디렉터리는 다음처럼 분리한다. 빌드·캐시는 GitLab에 커밋하지 않는다.

```text
/home/<사용자>/
├── yocto-workspace/
│   ├── layers/                 # 외부 Yocto 소스 저장소 6개
│   └── build-rpi4-xen/        # local.conf, 캐시, 최종 이미지
└── S15P31E110/               # GitLab 프로젝트
    └── platform/yocto/
        ├── layers.lock
        ├── config/local.conf.fragment
        └── meta-perseus/      # U-Boot 호환성 수정 .bbappend
```

WSL 내부 `~/`에 저장한다. `/mnt/c/` 등 Windows 마운트 폴더를 Yocto 빌드 작업 디렉터리로 사용하지 않는다. 빌드에는 큰 저장 공간과 상당한 시간이 필요하며, 아래 20GB 메모리 설정은 **검증한 PC의 예시**이지 필수 최소값은 아니다.

## 1. WSL2와 Ubuntu 24.04 설치

**Windows PowerShell(관리자 권한)** 에서 실행한다.

```powershell
wsl --install -d Ubuntu-24.04
```

재부팅 안내가 나오면 재부팅한다. `wsl --install` 이후 배포판이 아직 없다고 표시되면 같은 명령을 다시 실행한다. 최초 Ubuntu 시작 시 Linux 사용자 이름·비밀번호를 설정한다.

```powershell
wsl -l -v
wsl --status
wsl -d Ubuntu-24.04
```

`Ubuntu-24.04`의 WSL 버전이 **2**인지 확인한다.

### 선택: WSL2 자원 상한 조정

물리 RAM 32GB인 개발 PC에서 사용했던 값이다. 본인 PC에 맞춰 조정한다. Windows PowerShell에서:

```powershell
notepad "$env:USERPROFILE\.wslconfig"
```

```ini
[wsl2]
memory=20GB
swap=8GB
```

```powershell
wsl --shutdown
wsl -d Ubuntu-24.04
```

이후부터 별도 표시가 없으면 모든 명령은 **Ubuntu Bash**에서 실행한다.

## 2. 빌드 호스트 확인 및 필수 패키지

```bash
cd ~
cat /etc/os-release
uname -m
nproc
free -h
df -h ~
```

`Ubuntu 24.04`, `x86_64` 여부를 확인한다. `df -h ~`는 WSL의 가상 Linux 파일시스템 용량이며 Windows 저장소의 실제 남은 용량과 같지 않을 수 있다.

```bash
sudo apt update
sudo apt install -y \
  build-essential chrpath cpio debianutils diffstat file \
  gawk gcc git iputils-ping libacl1 libcrypt-dev locales \
  python3 python3-git python3-jinja2 python3-pexpect \
  python3-pip python3-subunit python3-websockets \
  socat texinfo unzip wget xz-utils zstd
```

```bash
git --version
python3 --version
gcc --version | head -n 1
make --version | head -n 1
locale -a | grep -i en_US || sudo locale-gen en_US.UTF-8
```

**참고:** 일부 WSL2에서 패키지 설치 후 `systemd-binfmt.service`의 `Read-only file system` 경고가 발생할 수 있다. 해당 경고를 '해결됨'으로 취급하지 않는다. 우선 `sudo dpkg --audit`와 `sudo apt-get check`로 패키지 설정·의존성을 점검한다. 일반 크로스 빌드에는 즉시 차단 요인이 아닐 수 있지만 QEMU 사용자 모드 실행 시 별도 검토가 필요할 수 있다.

## 3. Yocto 외부 소스 다운로드 및 커밋 고정

```bash
mkdir -p ~/yocto-workspace/layers
cd ~/yocto-workspace/layers

git clone -b wrynose --single-branch https://git.openembedded.org/openembedded-core
git clone -b 2.18 --single-branch https://git.openembedded.org/bitbake
git clone -b wrynose --single-branch https://git.yoctoproject.org/meta-yocto
git clone -b wrynose --single-branch https://git.yoctoproject.org/meta-raspberrypi
git clone -b wrynose --single-branch https://git.yoctoproject.org/meta-virtualization
git clone -b wrynose --single-branch https://git.openembedded.org/meta-openembedded
```

브랜치는 시간이 지나면 움직일 수 있다. 아래는 실제 빌드에 사용한 **고정 커밋**이다. 새 컴퓨터에서 같은 소스를 재현할 때 사용한다.

```bash
cd ~/yocto-workspace/layers

git -C openembedded-core checkout --detach 0f03c732a0a2d0506b41b1abe60b3f368b2300dc
git -C bitbake checkout --detach 51fd94a67fa17650a9ff623492005972bbc7d26f
git -C meta-yocto checkout --detach 7c25b11156553145fa6b241a2d4f65d94438f804
git -C meta-raspberrypi checkout --detach 919588393f41b48cb6c5d832aa35e58a19b6762d
git -C meta-virtualization checkout --detach 2807579615834a143c8ed453b463f93f43446019
git -C meta-openembedded checkout --detach 47d7dbdd10890bbd429bc0cccbe14435c37e5af1
```

`git -C <저장소> rev-parse HEAD`로 커밋을 확인한다. 이미 같은 저장소를 다운로드한 경우 중복 Clone하지 않는다. 고정 정보의 기준 파일은 GitLab의 `platform/yocto/layers.lock`이다.

## 4. 프로젝트의 `meta-perseus` Layer 준비

Wrynose의 `xen-rpi-u-boot-scr` 레시피에는 실제 빌드에서 `do_unpack` 경로 검증과 충돌했던 부분이 있다. 프로젝트에서는 외부 Layer를 직접 수정하지 않고 `meta-perseus`의 `.bbappend`로 수정한다.

**권장:** GitLab 프로젝트를 WSL 홈에 Clone해, 버전 관리 중인 수정 파일을 그대로 사용한다.

```bash
cd ~
git clone https://lab.ssafy.com/s15-final/S15P31E110.git
cd ~/S15P31E110
ls platform/yocto/meta-perseus/conf/layer.conf
ls platform/yocto/meta-perseus/recipes-bsp/xen-rpi-u-boot-scr/xen-rpi-u-boot-scr.bbappend
```

위 파일이 없다면 해당 변경이 포함된 프로젝트 브랜치/커밋으로 전환해야 한다. 최초 검증한 브랜치는 `feat/platform/yocto-build-setup`이며, 이후 기본 브랜치에 병합될 수 있다. **빌드 전 반드시 두 파일이 존재하는지 확인**한다.

레퍼런스 수정의 핵심은 다음과 같다(이미 GitLab 파일에 있으면 **중복 작성하지 않는다**).

```bitbake
# meta-perseus/.../xen-rpi-u-boot-scr.bbappend
UNPACKDIR = "${WORKDIR}/sources"
S = "${UNPACKDIR}"
```

> GitLab Clone이나 프로젝트 Layer 없이 독립적으로 시험하는 경우에도 같은 이름의 `.bbappend`와 올바른 `conf/layer.conf`를 갖는 로컬 Yocto Layer를 직접 구성할 수 있다. 다만 팀에서 공유하는 기준은 GitLab의 `meta-perseus`이므로 별도 복사본을 유지하지 않는 편이 좋다.

## 5. 빌드 디렉터리 생성과 Layer 수동 등록

**자동화 스크립트를 사용하지 않는 경로**다.

```bash
cd ~/yocto-workspace
source layers/openembedded-core/oe-init-build-env build-rpi4-xen

bitbake --version
```

생성된 `conf/bblayers.conf`에 아래 Layer를 차례대로 등록한다.

```bash
bitbake-layers add-layer ../layers/meta-yocto/meta-poky
bitbake-layers add-layer ../layers/meta-openembedded/meta-oe
bitbake-layers add-layer ../layers/meta-openembedded/meta-python
bitbake-layers add-layer ../layers/meta-openembedded/meta-networking
bitbake-layers add-layer ../layers/meta-openembedded/meta-filesystems
bitbake-layers add-layer ../layers/meta-raspberrypi
bitbake-layers add-layer ../layers/meta-virtualization
bitbake-layers add-layer ~/S15P31E110/platform/yocto/meta-perseus

bitbake-layers show-layers
```

**검증 조건:** `core`까지 총 **9개 Layer**가 인식되고, `perseus`가 표시되어야 한다. 이미 등록되어 있으면 같은 경로를 다시 추가하지 않는다.

## 6. `conf/local.conf` 수동 설정

```bash
cd ~/yocto-workspace/build-rpi4-xen
cp conf/local.conf conf/local.conf.before-perseus
nano conf/local.conf
```

파일 맨 아래에 다음 블록을 **한 번만** 추가한다. 이는 이번 성공한 설정과 동일하며, 저장소의 `platform/yocto/config/local.conf.fragment`에도 같은 설정이 있다.

```bitbake
# Perseus / Raspberry Pi 4 + Xen (Yocto 6.0 Wrynose)
MACHINE = "raspberrypi4-64"
DISTRO = "poky"

INIT_MANAGER = "systemd"
DISTRO_FEATURES:append = " virtualization xen"
PREFERRED_PROVIDER_virtual/kernel = "linux-raspberrypi"

# Initial allocation, not a measured optimum
RPI_DOM0_MEM = "1024M"

# Conservative limits for WSL2 (adjust for your PC)
BB_NUMBER_THREADS = "4"
PARALLEL_MAKE = "-j 4"
```

`nano` 저장은 `Ctrl+O` → Enter → `Ctrl+X`다.

**주의:** 초기 실험에서 중복 포함 경고를 일으킨 `require conf/distro/include/meta-virt-xen.inc`를 **추가하지 않는다**. U-Boot `S`·`UNPACKDIR` 수정은 `local.conf`에 중복 추가하지 않고 **`meta-perseus`의 `.bbappend`**가 담당한다. `linux-yocto` 대신 `linux-raspberrypi`를 선택한 것도 이 검증된 빌드 조합의 중요한 조건이다.

## 7. 파싱 및 설정 검사

```bash
bitbake -p
bitbake-layers show-appends | grep -A 2 'xen-rpi-u-boot-scr'

bitbake -e xen-image-minimal | grep -E \
'^(MACHINE|DISTRO_FEATURES|INIT_MANAGER|PREFERRED_PROVIDER_virtual/kernel|RPI_USE_U_BOOT|ENABLE_UART|IMAGE_FSTYPES|DEPLOYPAYLOAD|KERNEL_DEVICETREE)='

bitbake -e xen-rpi-u-boot-scr | grep -E '^(S|UNPACKDIR|RPI_DOM0_MEM)='
bitbake -n xen-image-minimal
```

**검증 조건:** `MACHINE=raspberrypi4-64`, `INIT_MANAGER=systemd`, 커널 `linux-raspberrypi`, `RPI_DOM0_MEM=1024M`, Xen 관련 기능, `S=UNPACKDIR=.../sources`가 확인되고 `bitbake -n`이 의존성 오류 없이 종료되어야 한다. `-n`은 **Dry Run**이며 실제 컴파일이 아니다.

## 8. 선택: 커널 Configure 및 Xen 설정 확인

설정이 적용되었는지 확인하는 단계다. 이를 별도로 실행하지 않아도 최종 이미지 빌드에서 필요한 태스크를 수행하지만, 커널 문제를 일찍 발견하려면 권장한다.

```bash
bitbake -c configure linux-raspberrypi

KERNEL_B=$(bitbake -e linux-raspberrypi | \
  sed -n 's/^B="\([^"]*\)"$/\1/p')

ls -lh "$KERNEL_B/.config"
for symbol in XEN XEN_DOM0 XEN_BACKEND \
  XEN_BLKDEV_BACKEND XEN_NETDEV_BACKEND \
  XEN_GNTDEV XEN_GRANT_DEV_ALLOC XEN_WDT \
  XENFS XEN_PRIVCMD; do
  grep -E "^(CONFIG_${symbol}=|# CONFIG_${symbol} is not set)" \
    "$KERNEL_B/.config" || echo "CONFIG_${symbol}: not found"
done
```

이번 빌드에서는 `CONFIG_XEN_DOM0=y`, `CONFIG_XEN_BACKEND=y`, `CONFIG_XEN_BLKDEV_BACKEND=m`, `CONFIG_XEN_NETDEV_BACKEND=m` 등을 확인했다. **`=m`은 모듈 빌드 대상으로 지정됐다는 뜻이지 아직 파일이 생성됐다는 뜻은 아니다.**

## 9. 이미지 실제 빌드

```bash
cd ~/yocto-workspace/build-rpi4-xen
bitbake xen-image-minimal
```

이 명령은 실제 다운로드·컴파일·패키징·이미지 조립을 수행한다. 최초 빌드는 오래 걸릴 수 있다. 실패 시 빌드 디렉터리 전체를 삭제하지 말고 실패 태스크의 로그를 확인하고 수정한 뒤 같은 명령을 다시 실행한다.

**검증 조건:** `Tasks Summary: ... all succeeded`, 오류 0개, 다음 경로에 배포 산출물이 존재해야 한다.

```bash
IMG_DIR=tmp/deploy/images/raspberrypi4-64
ls -lh "$IMG_DIR"
```

이번 구성에서 확인한 주요 산출물:

| 산출물 | 의미 |
| --- | --- |
| `xen-raspberrypi4-64` | Xen 실행 바이너리 |
| `Image` | dom0 Linux ARM64 커널 |
| `bcm2711-rpi-4-b.dtb` | Pi 4 Device Tree |
| `boot.scr`, `u-boot.bin` | U-Boot 부팅 관련 파일 |
| `xen-image-minimal-raspberrypi4-64.rootfs.rpi-sdimg` | SD 카드에 쓸 최종 디스크 이미지 |
| `*.manifest` | rootfs 포함 패키지 목록 |

이번 성공 이미지의 크기는 약 3.08GiB였다. **디스크 이미지 크기와 실행 시 RAM 사용량은 다른 값**이다.

## 10. SD 이미지 정적 검사

부팅 파일들이 실제 최종 디스크 이미지에 포함되는지, 디스크 파티션 구성이 적절한지를 확인한다. 다음 명령은 이미지 파일을 읽기만 한다.

```bash
sudo apt install -y mtools

IMG_DIR=tmp/deploy/images/raspberrypi4-64
BOOT_IMG="$IMG_DIR/xen-image-minimal-raspberrypi4-64.rootfs.vfat"
SD_IMG="$IMG_DIR/xen-image-minimal-raspberrypi4-64.rootfs.rpi-sdimg"

# FAT 부팅 파티션 이미지 검사
mdir -i "$BOOT_IMG" ::/

# Xen 부팅 인자와 커널 적재 명령 확인
strings "$IMG_DIR/boot.scr" | grep -E 'dom0_mem|fatload|root=PARTUUID'

# 최종 SD 이미지의 파티션 구조 검사
sudo fdisk -l "$SD_IMG"
```

**검증 조건:** 첫 파티션은 FAT 부팅 영역, 두 번째는 Linux rootfs 영역이며, FAT에 `xen`, `Image`, `boot.scr`, `kernel8.img`, Pi 4 DTB 등이 존재해야 한다. `dom0_mem='1024M'`이 `boot.scr`에 나타나야 한다.

이전에 확인한 SD 이미지의 첫 번째 파티션 시작 섹터는 **8192**, 섹터 크기는 **512바이트**였다. 동일한 구성이라면 4MiB 오프셋으로 최종 디스크 안쪽을 검사할 수 있다. **다른 이미지에서는 먼저 `fdisk -l` 결과를 확인하고 오프셋을 다시 계산한다.**

```bash
# 8192 * 512 = 4194304 (이 이미지에서 확인된 값)
mdir -i "${SD_IMG}@@4194304" ::/
mtype -i "${SD_IMG}@@4194304" ::config.txt
mtype -i "${SD_IMG}@@4194304" ::cmdline.txt
```

`config.txt`에서 `enable_uart=1`, `enable_gic=1` 등을 확인한다. `cmdline.txt`의 일반 Linux 부팅 인자와, U-Boot가 Device Tree에 설정하는 Xen/dom0 부팅 인자는 구분한다.

## 11. 자주 만나는 문제

| 증상 | 확인 및 대응 |
| --- | --- |
| `xen-image-minimal ... missing systemd` | `INIT_MANAGER = "systemd"` 확인 |
| `Nothing PROVIDES 'virtual/kernel'` | `PREFERRED_PROVIDER_virtual/kernel = "linux-raspberrypi"` 확인 |
| `xen-rpi-u-boot-scr do_unpack: S should be set relative to UNPACKDIR` | 프로젝트 `meta-perseus` Layer 등록 및 `.bbappend`의 `S`·`UNPACKDIR` 확인 |
| `Duplicate inclusion ... meta-virt-xen.inc` | `local.conf`에 불필요한 `require`를 수동 추가하지 않았는지 확인 |
| WSL2 `systemd-binfmt.service` 실패 | `dpkg --audit`, `apt-get check` 확인. QEMU 사용자 모드 등록과 별도로 취급 |
| `do_fetch` 원본 URL 경고 | Mirror 재시도 후 최종 태스크 성공 여부 확인. 최종 실패라면 해당 로그 검토 |
| `CONFIG_TASK_IO_ACCOUNTING` 경고 | 빌드 호스트 커널의 I/O 통계 기능 경고. 현재 실제 이미지 빌드를 막지는 않았음 |
| Windows SSD 공간이 줄어듦 | WSL2 VHDX는 사용에 따라 증가. Yocto `tmp`, `downloads`, `sstate-cache`의 물리 사용량 고려 |

기존 `tmp/`와 이미지는 터미널 종료·Windows 재부팅으로 자동 삭제되지 않는다. 반면 `bitbake -c cleanall`, 작업 폴더 삭제, WSL 배포판 초기화는 일부 또는 전체 결과물을 잃게 할 수 있다.

## 참고

- [Yocto Project 문서](https://docs.yoctoproject.org/)
- [Yocto 6.0 시스템 요구사항](https://docs.yoctoproject.org/6.0/ref-manual/system-requirements.html)
- [OpenEmbedded meta-virtualization Layer](https://layers.openembedded.org/layerindex/branch/wrynose/layer/meta-virtualization/)
- 팀 GitLab: `platform/yocto/layers.lock`, `platform/yocto/config/local.conf.fragment`, `platform/yocto/meta-perseus/`
