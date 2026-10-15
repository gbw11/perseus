# Raspberry Pi 4 Yocto/Xen 환경 재구성 및 domU 인계

GitLab 프로젝트와 `platform/yocto/layers.lock`만으로 **새 Linux 작업 경로**에 환경을 준비한다. 원본 빌드의 `conf`, 외부 레이어 복사본, ZIP은 필요하지 않다. 실제 검증 범위는 [감사 기록](yocto-rpi4-xen-audit-20261015.md)에 기록한다.

## 1. 호스트 준비와 Clone

감사한 호스트는 Ubuntu 24.04.5 LTS / x86_64 / WSL2, Python 3.12.3, Git 2.43.0이다. Linux 파일시스템을 사용한다. `/mnt/c`의 프로젝트·빌드 경로 및 공백·따옴표가 있는 경로는 설정 도구가 거부한다. 전체 빌드에는 충분한 디스크와 메모리가 필요하며 현재 병렬 작업 설정은 4개다.

기존 빌드 안내에서 사용한 Ubuntu 호스트 패키지는 다음과 같다. 이미 준비된 호스트에서는 재설치하지 않아도 된다. 설정 도구는 시스템 패키지나 인증 설정을 설치·변경하지 않는다.

```bash
sudo apt update
sudo apt install -y \
  build-essential chrpath cpio debianutils diffstat file \
  gawk gcc git iputils-ping libacl1 libcrypt-dev locales \
  python3 python3-git python3-jinja2 python3-pexpect \
  python3-pip python3-subunit python3-websockets \
  socat texinfo unzip wget xz-utils zstd
locale -a | grep -i en_US || sudo locale-gen en_US.UTF-8
```

GitLab 인증은 본인의 기존 Git 인증 도구로 준비한다. 비밀번호·토큰을 URL, 잠금 파일 또는 설정에 넣지 않는다.

```bash
mkdir -p "$HOME/perseus-repro"
cd "$HOME/perseus-repro"
git clone --branch feat/platform/yocto-build-setup --single-branch \
  https://lab.ssafy.com/s15-final/S15P31E110.git
cd S15P31E110
export PERSEUS_PROJECT="$PWD"
git status --short

export YOCTO_WORKSPACE="$HOME/perseus-repro/workspace"
export YOCTO_BUILD_DIR="$YOCTO_WORKSPACE/build-rpi4-xen"
bash scripts/setup-yocto.sh --verify
```

`YOCTO_WORKSPACE`와 `YOCTO_BUILD_DIR`는 **새 경로**를 선택한다. 최초 외부 소스 확보에는 네트워크가 필요하다. `--verify`는 이미지를 컴파일하지 않는다.

## 2. 설정 도구의 계약

`scripts/setup-yocto.sh`는 이미 존재한다. `platform/yocto/` 아래에 동일한 도구를 새로 만들지 않는다.

- `layers.lock`의 저장소 이름 6개, 공개 HTTPS URL, 40자리 SHA와 중복을 먼저 검사한다.
- 소스는 `workspace/layers/<repository>`에 Clone하고 정확한 SHA로 detached checkout한다. `wrynose`/`2.18`은 이력을 받는 브랜치이며 **버전 기준은 잠금 SHA**다.
- 기존 checkout은 remote URL·HEAD·dirty 상태를 검사하고 자동 checkout, reset, pull, 패치를 하지 않는다. 다르면 중단한다.
- 새 빌드를 초기화하고 fragment와 9개 레이어 경로를 관리 블록으로 작성한다. 경로는 현재 Clone 및 작업 공간에서 계산한다.
- 모든 레이어와 MACHINE/DISTRO 설정을 준비한 다음 BitBake를 실행한다.
- `--verify`는 `show-layers`, `bitbake -p`, U-Boot bbappend·`S == UNPACKDIR`, 이미지 3종의 주요 설정을 검사한다.
- 기본 실행도 `--verify`도 이미지를 빌드하지 않는다. `--check`는 **기존 외부 소스만 검사**하며 Clone이나 설정 생성을 하지 않는다.

```bash
bash scripts/setup-yocto.sh --help
bash scripts/setup-yocto.sh --check
python3 -m unittest discover -s tests/platform -v
```

재실행 시 관리 블록은 일치해야 한다. 기존 수동 빌드나 이전 도구 버전의 설정은 자동 전환하지 않는다. `not owned` 또는 `managed settings differ`이면 기존 디렉터리를 보존하고 새 빌드 디렉터리를 선택한다. fragment를 바꿀 때에도 기존 설정을 자동 덮어쓰지 않는다.

## 3. 이미지 빌드와 산출물

설정 도구는 호출한 셸의 BitBake 환경을 유지하지 않으므로 다시 source한다.

```bash
source "$YOCTO_WORKSPACE/layers/openembedded-core/oe-init-build-env" "$YOCTO_BUILD_DIR"
bitbake-layers show-layers
bitbake -p
bitbake -n perseus-xen-bringup-image

# 기존 기본 Xen/dom0 이미지
bitbake xen-image-minimal

# 진단 도구와 테스트 domU 자산을 포함한 dom0 이미지
bitbake perseus-xen-bringup-image

# 필요하면 테스트 domU initramfs만 생성
bitbake perseus-domu-initramfs

ls -lh tmp/deploy/images/raspberrypi4-64/
```

`bitbake -n`은 실행 계획 확인이며 실제 이미지 빌드와 구분한다.

| 타깃 | 산출물 및 의미 |
| --- | --- |
| `xen-image-minimal` | `xen-image-minimal-raspberrypi4-64.rootfs.rpi-sdimg`, `.manifest`, `.ext3`, `.tar.bz2`, `.wic.bz2` 등. meta-virtualization의 기본 Xen/dom0 이미지 |
| `perseus-xen-bringup-image` | 같은 형식의 `perseus-xen-bringup-image-raspberrypi4-64.rootfs.*`. 기본 이미지를 확장한 별도 테스트 이미지 |
| `perseus-domu-initramfs` | `perseus-domu-initramfs-raspberrypi4-64.rootfs.cpio.gz` 및 `.manifest`. 디스크 없는 최소 테스트 게스트 rootfs |
| 공통 부트 자산 | `Image`, `xen-raspberrypi4-64`, `boot.scr`, `u-boot.bin`, Pi 4 DTB |

타임스탬프가 붙은 실제 파일과 위 이름의 심볼릭 링크가 함께 생성된다. 기본 `TMPDIR`, `DL_DIR`, `SSTATE_DIR`는 새 빌드 아래에 있으므로 기존 캐시와 분리된다. 대형 이미지·로그는 Git에 넣지 않고 별도 산출물 저장소에서 전달한다.

## 4. 현재 구성과 부트 체인

확인한 구성은 `raspberrypi4-64`, `poky` 6.0.4, `systemd`, `linux-raspberrypi` 6.18.33+git, Xen 4.21.0+stable, U-Boot 2026.01이다. 선택된 버전과 원본 로그의 근거는 감사 기록을 참고한다.

Pi 펌웨어 → U-Boot → `boot.scr` → Xen → dom0 순서다. 외부 meta-virtualization의 Raspberry Pi 동적 레이어가 Xen 부트 스크립트를 제공한다. 프로젝트 bbappend는 unpack 경로 호환성만 보완한다. 생성된 `boot.scr`는 FAT에서 `xen`·`Image`를 적재하며 dom0 rootfs는 두 번째 SD 파티션의 PARTUUID로 찾는다. Xen dom0 메모리는 `RPI_DOM0_MEM = "1024M"`, 콘솔은 `hvc0`다.

현재 rootfs에 자산이 포함되었다는 정적 증거는 있다. 실보드 UART 부팅, `xl info`, 게스트 부팅·통신 성공은 별도 검증 대상이다.

## 5. domU 담당자가 수정할 위치

아래 경로는 모두 `platform/yocto/meta-perseus/` 기준이다.

| 위치 | 현재 역할 / 후속 작업 |
| --- | --- |
| `recipes-core/images/perseus-domu-initramfs.bb` | busybox·base-passwd·iproute2·udhcpc·자체 `/init`을 `cpio.gz`로 패키징 |
| `recipes-core/perseus-domu-init/` | `/init` 및 `/etc/perseus/udhcpc.script`. proc/sys/dev 마운트, hvc0 연결, DHCP, 대화형 셸 |
| `recipes-core/images/perseus-xen-bringup-image.bb` | domU 이미지 완료와 커널 deploy를 의존성으로 연결. rootfs 후처리에서 게스트 자산 2개 복사 |
| `recipes-core/perseus-bringup/files/perseus-domu.cfg` | `xl create`에 전달할 VM 설정. kernel/ramdisk 경로, 메모리·vCPU·MAC·bridge 연결 |
| `recipes-core/perseus-bringup/perseus-bringup_1.0.bb` | 진단 스크립트·VM 설정·systemd unit 설치. 네트워크 unit 자동 활성화는 disable |
| `recipes-core/perseus-bringup/files/` | Bridge, DHCP, 진단 스크립트와 service 원본 |
| `recipes-support/dnsmasq/dnsmasq_%.bbappend` | 기본 dnsmasq unit 자동 기동 비활성화. **dnsmasq를 설치하는 다른 이미지에도 적용되는 레이어 범위 설정** |
| `recipes-bsp/xen-rpi-u-boot-scr/` | 기존 Xen 부트 스크립트 unpack 경로 호환성 보완 |

연결 관계:

```text
perseus-domu-init → perseus-domu-initramfs:do_image_complete → rootfs.cpio.gz
virtual/kernel:do_deploy → Image
                                ↓ bring-up 이미지 do_rootfs 후처리
/opt/perseus/bringup/Image
/opt/perseus/bringup/domU-initramfs.cpio.gz
                                ↑ /etc/xen/perseus-domu.cfg의 kernel / ramdisk
xl create -c /etc/xen/perseus-domu.cfg
```

현재 게스트는 `perseus-test`, `type="pvh"`, 256MiB, 1 vCPU이고 dom0 커널을 재사용한다. 영구 디스크·정식 배포판 init·애플리케이션·에이전트가 없는 임시 진단 이미지다. Debian/Ubuntu/Alpine 완성형 템플릿이나 3종 domU 통합은 구현되어 있지 않다. 후속 담당자는 디스크 rootfs 또는 initramfs 방식, 게스트 전용 커널, 서비스·정상 종료·저장 정책을 결정하고 새 이미지 레시피와 VM 설정으로 확장한다.

네트워크는 물리 NIC에 연결하지 않은 `xenbr0`, dom0 `192.168.77.1/24`, DHCP `192.168.77.100~150`, MAC `02:16:3e:20:00:01`이다. DNS·기본 라우터를 제공하지 않는다. 네트워크 unit은 수동 시작한다.

```bash
# 커널 configure/빌드 후 호스트에서 필수 옵션 검사
bash "$PERSEUS_PROJECT/scripts/check-xen-bringup-prereqs.sh"

# 아래는 실보드 dom0에서 수행할 후속 검증이며 이번 감사의 부팅 성공 증거가 아니다.
systemctl start perseus-bridge.service perseus-dhcp.service
xl create -c /etc/xen/perseus-domu.cfg
```

bring-up 이미지는 기존 설정대로 root 빈 비밀번호를 허용하고 SSH 도구를 포함한다. UART 진단용 설정이므로 배포 이미지에 적용하기 전에 인증 정책을 별도로 정해야 한다. 임시 게스트 PID 1은 셸 재시작 루프이며 정상 종료 처리는 미구현이다. `xl destroy`는 정상 종료 검증이 아니다. 보드 점검은 [bring-up 절차](yocto-rpi4-xen-minimal-bringup.md)를 따른다.

## 6. 재현성의 경계

잠금 SHA와 소스·설정으로 같은 빌드 입력을 구성하는 것, 캐시를 활용한 이미지 재생성, 캐시 없는 전체 컴파일, 이미지 바이트 동일성, 실제 부팅 성공은 각각 다른 검증이다. 수행한 수준만 감사 기록에 표시한다.

기본 설정은 캐시를 공유하지 않는다. 선택적으로 기존 캐시를 사용할 때는 새 빌드의 `conf/site.conf`에 읽기용 mirror를 지정할 수 있다. 개발자별 최적화이며 Git에 올리거나 환경 준비의 필수 조건으로 삼지 않는다.

```bitbake
# 실제 캐시 경로로 바꾼다. 새 빌드의 SSTATE_DIR는 그대로 유지한다.
SSTATE_MIRRORS = "file://.* file:///absolute/existing-build/sstate-cache/PATH"
```

패키지 공급자·소스 URL의 장기 가용성이나 모든 호스트 조합까지 검증한 것은 아니다. 완전한 오프라인 빌드가 필요하면 다운로드·sstate mirror를 별도로 운영해야 한다.
