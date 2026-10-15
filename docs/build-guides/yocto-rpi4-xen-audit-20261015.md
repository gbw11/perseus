# Yocto/Xen 환경 감사 기록 — 2026-10-15

## 감사 기준과 보존 범위

- Linux 환경: WSL2 `Ubuntu-24.04`, Ubuntu 24.04.5 LTS, x86_64.
- 프로젝트: `/home/ssafy/S15P31E110`, `feat/platform/yocto-build-setup`, 감사 시작 HEAD `87412cf382892bcc591880ce88dc82d17a716d00`. 시작 작업 트리는 깨끗했다.
- GitLab 작업 브랜치도 같은 HEAD였다. Windows 체크아웃의 `d5198c8`은 원격과 이력이 갈라져 있어 덮어쓰거나 병합하지 않았다. Linux 브랜치에서 작업했다.
- 기존 `/home/ssafy/yocto-workspace/build-rpi4-xen`, `build-rpi4-xen-repro`, 레이어·downloads·sstate·산출물은 삭제·이동·초기화하지 않는다.
- 별도 검증 경로: `/home/ssafy/yocto-repro-validation-20261015`. 원본 설정·주요 부트 자산·이미지의 변경 전 SHA-256은 그 아래 `logs/original.sha256`에 보존한다. 로그·이미지는 Git 추적 대상이 아니다.

## 외부 저장소 조사

다음 표는 감사 시점의 **관측 기록**이다. 버전을 변경·재구성할 때의 단일 기준은 계속 `platform/yocto/layers.lock`이다. 외부 소스를 프로젝트에 복사하거나 새 버전 관리 형식을 만들지 않았다.

| 저장소 | origin URL | 실제 HEAD = lock SHA | dirty |
| --- | --- | --- | --- |
| openembedded-core | https://git.openembedded.org/openembedded-core | `0f03c732a0a2d0506b41b1abe60b3f368b2300dc` | 없음 |
| bitbake | https://git.openembedded.org/bitbake | `51fd94a67fa17650a9ff623492005972bbc7d26f` | 없음 |
| meta-yocto | https://git.yoctoproject.org/meta-yocto | `7c25b11156553145fa6b241a2d4f65d94438f804` | 없음 |
| meta-raspberrypi | https://git.yoctoproject.org/meta-raspberrypi | `919588393f41b48cb6c5d832aa35e58a19b6762d` | 없음 |
| meta-virtualization | https://git.yoctoproject.org/meta-virtualization | `2807579615834a143c8ed453b463f93f43446019` | 없음 |
| meta-openembedded | https://git.openembedded.org/meta-openembedded | `47d7dbdd10890bbd429bc0cccbe14435c37e5af1` | 없음 |

각 저장소에서 `remote -v`, `rev-parse HEAD`, `status --porcelain=v1 --untracked-files=all`을 실행했다. lock 불일치는 없으며 SHA를 변경하지 않았다.

## 실제 사용 구성과 소스 누락 여부

### 환경 설정과 초기화

기존 `conf/bblayers.conf`는 core, meta-poky, meta-oe, meta-python, meta-networking, meta-filesystems, meta-raspberrypi, meta-virtualization, meta-perseus의 **9개 레이어**를 절대경로로 등록하고 있었다.

기존 `conf/local.conf`의 프로젝트 설정은 커밋된 fragment와 의미상 일치했다. MACHINE `raspberrypi4-64`, DISTRO `poky`, INIT_MANAGER `systemd`, `virtualization xen`, 커널 제공자 `linux-raspberrypi`, dom0 메모리 `1024M`, 빌드 병렬도 4다. 기존 설정 파일과 backup은 서버 로컬 파일이며 GitLab 소스 누락으로 판단하지 않았다. 새 경로에서는 fragment와 스크립트가 설정을 재생성한다.

`scripts/setup-yocto.sh`는 **GitLab에도 서버에도 이미 존재**했다. `platform/yocto/` 바로 아래에 없었던 이유는 루트 `scripts/`에서 관리하기 때문이다. 초기화는 외부 OE-core의 `oe-init-build-env`를 source하는 방식이다. 조사한 `.bashrc`·`.profile`에서 별도 Yocto 경로·환경 설정은 발견되지 않았다. 기존 conf의 backup·template 기록 외 추가 site/auto conf는 없었다.

기존 setup은 레이어를 순차 add한 뒤 fragment를 적용했고, 검증은 기본 이미지 위주였다. 새 setup은 레이어·설정을 모두 생성한 후 파싱하고 bring-up/domU 메타데이터까지 검사한다. 기존 수동 빌드 경로는 관리 블록이 없으므로 거부한다.

### 실제 빌드 증거

`build-rpi4-xen/tmp/log/cooker/raspberrypi4-64/` 아래 원본 기록:

| 로그 | 확인한 결과 |
| --- | --- |
| `20261012224906.log` | `xen-image-minimal` 최종 이미지 작업 포함, 6481 tasks / 4904 재실행 불필요 / all succeeded |
| `20261013142007.log` | `perseus-xen-bringup-image` 최종 이미지 작업 포함, 6584 tasks / 6479 재실행 불필요 / all succeeded |
| `20261013141458.log` | 과거 domU 라이선스 checksum 오류. 이후 populate_lic 및 이미지 완료 성공 로그가 있고 현재 레시피의 checksum은 외부 MIT 원본과 일치 |

기존 배포 디렉터리 `tmp/deploy/images/raspberrypi4-64/`에서 확인:

- 기본 Xen SD 이미지: `xen-image-minimal-raspberrypi4-64.rootfs-20261012224914.rpi-sdimg`, 3,305,111,552 bytes.
- bring-up SD 이미지: `perseus-xen-bringup-image-raspberrypi4-64.rootfs-20261013142012.rpi-sdimg`, 3,347,054,592 bytes.
- domU initramfs: `perseus-domu-initramfs-raspberrypi4-64.rootfs-20261013142012.cpio.gz`, 2,811,171 bytes.
- 각 manifest·testdata·ext3·부트 자산도 존재한다. testdata의 설정과 manifest에서 Poky 6.0.4, Linux 6.18.33+git, Xen tools 4.21+stable을 확인했다. U-Boot 작업·산출물은 2026.01이다.

로그의 meta-perseus Git 표시는 빌드 당시 `d0b0674`다. **그 시점에 새 레시피가 미커밋 상태였을 가능성**이 있으므로 그 Git 표시만으로 최종 커밋과 같다고 단정하지 않았다. 아래의 실제 unpack 입력 및 이미지 파일 비교를 추가 수행했다.

### 존재와 사용의 구분

- 자체 `perseus-bringup/files/` 6개와 `perseus-domu-init/files/` 2개 모두 기존 빌드의 unpack source와 바이트 단위로 일치했다.
- `debugfs`로 기존 bring-up ext3를 읽어 VM 설정·DHCP 설정·bridge 스크립트·진단 스크립트가 Git 원본과 일치함을 확인했다.
- systemd unit은 `@SBINDIR@ → /usr/sbin` 치환 결과와 일치했다. 네트워크·기본 dnsmasq의 자동 시작 링크는 없고 `serial-getty@hvc0` 링크는 존재했다.
- domU cpio archive의 `/init`·DHCP hook도 원본과 일치했다.
- dom0에 내장된 `/opt/perseus/bringup/Image` 및 ramdisk는 deploy 파일과 바이트 단위로 일치했다. 커널 SHA-256은 `a4baa7d3fd4f225d5a2591d39cf6c9e6da73f7811d716263b60e9cbd96569ac1`, ramdisk는 `318da45c2a94763364d1e060448410ff9f68bd50a18223b27d257293e2e10616`였다.
- 원본 커널 소스 Git 작업 트리는 깨끗했다. 실제 빌드 `.config`에서 XEN, XEN_DOM0, XEN_NETDEV_FRONTEND, HVC_XEN, BLK_DEV_INITRD, RD_GZIP, DEVTMPFS는 `y`, BRIDGE·XEN_NETDEV_BACKEND는 `m`였다. manifest에 bridge·netback 모듈이 포함되어 있었다.
- `boot.scr`의 실제 문자열에서 FAT의 `xen`·`Image` 적재, `dom0_mem=1024M`, `console=hvc0`, 두 번째 파티션 PARTUUID rootfs를 확인했다.

따라서 단순 초안 파일만 있는 상태가 아니라 **이미지 생성 및 자산 포함 증거가 있는 상태**다. 조사 범위에서 프로젝트에 반영되지 않은 자체 소스·패치는 발견되지 않았다. **실보드 부팅·xl·DHCP 통신 성공을 입증하는 실행 로그는 확보하지 못했다.**

## GitLab 관리 경계와 변경 사항

직접 관리: 기존 자체 레시피·bbappend·init/네트워크/VM 설정 원본, 공통 fragment, 단일 layers.lock, 설정·검사 스크립트, 회귀 테스트, 재구성·감사·인계 문서.

외부 의존성: 기존 6개 저장소를 URL·정확한 SHA로 유지. 버전을 올리지 않았다.

추가/보완: `scripts/setup-yocto.sh`, `tests/platform/test_yocto_setup.py`, `.gitignore`, `.gitattributes`, 본 감사 기록과 재구성 안내, README 링크, 기존 bring-up 문서의 오래된 상태 및 ZIP 설치 안내, fragment/lock 설명 주석. **이미지 레시피·부팅·VM/네트워크 동작은 변경하지 않았다.**

빌드 산출물·외부 레이어·tmp·downloads·sstate·로그·개인 인증 파일은 일반 Git에서 제외한다. ignore 검사에서 레시피·fragment·lock·스크립트는 계속 추적 가능함을 확인했다. 인증정보 패턴 검사는 값 대신 발견 여부만 출력한다.

## 새 경로 사전 검증

검증 경로에 GitLab 브랜치를 Clone한 다음 커밋 전 변경안의 setup·lock·fragment·ignore/attributes만 복사하여 다음을 실행했다. 최종 순수 GitLab Clone 검증은 별도 절에 기록한다. 외부 레이어 6개는 **원본 작업 공간을 참조하거나 복사하지 않고 각 URL에서 새로 Clone**했다.

```bash
BASE=/home/ssafy/yocto-repro-validation-20261015
cd "$BASE/S15P31E110"

bash -n scripts/setup-yocto.sh scripts/check-xen-bringup-prereqs.sh
YOCTO_WORKSPACE="$BASE/workspace" \
YOCTO_BUILD_DIR="$BASE/workspace/build-verify" \
  bash scripts/setup-yocto.sh --verify

source "$BASE/workspace/layers/openembedded-core/oe-init-build-env" \
  "$BASE/workspace/build-verify"
bitbake -n xen-image-minimal perseus-xen-bringup-image
```

| 검사 | 관측 결과 |
| --- | --- |
| 잠금/외부 소스 | 6개 URL·전체 SHA·clean 검사 통과 |
| 새 설정 구성 | 원본 `/home/ssafy/yocto-workspace` 참조 없이 현재 Clone과 새 레이어 경로로 생성 |
| `bitbake-layers show-layers` | core 포함 9개, 새 Clone의 meta-perseus 활성화 |
| `bitbake -p` | 3212개 레시피 파싱, 5502 targets, 173 skipped, 0 masked, **0 errors** |
| U-Boot append·이미지 3종 `-e` | bbappend 등록, S=UNPACKDIR, MACHINE/DISTRO/init/kernel/dom0 메모리 및 산출물 경로 확인 |
| `bitbake -n` 두 dom0 타깃 | 6608개 태스크 실행 계획 완료. **실제 컴파일 성공 증거로 사용하지 않음** |
| setup 반복 실행 | local.conf·bblayers.conf SHA-256 동일 |
| 관리 블록 충돌 | 별도 conf 복사본의 레이어 블록을 바꾸자 실패. local.conf·bblayers.conf 둘 다 덮어쓰지 않음 |
| `python3 -m unittest discover -s tests/platform -v` | fixture 회귀 테스트 **8개 통과**. clean·missing·dirty·HEAD·remote·중복 lock·기존 conf·인자 검사 |
| 추가 실패 동작 | 원본 빌드 경로, 없는 소스의 `--check`, 잘못된 인자·공백 경로 거부. 원본 conf 보존 |
| `git check-ignore -v` / `git check-attr` | 산출물·캐시·인증 파일 제외, 소스는 추적 가능, shell/Yocto 메타데이터 LF |
| 인증정보/크기 검사 | 접근 가능한 Git 이력 blob 29개 및 변경 포함 소스 35개에 고신뢰도 토큰·개인키·URL 자격증명 패턴/1MiB 초과 파일 발견 없음. 모든 형태의 비밀 부재를 증명하는 검사는 아님 |
| `git diff --check` / 문서 링크 검사 | 공백 오류 없음, 저장소 내부 Markdown 링크 존재 |

새 환경 `bitbake -e`로 확인한 주요 소스 고정값:

| 레시피 | PV / 소스 리비전 |
| --- | --- |
| linux-raspberrypi | `6.18.33+git`, `KBUILD_DEFCONFIG=bcm2711_defconfig`, `SRCREV_machine=95b85bebbedcaedfa7ca79116ed38b7376fba412`, `SRCREV_meta=6d012fbc35201ba081efcc19c9519c1dc7b64c43` |
| xen | `4.21.0+stable`, `SRCREV=1c72306b1f0b4dc533954f678361044f8083dccc` |
| u-boot | `2026.01`, `SRCREV=127a42c7257a6ffbbd1575ed1cbaa8f5408a44b3` |

커널은 복수 SRCREV를 사용하므로 대표 `SRCREV="INVALID"`만 보고 비고정 버전이라고 판정하면 안 된다. 위 machine/meta 값이 실제 고정 입력이다. 이 표는 관측 기록이며 별도 pin 파일이 아니다.

검증 로그는 `$BASE/logs/setup-verify.log`, `setup-repeat.log`, `dry-run.log`, `selected-versions.txt`에 남긴다. WSL VHDX 최적화 경고가 있었으나 파싱 오류는 없었다.

## 이미지 빌드 시도와 최종 검증 범위

사전 환경 검증 후 새 빌드의 §conf/site.conf§에만 다음 읽기용 mirror를 추가하고 §bitbake xen-image-minimal perseus-xen-bringup-image§를 시작했다.

§§§bitbake
SSTATE_MIRRORS = "file://.* file:///home/ssafy/yocto-workspace/build-rpi4-xen/sstate-cache/PATH"
§§§

§Sstate summary: Wanted 3608 Local 0 Mirrors 3587 Missed 21 Current 0 (99% match, 0% complete)§를 확인했다. 이후 사용자가 **실제 빌드 대신 빌드 가능한 환경의 검증까지만 수행**하도록 범위를 조정하여, 이 새 빌드 프로세스에만 SIGINT를 보내 중단했다. 로그 §logs/image-build.log§와 새 작업 공간의 부분 상태는 보존했다. 중단 종료 코드를 이미지 빌드 성공으로 계산하지 않았다.

따라서 **이번 변경으로 새 환경에서 이미지 생성이 완료되었다고 주장하지 않는다.** 검증 완료 범위는 잠금 소스 확보·설정 재생성·9개 레이어 등록·전체 파싱·이미지 3종 메타데이터·두 dom0 타깃 dry-run·보호 동작이다. 기존 경로에서의 이미지 생성 성공은 위 원본 로그·산출물 조사 결과로만 기록한다.

실제 빌드 시도에는 로컬 hash equivalence와 sstate mirror 조합 경고 및 호스트 §CONFIG_TASK_IO_ACCOUNTING§ 경고가 있었다. mirror는 99%를 찾았으므로 경고만 보고 실패라고 판정하지 않았으며, 공통 설정을 임의 변경하지 않았다. cache mirror는 환경 준비의 필수 입력이 아니다.

원본 §local.conf§·§bblayers.conf§, 부트 스크립트·커널·Xen, SD 이미지 2개, domU ramdisk의 SHA-256 재검사는 모두 일치했다. 원본 외부 저장소 6개의 SHA와 clean 상태도 다시 확인했다.

## 인계 및 미검증 항목

- 재구성·빌드 타깃·산출물·VM 연결·수정 위치는 [재구성 및 domU 인계](yocto-rpi4-xen-reproduce.md)를 따른다.
- 현재 domU는 디스크 없는 최소 initramfs 테스트 게스트다. 완성형 Debian/Ubuntu/Alpine 템플릿, 앱·Agent 통합, 영구 저장장치·정상 종료 처리는 미구현이다.
- 커널 configure를 새로 수행한 결과와 원본 §.config§ 전체의 비교, cache 없는 전체 컴파일, 새 SD 이미지 완성, 이미지 바이트 재현성은 이번 범위에서 확인하지 않았다.
- 실보드 UART 부팅·Xen/dom0 실행·xl 명령·domU 부팅·Bridge/DHCP 통신·HDMI는 미검증이다.
- 외부 URL의 미래 가용성, 완전한 오프라인 빌드, 다른 호스트 배포판 조합은 별도 검증이 필요하다.

## 최종 GitLab 순수 Clone 검증

구현·문서 커밋 `e455397`을 작업 브랜치에 일반 push한 뒤,
`/home/ssafy/yocto-repro-final-20261015/S15P31E110`에 GitLab URL에서 **다시 Clone**했다.
최종 Clone에는 소스·설정 파일을 수동 복사하지 않았고 모든 Git 추적 파일의 바이트가
푸시한 원본과 동일함을 확인했다. 외부 의존성은 사전 검증에서 URL로 새로 Clone한
6개 저장소를 재사용했으며 기존 `/home/ssafy/yocto-workspace/layers`는 사용하지 않았다.

```bash
cd /home/ssafy/yocto-repro-final-20261015/S15P31E110
python3 -m unittest discover -s tests/platform -v
YOCTO_WORKSPACE=/home/ssafy/yocto-repro-validation-20261015/workspace \
YOCTO_BUILD_DIR=/home/ssafy/yocto-repro-final-20261015/build-rpi4-xen \
  bash scripts/setup-yocto.sh --verify
git status --short
```

결과: **종료 코드 0**, 9개 레이어, 3212개 레시피 파싱 오류 0건,
이미지 3종의 MACHINE/DISTRO/init/kernel/dom0 메모리·산출물 경로 및 U-Boot append 검증 통과,
회귀 테스트 8개 통과, Clone 작업 트리 clean.
새 conf에는 원본 작업 공간 경로가 없고 site.conf·auto.conf·cache mirror도 없다.
최종 검증은 환경·메타데이터 검사만 수행했으며 실제 이미지 빌드는 실행하지 않았다.

로그: `/home/ssafy/yocto-repro-final-20261015/logs/setup-verify.log`, `tests.log`.
이 결과를 추가하는 후속 커밋은 감사 문서만 변경한다.
