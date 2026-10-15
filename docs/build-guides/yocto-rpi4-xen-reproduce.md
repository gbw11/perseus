# Raspberry Pi 4 Yocto/Xen 환경 재구성 및 domU 인계

**이 문서가 팀원의 환경 준비 시작점이다.** GitLab에서 Clone한 최신 프로젝트와 그 안의 `platform/yocto/layers.lock`을 출발점으로 **새 Linux 작업 경로**에 환경을 준비한다. 문서화된 호스트 패키지·GitLab 인증·외부 소스 다운로드를 위한 네트워크는 필요하다. 기존 서버의 `conf`, 외부 레이어 복사본, 개인 스크립트나 ZIP은 필요하지 않다.

| 하려는 작업 | 읽을 문서 / 범위 |
| --- | --- |
| 처음 환경 준비·최신 프로젝트 갱신 | 이 문서 1~2절. `--verify` 완료 후 종료 |
| 실제 이미지 생성·domU 통합 개발 | 이 문서 3~6절. 빌드는 별도 선택 단계 |
| 커널·이미지·실보드 검증 | [최소 bring-up 검증](yocto-rpi4-xen-minimal-bringup.md) |
| 과거에 확인한 환경·성공 로그·한계 조회 | [2026-10-15 감사 기록](yocto-rpi4-xen-audit-20261015.md). 현재 실행 절차는 이 문서를 따름 |


프로젝트는 팀이 사용하는 통합 브랜치의 최신 버전을 받는다. 스크립트·자체 레시피·공통 설정·잠금 파일은 같은 checkout의 파일을 함께 사용한다. 팀원마다 환경 구성 스크립트를 따로 만들거나 복사할 필요는 없다.

외부 레이어는 **현재 프로젝트의 `layers.lock`에 기록된 정확한 커밋**을 사용한다. 프로젝트의 최신 버전을 받는 것과 외부 의존성을 고정하는 것은 함께 유지한다. 감사 기록의 프로젝트 SHA는 과거 검증 대상을 식별하는 기록이며, 아래 실행 절차에서 checkout할 버전이 아니다.

## 1. 호스트 준비와 Clone

감사한 호스트는 Ubuntu 24.04.5 LTS / x86_64 / WSL2, Python 3.12.3, Git 2.43.0이다. Linux 파일시스템을 사용한다. `/mnt/c`의 프로젝트·빌드 경로 및 공백·따옴표가 있는 경로는 설정 도구가 거부한다. 전체 빌드에는 충분한 디스크와 메모리가 필요하며 현재 병렬 작업 설정은 4개다.

### Windows 사용자의 WSL 준비

이미 Ubuntu Linux를 사용한다면 이 단계는 생략한다. WSL이 없다면 관리자 PowerShell에서 설치하고, 재부팅·최초 Linux 계정 생성을 마친다.

```powershell
wsl --install -d Ubuntu-24.04
```

PowerShell에서 배포판과 WSL 버전 2를 확인한 뒤 Ubuntu 터미널로 들어간다.

```powershell
wsl -l -v
wsl --status
wsl -d Ubuntu-24.04
```

선택적으로 WSL 자원 상한을 조정할 수 있다. 기존 32GB RAM PC에서 `%USERPROFILE%\.wslconfig`에 `memory=20GB`, `swap=8GB`를 사용한 사례가 있으며 필수값은 아니다. 설정 반영을 위해 WSL을 재시작할 때는 먼저 실행 중인 작업을 종료한다.

### Linux 호스트 확인과 패키지

이하 명령은 Ubuntu Bash에서 실행한다. 프로젝트·외부 소스·빌드 경로 모두 Linux 파일시스템을 사용한다. WSL 내부 여유 공간과 Windows 실제 디스크 여유 공간을 함께 확인한다.

```bash
cat /etc/os-release
uname -m
nproc
free -h
df -h "$HOME"
```

Ubuntu 호스트 패키지는 다음과 같다. 이미 준비된 호스트에서는 재설치하지 않아도 된다. 설정 도구는 시스템 패키지나 인증 설정을 설치·변경하지 않는다.

```bash
sudo apt update
sudo apt install -y \
  build-essential chrpath cpio debianutils diffstat file \
  gawk gcc git iputils-ping libacl1 libcrypt-dev locales \
  python3 python3-git python3-jinja2 python3-pexpect \
  python3-pip python3-subunit python3-websockets \
  socat texinfo unzip wget xz-utils zstd
locale -a | grep -i en_US || sudo locale-gen en_US.UTF-8
export LANG=en_US.UTF-8
export LC_ALL=en_US.UTF-8
```

GitLab 인증은 본인의 기존 Git 인증 도구로 준비한다. 비밀번호·토큰을 URL, 잠금 파일 또는 설정에 넣지 않는다.

### 처음 Clone하여 환경만 준비

아래 명령은 GitLab에서 지정한 **기본 브랜치의 최신 버전**을 받는다. 이 문서와 `scripts/setup-yocto.sh`, `meta-perseus`의 해당 변경이 기본 브랜치에 병합되어 있어야 한다. 아직 없는 파일은 다른 브랜치에서 개별 복사하지 말고 팀의 통합 상태를 확인한다.

```bash
mkdir -p "$HOME/perseus-repro"
cd "$HOME/perseus-repro"
git clone https://lab.ssafy.com/s15-final/S15P31E110.git
cd S15P31E110
export PERSEUS_PROJECT="$PWD"
git status --short --branch

export YOCTO_WORKSPACE="$HOME/perseus-repro/workspace"
export YOCTO_BUILD_DIR="$YOCTO_WORKSPACE/build-rpi4-xen"
bash scripts/setup-yocto.sh --verify
```

`YOCTO_WORKSPACE`와 `YOCTO_BUILD_DIR`는 **새 경로**를 선택한다. 최초 외부 소스 확보에는 네트워크가 필요하다. `--verify`는 소스 확보·설정 생성·메타데이터 검증만 수행하며 이미지를 컴파일하지 않는다. `Setup complete (no image build or boot test performed)`가 출력되고 종료 코드가 0이면 환경 준비가 끝난다. 환경만 준비할 때는 여기서 멈춘다.

### 기존 Clone을 최신 프로젝트로 갱신

기존 Clone은 현재 브랜치가 추적하는 원격 브랜치의 최신 버전을 받는다. 먼저 자신의 프로젝트 경로에서 상태를 확인한다.

```bash
cd "$HOME/perseus-repro/S15P31E110"
export PERSEUS_PROJECT="$PWD"
git status --short --branch
```

미커밋 변경이나 진행 중인 merge/rebase가 없고, 현재 브랜치에 올바른 원격 추적 대상이 설정되어 있는지 확인한 뒤 실행한다. detached HEAD 상태라면 팀에서 사용할 브랜치를 먼저 정해야 한다. `git pull --ff-only`가 실패하면 로컬 변경이나 갈라진 이력을 자동 덮어쓰지 말고 원인을 확인한다.

```bash
git pull --ff-only

export YOCTO_WORKSPACE="$HOME/perseus-repro/workspace"
export YOCTO_BUILD_DIR="$YOCTO_WORKSPACE/build-rpi4-xen"
bash scripts/setup-yocto.sh --verify
```

GitLab 기본 브랜치가 변경되어도 기존 Clone의 현재 브랜치는 자동 전환되지 않는다. 팀에서 통합 대상을 바꾸면 각자 작업 상태를 확인하고 사용할 추적 브랜치를 선택한다. 설정 도구는 프로젝트 브랜치를 전환하거나 프로젝트 커밋을 checkout하지 않는다.

### 잠금 파일·공통 설정이 갱신된 경우

업데이트된 `layers.lock`과 기존 외부 checkout의 SHA가 다르거나, 공통 fragment와 기존 관리 블록이 다르면 설정 도구는 중단한다. 기존 빌드·캐시·소스 변경을 보존하기 위한 동작이다.

새 작업 공간이 필요할 때만 아래를 실행한다. 실행할 때마다 고유한 새 디렉터리를 만들며 기존 작업 공간은 유지한다.

```bash
if YOCTO_WORKSPACE="$(mktemp -d "$HOME/perseus-repro/workspace.XXXXXX")"; then
  export YOCTO_WORKSPACE
  export YOCTO_BUILD_DIR="$YOCTO_WORKSPACE/build-rpi4-xen"
  bash "$PERSEUS_PROJECT/scripts/setup-yocto.sh" --verify
fi
```

항상 최신 checkout에 포함된 `layers.lock`으로 외부 의존성을 구성한다. 외부 저장소를 임의의 최신 HEAD로 올리거나 불일치하는 기존 설정을 자동 덮어쓰지 않는다.

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

## 3. 선택: 실제 이미지 빌드와 산출물

**환경만 준비하는 팀원은 이 절을 실행하지 않는다.** `--verify`에는 이미지 컴파일이 포함되지 않는다. 실제 빌드 담당자만 아래 절차를 수행한다. 처음 bring-up 이미지를 만들 때는 [커널 필수 옵션 검사](yocto-rpi4-xen-minimal-bringup.md#1-호스트에서-커널-필수-옵션-검사)를 먼저 확인한다.

설정 도구는 호출한 셸의 BitBake 환경을 유지하지 않으므로, 1절에서 지정한 프로젝트·작업 공간·빌드 경로 변수를 사용해 다시 source한다.

```bash
source "$YOCTO_WORKSPACE/layers/openembedded-core/oe-init-build-env" "$YOCTO_BUILD_DIR"

# 선택: 실행 계획만 확인. 실제 컴파일은 하지 않는다.
bitbake -n perseus-xen-bringup-image
```

실제 이미지를 생성하려면 아래에서 목적에 맞는 타깃을 선택한다.

| 명령 | 목적 |
| --- | --- |
| `bitbake xen-image-minimal` | 기본 Xen/dom0 이미지 생성 |
| `bitbake perseus-xen-bringup-image` | 진단 도구와 최소 테스트 domU 자산을 포함한 dom0 이미지 생성 |
| `bitbake perseus-domu-initramfs` | 최소 테스트 domU initramfs만 별도 생성 |

bring-up 이미지 타깃은 domU initramfs와 커널 deploy를 의존성으로 연결한다. 이 타깃을 만들기 위해 기본 dom0 이미지와 domU 타깃을 각각 먼저 빌드할 필요는 없다. 실제 빌드의 최종 종료 코드·오류·태스크 요약을 확인하고, 실패한 태스크의 로그를 보존한다. 기존 빌드 전체나 캐시를 삭제하지 않는다.

```bash
ls -lh "$YOCTO_BUILD_DIR/tmp/deploy/images/raspberrypi4-64/"
```

`bitbake -n`의 완료는 실행 계획 검사 결과다. 실제 이미지 생성과 실보드 부팅 결과는 각각 별도로 기록한다.

| 타깃 | 산출물 및 의미 |
| --- | --- |
| `xen-image-minimal` | `xen-image-minimal-raspberrypi4-64.rootfs.rpi-sdimg`, `.manifest`, `.ext3`, `.tar.bz2`, `.wic.bz2` 등. meta-virtualization의 기본 Xen/dom0 이미지 |
| `perseus-xen-bringup-image` | 같은 형식의 `perseus-xen-bringup-image-raspberrypi4-64.rootfs.*`. 기본 이미지를 확장한 별도 테스트 이미지 |
| `perseus-domu-initramfs` | `perseus-domu-initramfs-raspberrypi4-64.rootfs.cpio.gz` 및 `.manifest`. 디스크 없는 최소 테스트 게스트 rootfs |
| 공통 부트 자산 | `Image`, `xen-raspberrypi4-64`, `boot.scr`, `u-boot.bin`, Pi 4 DTB |

타임스탬프가 붙은 실제 파일과 위 이름의 심볼릭 링크가 함께 생성된다. 기본 `TMPDIR`, `DL_DIR`, `SSTATE_DIR`는 새 빌드 아래에 있으므로 기존 캐시와 분리된다. 대형 이미지·로그는 Git에 넣지 않고 별도 산출물 저장소에서 전달한다.

## 4. 현재 구성과 부트 체인

프로젝트 공통 정책은 `platform/yocto/config/local.conf.fragment`, 외부 레이어 버전은 `platform/yocto/layers.lock`, Xen·Linux·U-Boot의 소스 선택은 그 고정 레이어의 레시피가 기준이다. 레이어 등록과 설정 생성은 `scripts/setup-yocto.sh`가 담당한다. 문서에서 SHA나 공통 설정 전체를 복사해 별도로 유지하지 않는다. 과거에 확인한 구체적인 버전과 원본 로그는 감사 기록을 참고한다.

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

실제 커널 옵션 검사와 네트워크·VM 실행 명령은 [bring-up 검증 절차](yocto-rpi4-xen-minimal-bringup.md)에 모아 관리한다.

bring-up 이미지는 기존 설정대로 root 빈 비밀번호를 허용하고 SSH 도구를 포함한다. UART 진단용 설정이므로 배포 이미지에 적용하기 전에 인증 정책을 별도로 정해야 한다. 임시 게스트 PID 1은 셸 재시작 루프이며 정상 종료 처리는 미구현이다. `xl destroy`는 정상 종료 검증이 아니다. 보드 점검은 [bring-up 절차](yocto-rpi4-xen-minimal-bringup.md)를 따른다.

## 6. 재현성의 경계

잠금 SHA와 소스·설정으로 같은 빌드 입력을 구성하는 것, 캐시를 활용한 이미지 재생성, 캐시 없는 전체 컴파일, 이미지 바이트 동일성, 실제 부팅 성공은 각각 다른 검증이다. 수행한 수준만 감사 기록에 표시한다.

기본 설정은 캐시를 공유하지 않는다. 선택적으로 기존 캐시를 사용할 때는 새 빌드의 `conf/site.conf`에 읽기용 mirror를 지정할 수 있다. 개발자별 최적화이며 Git에 올리거나 환경 준비의 필수 조건으로 삼지 않는다.

```bitbake
# 실제 캐시 경로로 바꾼다. 새 빌드의 SSTATE_DIR는 그대로 유지한다.
SSTATE_MIRRORS = "file://.* file:///absolute/existing-build/sstate-cache/PATH"
```

패키지 공급자·소스 URL의 장기 가용성이나 모든 호스트 조합까지 검증한 것은 아니다. 완전한 오프라인 빌드가 필요하면 다운로드·sstate mirror를 별도로 운영해야 한다.

## 7. 환경 준비와 빌드 문제 해결

| 증상 | 확인할 내용 |
| --- | --- |
| GitLab 인증 실패 / 필요한 프로젝트 파일 없음 | 본인 접근 권한과 현재 통합 상태를 확인한다. 파일을 다른 버전에서 개별 복사하지 않는다. |
| `Permission denied`로 setup 실행 불가 | `bash scripts/setup-yocto.sh --verify`로 실행한다. |
| origin·HEAD 불일치 / `local changes` | 현재 `layers.lock`과 외부 checkout을 대조한다. 로컬 변경을 보존하고 필요하면 1절의 새 작업 공간을 사용한다. |
| `not owned` / `managed settings differ` | 기존 수동 conf를 덮어쓰지 않는다. 새 빌드 경로 또는 새 작업 공간에서 구성한다. |
| systemd 요구 / 커널 제공자 오류 | 현재 fragment·레이어가 적용됐는지 확인한다. 임시 설정을 local.conf에 중복 추가하지 않는다. |
| Xen 부트 스크립트 unpack 경로 오류 | meta-perseus의 U-Boot bbappend 등록과 `S == UNPACKDIR` 검증 결과를 확인한다. |
| `meta-virt-xen.inc` 중복 inclusion | local.conf에 해당 include를 수동으로 추가했는지 확인한다. |
| `Reconnecting to bitbake server` / 원본 URL 재시도 경고 | 문구만으로 성공·실패를 판정하지 않는다. 최종 종료 코드와 오류, 실패 태스크 로그를 확인한다. |
| WSL의 `systemd-binfmt.service` 오류 | `sudo dpkg --audit`, `sudo apt-get check`로 패키지 상태를 확인한다. QEMU 실행 관련 문제는 별도 조사한다. |
| 호스트 `CONFIG_TASK_IO_ACCOUNTING` / VHDX 경고 | 각각 호스트 I/O 통계와 저장 공간 관련 경고다. 최종 결과와 실제 디스크 여유를 함께 확인한다. |

커널 설정·SD 이미지·실보드 부팅·domU 통신 문제는 [bring-up 검증](yocto-rpi4-xen-minimal-bringup.md#6-실패할-때-먼저-볼-항목)을 참고한다. 캐시 없는 전체 컴파일·이미지 바이트 동일성·실보드 동작을 검증했다는 뜻으로 환경 준비 성공을 표현하지 않는다.
