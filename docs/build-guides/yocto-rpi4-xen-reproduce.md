> **리브랜딩 이전 보존 문서입니다.** 아래 본문은 당시 Perseus 소스·산출물·검증 기록이며, 현재 명령과 경로는 [Xenova 플랫폼 가이드](xenova-platform.md)를 따릅니다.

# Raspberry Pi 4 Yocto/Xen 환경 재구성 및 domU 인계

**이 문서가 팀원의 환경 준비 시작점이다.** GitLab에서 Clone한 최신 프로젝트와 그 안의 `platform/yocto/layers.lock`을 출발점으로 **새 Linux 작업 경로**에 환경을 준비한다. 문서화된 호스트 패키지·GitLab 인증·외부 소스 다운로드를 위한 네트워크는 필요하다. 기존 서버의 `conf`, 외부 레이어 복사본, 개인 스크립트나 ZIP은 필요하지 않다.

**팀 배포 전 조건:** 이 안내서·설정 도구·자체 레시피가 GitLab의 기본 브랜치에 함께 통합되어 있어야 아래의 일반 Clone으로 같은 구성을 받는다. 기본 브랜치에 아직 없는 파일은 담당자가 먼저 통합해야 한다. 팀원에게 특정 기능 브랜치나 프로젝트 SHA를 고정해서 안내하지 않는다.

**현재 검증 범위:** 이미 설치된 WSL2 Ubuntu 24.04에서 GitLab Clone, 새 Linux 작업 경로의 설정 생성과 BitBake 파싱을 확인했다. Windows 11 초기 상태에서 WSL 설치·재부팅·계정 생성·개인 토큰 발급까지 처음부터 실행한 검증은 아직 하지 않았다.

| 하려는 작업 | 읽을 문서 / 범위 |
| --- | --- |
| 처음 환경 준비 | 이 문서 1-1~1-6. `--verify` 완료 후 종료 |
| 처음 이미지 생성 | 이 문서 1-1~1-6 → 3-1~3-3 순서. 커널 검사까지 이 문서에서 실행 |
| 기존 Clone 갱신·domU 통합 개발 | 1절의 갱신 안내, 4~6절의 구성·통합 계약 |
| 생성 이미지·실보드 검증과 추가 커널 진단 | [최소 bring-up 검증](yocto-rpi4-xen-minimal-bringup.md) |
| 과거에 확인한 환경·성공 로그·한계 조회 | [2026-10-15 감사 기록](yocto-rpi4-xen-audit-20261015.md). 현재 실행 절차는 이 문서를 따름 |


프로젝트는 팀이 사용하는 통합 브랜치의 최신 버전을 받는다. 스크립트·자체 레시피·공통 설정·잠금 파일은 같은 checkout의 파일을 함께 사용한다. 팀원마다 환경 구성 스크립트를 따로 만들거나 복사할 필요는 없다.

외부 레이어는 **현재 프로젝트의 `layers.lock`에 기록된 정확한 커밋**을 사용한다. 프로젝트의 최신 버전을 받는 것과 외부 의존성을 고정하는 것은 함께 유지한다. 감사 기록의 프로젝트 SHA는 과거 검증 대상을 식별하는 기록이며, 아래 실행 절차에서 checkout할 버전이 아니다.

## 1. 호스트 준비와 Clone

감사한 호스트는 Ubuntu 24.04.5 LTS / x86_64 / WSL2, Python 3.12.3, Git 2.43.0이다. Linux 파일시스템을 사용한다. `/mnt/c`의 프로젝트·빌드 경로 및 공백·따옴표가 있는 경로는 설정 도구가 거부한다. 현재 병렬 작업 설정은 4개다.

환경 준비와 실제 이미지 빌드의 자원 요구를 구분한다. [Yocto 6.0 공식 자원 안내](https://docs.yoctoproject.org/6.0/ref-manual/system-requirements.html)는 `core-image-sato/qemux86-64` 참고 이미지에 여유 디스크 140GB, RAM 32GB / CPU 4코어를 제시한다. 이 숫자는 Raspberry Pi Xen 이미지의 실측 최소 요구값이 아니다. 이 프로젝트의 새 환경 전체 빌드에 필요한 최대 디스크·메모리는 아직 측정하지 않았으므로, `--verify` 통과만으로 전체 빌드 자원이 충분하다고 판정하지 않는다.

### 1-1. Windows 11에서 WSL 설치 — 관리자 PowerShell

Windows 11 x64 PC, 관리자 권한, 인터넷 연결과 GitLab 프로젝트 읽기 권한을 준비한다. Clone 전에는 Windows 브라우저에서 GitLab에 로그인해 이 문서를 읽는다. 아래 `powershell` 블록은 Windows PowerShell에서, `bash` 블록은 Ubuntu 터미널에서 실행한다. 한 블록이 실패하면 오류를 해결한 뒤 다음 단계로 간다.

시작 메뉴에서 **PowerShell → 관리자 권한으로 실행**을 선택하고 다음을 실행한다. 이미 Ubuntu 24.04 / WSL2가 설치되어 있으면 1-2로 간다.

```powershell
wsl --install -d Ubuntu-24.04
```

설치가 끝나면 작업을 저장하고 **Windows를 재부팅**한다. 재부팅 후 시작 메뉴에서 **Ubuntu 24.04**를 실행해 최초 압축 해제를 기다린다.

- Linux 사용자 이름을 입력한다. 예: `perseus`. Windows 계정명·GitLab 계정명과 같을 필요는 없다.
- Linux 비밀번호를 두 번 입력한다. 입력 중 글자나 `*`가 보이지 않는 것이 정상이다.
- 이 비밀번호는 이후 Ubuntu의 `sudo` 명령에서 사용한다. GitLab 인증과는 별개다.
- `사용자@PC:~$` 형태의 프롬프트가 나오면 Linux 계정 생성이 끝난 것이다.

설치·재부팅·계정 생성은 [Microsoft WSL 설치 안내](https://learn.microsoft.com/en-us/windows/wsl/install)와 [최초 사용자 설정](https://learn.microsoft.com/en-us/windows/wsl/setup/environment#set-up-your-linux-username-and-password)을 따른다.

### 1-2. WSL2 확인 후 Ubuntu로 진입 — PowerShell → Bash

Windows에서 새 PowerShell 창을 열어 확인한다. 기존 WSL을 업데이트할 때는 실행 중인 작업을 먼저 마친다.

```powershell
wsl --update
wsl -l -v
wsl --status
```

`wsl -l -v`에서 `Ubuntu-24.04` 행의 `VERSION`이 **2**여야 한다. `STATE`가 `Stopped`여도 설치 오류가 아니다. 신규 설치인데 `VERSION`이 1이면 PowerShell에서 `wsl --set-version Ubuntu-24.04 2`를 실행한 뒤 다시 확인한다. 이는 Ubuntu 버전(24.04)이나 WSL 프로그램 버전과 별개의 값이다. `wsl --status`의 WSL1 미지원 안내가 있더라도 이 문서의 대상인 WSL2 행과 Ubuntu 실행이 정상이면 진행할 수 있다. [WSL 기본 명령](https://learn.microsoft.com/en-us/windows/wsl/basic-commands)

이제 같은 PowerShell에서 Ubuntu로 들어간다.

```powershell
wsl -d Ubuntu-24.04
```

**이후부터는 Ubuntu Bash에서 실행한다.** PowerShell에서 진입하면 현재 경로가 `/mnt/c/windows/system32` 등일 수 있으므로 먼저 홈으로 이동한다.

```bash
cd "$HOME"
whoami
pwd
cat /etc/os-release
uname -m
nproc
free -h
df -h "$HOME"
```

확인 기준은 일반 Linux 사용자(`root` 아님), `/home/<Linux사용자>` 경로, Ubuntu `VERSION_ID="24.04"`, `x86_64`다. 이 안내의 검증 대상은 이 조합이다. 프로젝트·외부 소스·빌드는 모두 이 Linux 파일시스템 아래에 만든다. `/mnt/c`에 Clone하지 않는다. WSL 내부 여유 공간 외에 Windows의 해당 드라이브 여유 공간도 확인한다.

WSL 자원 상한 조정은 선택이다. 기존 32GB RAM PC에서 `%USERPROFILE%\.wslconfig`의 `[wsl2]` 절 아래 `memory=20GB`, `swap=8GB`를 사용한 사례가 있으며 필수값은 아니다. 자원 조정 없이 먼저 환경 준비를 진행할 수 있다.

### 1-3. Linux 호스트 패키지 설치 — Ubuntu Bash

아래 `sudo` 비밀번호에는 1-1에서 만든 **Linux 비밀번호**를 입력한다. 입력이 보이지 않는 것이 정상이다. 기존 Ubuntu 호스트에서는 이미 설치된 패키지를 다시 준비할 필요가 없다. 설정 도구는 시스템 패키지나 인증 설정을 설치·변경하지 않는다.

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
git --version
python3 --version
locale charmap
```

`apt`가 오류 없이 끝나고 Git·Python 버전, `UTF-8`이 출력되는지 확인한다. 새 터미널에서는 `LANG`·`LC_ALL` export를 다시 실행한다. 실제 감사한 버전은 Git 2.43.0, Python 3.12.3이다.

### 1-4. GitLab 읽기 인증 준비 — Windows 브라우저

새 WSL의 Git 인증은 별도로 준비한다. Windows 브라우저에 로그인되어 있다는 사실만으로 Ubuntu의 `git clone` 인증이 끝난 것은 아니다. 이미 Ubuntu에서 프로젝트 Clone이 가능한 인증을 구성했다면 다음으로 간다.

새 환경에서는 HTTPS용 **개인 액세스 토큰(PAT)**을 사용할 수 있다.

1. Windows 브라우저에서 [프로젝트](https://lab.ssafy.com/s15-final/S15P31E110)에 로그인하고 저장소가 보이는지 확인한다. 보이지 않으면 프로젝트 접근 권한을 먼저 받는다.
2. 프로필의 **Edit profile / Preferences → Access Tokens / Personal access tokens**에서 토큰 생성 화면을 연다. 서버 버전에 따라 메뉴 이름은 다를 수 있다.
3. 구분 가능한 이름(예: `yocto-read`), 만료일, `read_repository` 범위를 지정해 생성한다. 이 문서의 Clone·갱신에는 저장소 읽기 권한을 사용한다.
4. 생성된 토큰을 개인 비밀 저장소에 보관한다. 아래 Clone에서 `Username` 질문에는 GitLab 사용자 이름, `Password` 질문에는 **계정 비밀번호 대신 토큰**을 입력한다. 토큰 입력도 화면에 표시되지 않는다.

토큰을 명령어·URL·문서·Git 설정에 직접 넣지 않는다. 아래 예시는 인증 값을 저장하지 않으므로 이후 `git pull`에서 다시 물을 수 있다. 토큰 발급 메뉴가 없거나 정책상 막혔다면 프로젝트 관리자가 승인한 인증 방식을 준비해야 한다. 서버의 개인 토큰 허용 정책과 팀원별 발급 과정은 이 작업에서 확인하지 않았다. [GitLab HTTPS 인증 안내](https://docs.gitlab.com/user/profile/personal_access_tokens/#clone-repository-using-personal-access-token), [토큰 범위](https://docs.gitlab.com/security/tokens/access_token_scopes/)

### 1-5. 최신 프로젝트 Clone 및 파일 확인 — Ubuntu Bash

아래 명령은 GitLab에서 지정한 **기본 브랜치의 최신 버전**을 받는다. 1절 앞의 팀 배포 조건을 먼저 충족해야 한다. 새 경로에서 실행하며, 같은 이름의 Clone이 이미 있으면 아래의 기존 Clone 갱신 절차를 사용한다.

```bash
mkdir -p "$HOME/perseus-repro"
cd "$HOME/perseus-repro"

git clone https://lab.ssafy.com/s15-final/S15P31E110.git
```

Clone이 성공한 뒤에만 다음 블록으로 넘어간다. 인증 실패나 `destination path ... already exists`를 무시하고 진행하지 않는다.

```bash
cd "$HOME/perseus-repro/S15P31E110"
export PERSEUS_PROJECT="$PWD"
git status --short --branch

for file in \
  docs/build-guides/yocto-rpi4-xen-reproduce.md \
  platform/scripts/setup-yocto.sh \
  platform/yocto/layers.lock \
  platform/yocto/config/local.conf.fragment \
  platform/yocto/meta-perseus/recipes-core/images/perseus-xen-bringup-image.bb \
  platform/yocto/meta-perseus/recipes-core/images/perseus-domu-initramfs.bb
do
  if test -f "$file"; then
    printf '[OK] %s\n' "$file"
  else
    printf '[MISSING] %s\n' "$file"
  fi
done
```

모든 파일이 `[OK]`이고 새 Clone에 미커밋 변경이 없어야 한다. `[MISSING]`이 하나라도 나오면 **여기서 중단**하고 기본 브랜치의 통합 상태를 확인한다. 다른 브랜치에서 파일을 개별 복사하지 않는다.

### 1-6. Yocto 빌드 도구·레이어 준비 및 검증 — Ubuntu Bash

**이 프로젝트에서 Yocto 환경을 준비한다는 것은 BitBake와 메타데이터 레이어를 잠금 버전으로 확보하고 빌드 설정을 생성하는 것이다.** BitBake는 레시피를 읽어 소스 다운로드·크로스 컴파일·이미지 생성을 실행하는 도구다. 1-3의 `apt` 명령은 이 도구들을 실행할 Ubuntu 호스트 패키지를 설치한다. 프로젝트에서 사용할 BitBake·OE-Core·레이어는 아래 `setup-yocto.sh --verify`가 준비한다.

| 준비 대상 | 담당 단계 |
| --- | --- |
| BitBake·OE-Core·meta-yocto·meta-raspberrypi·meta-openembedded·meta-virtualization | setup이 `layers.lock`의 URL·SHA로 `$YOCTO_WORKSPACE/layers/`에 확보 |
| MACHINE·DISTRO·자체 meta-perseus 활성화 | setup이 `oe-init-build-env`를 사용하고 `local.conf`·`bblayers.conf` 생성 |
| BitBake 실행 환경(PATH 등) | 실제 빌드 직전에 3-1의 `source oe-init-build-env`로 현재 셸에 활성화 |
| Linux·Xen·U-Boot 및 이미지 패키지 소스 | 3-2의 BitBake 태스크가 고정 레이어의 레시피에 따라 추가 다운로드·빌드 |

별도의 Yocto 설치 파일이나 이전 개발자의 SDK를 전달받는 단계는 없다. BitBake는 잠금 파일로 받은 사본을 사용한다.

같은 Ubuntu 터미널에서 새 작업 경로를 지정한다. 이 예시는 프로젝트 옆에 별도 작업 공간을 만들며 기존 `~/yocto-workspace`를 사용하지 않는다.

```bash
export YOCTO_WORKSPACE="$HOME/perseus-repro/workspace"
export YOCTO_BUILD_DIR="$YOCTO_WORKSPACE/build-rpi4-xen"
if bash "$PERSEUS_PROJECT/scripts/setup-yocto.sh" --verify; then
  printf '\n환경 준비 검증 완료. 이미지 빌드는 실행하지 않았습니다.\n'
else
  printf '\n환경 준비 실패. 위 오류를 해결한 뒤 같은 명령을 다시 실행하세요.\n' >&2
fi
```

최초 실행에는 외부 저장소 6개의 다운로드가 포함되므로 네트워크가 필요하다. `--verify`는 소스 확보·설정 생성·메타데이터 검증만 수행하며 이미지를 컴파일하지 않는다.

완료 판단은 다음과 같다.

| 확인 지점 | 통과 기준 |
| --- | --- |
| 외부 저장소 | 6개 저장소의 URL·정확한 잠금 SHA·깨끗한 작업 트리 확인 |
| 레이어 | `show-layers`에 `meta-perseus`를 포함한 9개 레이어 표시 |
| 파싱·이미지 설정 | `bitbake -p`와 이미지 3종의 설정 검사에서 오류 없이 통과 |
| 스크립트 종료 | `Setup complete (no image build or boot test performed)`와 위 블록의 `환경 준비 검증 완료` 출력 |

**환경만 준비하는 팀원은 여기서 멈춘다. 처음 이미지를 만드는 팀원은 3절로 바로 이동한다.** 아래의 기존 Clone 갱신·작업 공간 교체와 2절은 최초 빌드의 추가 실행 단계가 아니다. `Baseline dom0: bitbake ...` 등의 마지막 출력은 이후 빌드 담당자를 위한 안내이며 자동 실행 결과가 아니다. 아직 SD 이미지나 커널 산출물이 없어도 정상이다. `tmp/deploy/images/` 확인은 실제 빌드 뒤에 한다.

터미널을 닫았다면 다시 `wsl -d Ubuntu-24.04`로 들어와 `cd "$HOME/perseus-repro/S15P31E110"`, `PERSEUS_PROJECT`·`YOCTO_WORKSPACE`·`YOCTO_BUILD_DIR`와 locale의 export를 다시 지정하면 된다. 매번 Clone할 필요는 없다.

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
bash platform/scripts/setup-yocto.sh --verify
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

`platform/scripts/setup-yocto.sh`는 이미 존재한다. `platform/yocto/` 아래에 동일한 도구를 새로 만들지 않는다.

- `layers.lock`의 저장소 이름 6개, 공개 HTTPS URL, 40자리 SHA와 중복을 먼저 검사한다.
- 소스는 `workspace/layers/<repository>`에 Clone하고 정확한 SHA로 detached checkout한다. `wrynose`/`2.18`은 이력을 받는 브랜치이며 **버전 기준은 잠금 SHA**다.
- 기존 checkout은 remote URL·HEAD·dirty 상태를 검사하고 자동 checkout, reset, pull, 패치를 하지 않는다. 다르면 중단한다.
- 새 빌드를 초기화하고 fragment와 9개 레이어 경로를 관리 블록으로 작성한다. 경로는 현재 Clone 및 작업 공간에서 계산한다.
- 모든 레이어와 MACHINE/DISTRO 설정을 준비한 다음 BitBake를 실행한다.
- `--verify`는 `show-layers`, `bitbake -p`, U-Boot bbappend·`S == UNPACKDIR`, 이미지 3종의 주요 설정을 검사한다.
- 기본 실행도 `--verify`도 이미지를 빌드하지 않는다. `--check`는 **기존 외부 소스만 검사**하며 Clone이나 설정 생성을 하지 않는다.

```bash
bash platform/scripts/setup-yocto.sh --help
bash platform/scripts/setup-yocto.sh --check
python3 -m unittest discover -s platform/tests -v
```

재실행 시 관리 블록은 일치해야 한다. 기존 수동 빌드나 이전 도구 버전의 설정은 자동 전환하지 않는다. `not owned` 또는 `managed settings differ`이면 기존 디렉터리를 보존하고 새 빌드 디렉터리를 선택한다. fragment를 바꿀 때에도 기존 설정을 자동 덮어쓰지 않는다.

## 3. 선택: 실제 이미지 빌드와 산출물

**이 절은 실제 이미지를 만드는 팀원만 실행한다.** 1-6의 `--verify` 성공 후 아래 3-1 → 3-2 → 3-3을 순서대로 따른다. 최초 타깃은 `perseus-xen-bringup-image`로 정한다. 커널 설정 생성·필수 옵션 검사·이미지 생성 명령을 여기에 모았으므로 빌드를 위해 다른 문서로 이동할 필요가 없다.

### 3-1. 현재 셸에서 Yocto 환경 활성화 — Ubuntu Bash

1절과 같은 터미널이면 기존 경로 변수를 유지한다. 새 터미널이면 아래 기본 경로를 사용한다. 1절에서 다른 경로로 준비했다면 먼저 세 변수에 자신의 실제 경로를 지정한다. 설정 도구는 실행한 자식 셸 안에서 초기화하므로, 빌드를 시작할 현재 셸에서도 `source`가 필요하다.

```bash
export PERSEUS_PROJECT="${PERSEUS_PROJECT:-$HOME/perseus-repro/S15P31E110}"
export YOCTO_WORKSPACE="${YOCTO_WORKSPACE:-$HOME/perseus-repro/workspace}"
export YOCTO_BUILD_DIR="${YOCTO_BUILD_DIR:-$YOCTO_WORKSPACE/build-rpi4-xen}"
export LANG=en_US.UTF-8
export LC_ALL=en_US.UTF-8

source "$YOCTO_WORKSPACE/layers/openembedded-core/oe-init-build-env" "$YOCTO_BUILD_DIR"
command -v bitbake
pwd
```

`bitbake` 경로가 잠금 버전으로 받은 `layers/bitbake/bin/bitbake`이고, 현재 디렉터리가 `YOCTO_BUILD_DIR`인지 확인한다. `source`가 실패하면 다음 블록으로 진행하지 않는다.

### 3-2. 커널 설정 생성 → 검사 → bring-up 이미지 생성

다음은 **실제 태스크 실행**이다. 커널 configure에도 소스 다운로드와 의존 도구 빌드가 발생할 수 있으며 전체 이미지 빌드는 오래 걸린다. 아래 블록은 앞 명령이 성공해야 다음 명령을 실행한다. 커널 configure나 필수 옵션 검사가 실패하면 이미지 빌드를 시작하지 않는다.

```bash
bitbake -c configure linux-raspberrypi &&
bash "$PERSEUS_PROJECT/scripts/check-xen-bringup-prereqs.sh" &&
bitbake perseus-xen-bringup-image &&
printf '\n[OK] Bring-up image build completed.\n'
```

커널 설정은 현재 빌드의 `B/.config`에 생성된다. 이전 성공 빌드 디렉터리는 필요하지 않다. 이미 configure된 커널이라면 BitBake가 태스크 상태를 확인한다.

| 필수 심볼 | 필요값 | 목적 |
| --- | --- | --- |
| `CONFIG_XEN`, `CONFIG_XEN_DOM0` | `y` | Xen 기반 Linux |
| `CONFIG_XEN_NETDEV_FRONTEND` | `y` | 최소 initramfs가 dom0 커널을 게스트용으로 재사용 |
| `CONFIG_HVC_XEN` | `y` | 게스트 hvc0 콘솔 |
| `CONFIG_XEN_NETDEV_BACKEND`, `CONFIG_BRIDGE` | `y` 또는 `m` | dom0 VIF·브릿지 |
| `CONFIG_BLK_DEV_INITRD`, `CONFIG_RD_GZIP`, `CONFIG_DEVTMPFS` | `y` | 게스트 initramfs 실행 |

`[BLOCKER]`가 나오면 출력된 심볼과 현재 커널 설정을 담당자에게 전달하고 원인을 해결한다. `=m`인 기능은 최종 이미지에 대응 모듈 패키지도 포함되어야 한다. 이 검사는 커널 설정 조건을 확인하며 실보드 부팅 결과를 대신하지 않는다.

bring-up 타깃은 기본 Xen/dom0 이미지 구성을 상속하고, 최소 domU initramfs와 커널 deploy를 의존성으로 연결한다. 기본 이미지와 domU 타깃을 각각 먼저 빌드할 필요는 없다.

BitBake의 최종 태스크 요약이 성공이고 `[OK] Bring-up image build completed.`가 출력된 경우에만 3-3으로 진행한다. 실패하면 오류에 표시된 태스크 로그를 보존하고 원인을 해결한 뒤 이 블록을 다시 실행한다. 기존 빌드 전체나 캐시를 삭제하지 않는다.

### 3-3. 생성된 이미지 확인

```bash
IMG_DIR="$YOCTO_BUILD_DIR/tmp/deploy/images/raspberrypi4-64"
ls -lhL \
  "$IMG_DIR/perseus-xen-bringup-image-raspberrypi4-64.rootfs.rpi-sdimg" \
  "$IMG_DIR/perseus-xen-bringup-image-raspberrypi4-64.rootfs.manifest" \
  "$IMG_DIR/perseus-domu-initramfs-raspberrypi4-64.rootfs.cpio.gz"
```

SD 이미지, 설치 패키지 manifest, 최소 domU ramdisk가 모두 있어야 한다. 이미지 생성 이후의 FAT·rootfs 검사, SD 기록·UART·보드 검증은 [생성 이미지와 실보드 검증](yocto-rpi4-xen-minimal-bringup.md#2-호스트에서-생성-이미지-정적-검사)을 따른다. 이미지 생성 완료와 보드 부팅 성공은 별도로 기록한다.

### 참고: 다른 타깃과 산출물

처음 빌드하는 팀원은 위 3-1~3-3으로 충분하다. 아래 명령은 다른 목적의 참고용이며 모두 추가 실행하는 순서가 아니다. 활성화된 같은 빌드 환경에서 사용한다.

| 명령 | 목적 |
| --- | --- |
| `bitbake xen-image-minimal` | 기본 Xen/dom0 이미지만 생성 |
| `bitbake perseus-domu-initramfs` | 최소 테스트 domU initramfs만 별도 생성 |
| `bitbake -n perseus-xen-bringup-image` | 실행 계획만 검사. 실제 이미지 생성 성공을 뜻하지 않음 |

| 타깃 | 산출물 및 의미 |
| --- | --- |
| `xen-image-minimal` | `xen-image-minimal-raspberrypi4-64.rootfs.rpi-sdimg`, `.manifest`, `.ext3`, `.tar.bz2`, `.wic.bz2` 등. meta-virtualization의 기본 Xen/dom0 이미지 |
| `perseus-xen-bringup-image` | 같은 형식의 `perseus-xen-bringup-image-raspberrypi4-64.rootfs.*`. 기본 이미지를 확장한 별도 테스트 이미지 |
| `perseus-domu-initramfs` | `perseus-domu-initramfs-raspberrypi4-64.rootfs.cpio.gz` 및 `.manifest`. 디스크 없는 최소 테스트 게스트 rootfs |
| 공통 부트 자산 | `Image`, `xen-raspberrypi4-64`, `boot.scr`, `u-boot.bin`, Pi 4 DTB |

타임스탬프가 붙은 실제 파일과 위 이름의 심볼릭 링크가 함께 생성된다. 기본 `TMPDIR`, `DL_DIR`, `SSTATE_DIR`는 새 빌드 아래에 있으므로 기존 캐시와 분리된다. 대형 이미지·로그는 Git에 넣지 않고 별도 산출물 저장소에서 전달한다.

## 4. 현재 구성과 부트 체인

영구 Ubuntu·Debian VM, 자동 네트워크·시작/종료, 초기 인증과 데이터 영역의 현재 계약은 [영구 플랫폼 가이드](perseus-persistent-platform.md)를 우선 따른다. 이 문서의 BusyBox는 회귀 시험용이다.

프로젝트 공통 정책은 `platform/yocto/config/local.conf.fragment`, 외부 레이어 버전은 `platform/yocto/layers.lock`, Xen·Linux·U-Boot의 소스 선택은 그 고정 레이어의 레시피가 기준이다. 레이어 등록과 설정 생성은 `platform/scripts/setup-yocto.sh`가 담당한다. 문서에서 SHA나 공통 설정 전체를 복사해 별도로 유지하지 않는다. 과거에 확인한 구체적인 버전과 원본 로그는 감사 기록을 참고한다.

Pi 펌웨어 → U-Boot → `boot.scr` → Xen → dom0 순서다. 외부 meta-virtualization의 Raspberry Pi 동적 레이어가 Xen 부트 스크립트를 제공한다. 프로젝트 bbappend는 unpack 경로 호환성을 보완하고 자체 `boot.cmd.xen.in`을 선택한다. 현재 U-Boot 2026.01의 `bootflow scan`이 전달하는 `devtype/devnum`으로 MMC를 선택하며, 수동 `source`에서 변수가 없을 때만 `mmc 0`을 기본값으로 쓴다. Control FDT를 `${fdt_addr_r}`에 복사한 뒤 그 복사본만 수정한다. 생성된 `boot.scr`는 FAT에서 `xen`·`Image`를 적재하며 dom0 rootfs는 두 번째 SD 파티션의 PARTUUID로 찾는다. Xen dom0 메모리는 `RPI_DOM0_MEM = "1024M"`, 콘솔은 `hvc0`다.

기존 이미지에서 수동 보정 후 Xen/dom0/BusyBox domU 부팅·DHCP·양방향 Ping에 성공했다는 사용자 보고가 있다. 이를 소스에 반영한 [2026-10-16 1차 소스 감사](yocto-rpi4-xen-source-audit-20261016.md)는 빌드 없이 수행했으며, 수정본의 자동 부팅과 네트워크는 2차 빌드·실보드 재검증 대상이다.

## 5. domU 담당자가 수정할 위치

아래 경로는 모두 `platform/yocto/meta-perseus/` 기준이다.

| 위치 | 현재 역할 / 후속 작업 |
| --- | --- |
| `recipes-core/images/perseus-domu-initramfs.bb` | busybox·base-passwd·iproute2·udhcpc·자체 `/init`을 `cpio.gz`로 패키징 |
| `recipes-core/perseus-domu-init/` | `/init` 및 `/etc/perseus/udhcpc.script`. proc/sys/dev 마운트, hvc0 연결, DHCP, 대화형 셸 |
| `recipes-core/images/perseus-xen-bringup-image.bb` | domU 이미지 완료와 커널 deploy를 의존성으로 연결. rootfs 후처리에서 게스트 자산 2개 복사 |
| `recipes-core/perseus-bringup/files/perseus-domu.cfg` | `xl create`에 전달할 VM 설정. kernel/ramdisk 경로, 메모리·vCPU·MAC·bridge 연결 |
| `recipes-core/perseus-bringup/perseus-bringup_1.0.bb` | 진단 스크립트·VM 설정·systemd unit 설치. 브릿지·DHCP unit 기본 활성화 |
| `recipes-core/perseus-bringup/files/` | Bridge, DHCP, 진단 스크립트와 service 원본 |
| `recipes-support/dnsmasq/dnsmasq_%.bbappend` | 기본 dnsmasq unit 자동 기동 비활성화. **dnsmasq를 설치하는 다른 이미지에도 적용되는 레이어 범위 설정** |
| `recipes-bsp/xen-rpi-u-boot-scr/` | unpack 경로 호환성, 부트 장치 선택, Control FDT 복사 및 Xen/dom0 부팅 템플릿 |
| `recipes-extended/xen/` | `xen-tools-net-conf`의 기본 브릿지 정책 대체. 물리 NIC 자동 편입·브릿지 자동 생성 제거, `vif*`·`xenbr0` networkd 비관리 |

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

네트워크는 물리 NIC에 연결하지 않은 `xenbr0`, dom0 `192.168.77.1/24`, DHCP `192.168.77.100~150`, MAC `02:16:3e:20:00:01`이다. DNS·기본 라우터를 제공하지 않는다. 브릿지·DHCP unit은 기본 자동 시작한다. Perseus 스크립트가 브릿지 생성·고정 IP를, Xen `vif-bridge`가 VIF 부착·해제를 담당한다. systemd-networkd는 `vif*`와 `xenbr0`를 관리하지 않으며, 물리 NIC에는 기존 일반 networkd 정책이 적용된다. `xen-tools` bbappend는 meta-perseus를 사용하는 Xen 이미지 전체에 적용된다.

빌드 전 커널 필수 옵션 검사는 이 문서 3-2에서 수행한다. 생성 이미지·추가 커널 진단·네트워크·VM 실행 명령은 [bring-up 검증 절차](yocto-rpi4-xen-minimal-bringup.md)를 따른다.

현재 소스는 dom0 초기 암호를 root / 1234로 설정하고 SSH의 빈 암호 인증을 거부한다. 암호는 이미지 준비 시 설정하며 부팅 때 재설정하지 않는다. UART 진단용 설정이므로 배포 이미지에 적용하기 전에 인증 정책을 별도로 정해야 한다. 임시 게스트 PID 1은 셸 재시작 루프이며 정상 종료 처리는 미구현이다. `xl destroy`는 정상 종료 검증이 아니다. 보드 점검은 [bring-up 절차](yocto-rpi4-xen-minimal-bringup.md)를 따른다.

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
| WSL 설치가 0.0%에서 멈춤 | 관리자 PowerShell에서 `wsl --install --web-download -d Ubuntu-24.04`를 사용한다. [Microsoft 설치 안내](https://learn.microsoft.com/en-us/windows/wsl/install)를 참고한다. |
| WSL 오류 `0x80370102` / 가상 머신 시작 불가 | BIOS/UEFI의 가상화와 Windows Virtual Machine Platform 조건을 [WSL 설치 문제 해결](https://learn.microsoft.com/en-us/windows/wsl/troubleshooting#installation-issues)에서 확인한다. Ubuntu가 실행되기 전에는 Linux 명령을 진행하지 않는다. |
| Ubuntu 경로가 `/mnt/c/windows/system32` | Ubuntu Bash에서 `cd "$HOME"` 후 `pwd`가 `/home/<사용자>`인지 확인한다. |
| GitLab 인증 실패 / 필요한 프로젝트 파일 없음 | 본인 접근 권한과 현재 통합 상태를 확인한다. 파일을 다른 버전에서 개별 복사하지 않는다. |
| `Permission denied`로 setup 실행 불가 | `bash platform/scripts/setup-yocto.sh --verify`로 실행한다. |
| origin·HEAD 불일치 / `local changes` | 현재 `layers.lock`과 외부 checkout을 대조한다. 로컬 변경을 보존하고 필요하면 1절의 새 작업 공간을 사용한다. |
| `not owned` / `managed settings differ` | 기존 수동 conf를 덮어쓰지 않는다. 새 빌드 경로 또는 새 작업 공간에서 구성한다. |
| systemd 요구 / 커널 제공자 오류 | 현재 fragment·레이어가 적용됐는지 확인한다. 임시 설정을 local.conf에 중복 추가하지 않는다. |
| Xen 부트 스크립트 unpack 경로 오류 | meta-perseus의 U-Boot bbappend 등록과 `S == UNPACKDIR` 검증 결과를 확인한다. |
| `meta-virt-xen.inc` 중복 inclusion | local.conf에 해당 include를 수동으로 추가했는지 확인한다. |
| `Reconnecting to bitbake server` / 원본 URL 재시도 경고 | 문구만으로 성공·실패를 판정하지 않는다. 최종 종료 코드와 오류, 실패 태스크 로그를 확인한다. |
| WSL의 `systemd-binfmt.service` 오류 | `sudo dpkg --audit`, `sudo apt-get check`로 패키지 상태를 확인한다. QEMU 실행 관련 문제는 별도 조사한다. |
| 호스트 `CONFIG_TASK_IO_ACCOUNTING` / VHDX 경고 | 각각 호스트 I/O 통계와 저장 공간 관련 경고다. 최종 결과와 실제 디스크 여유를 함께 확인한다. |

커널 설정·SD 이미지·실보드 부팅·domU 통신 문제는 [bring-up 검증](yocto-rpi4-xen-minimal-bringup.md#6-실패할-때-먼저-볼-항목)을 참고한다. 캐시 없는 전체 컴파일·이미지 바이트 동일성·실보드 동작을 검증했다는 뜻으로 환경 준비 성공을 표현하지 않는다.
