# Raspberry Pi 4 + Xen — Yocto 빌드 환경 빠른 시작 (WSL2 / 자동화)

> GitLab의 `scripts/setup-yocto.sh`로 팀과 동일한 Yocto 빌드 환경을 준비하기 위한 안내입니다. 
> WSL2 Ubuntu 24.04에서 저장소 Clone → 설정 자동화 → 검증 → 필요 시 이미지 빌드  
> 2026-10-13 빌드, WSL2 Ubuntu 24.04.5 / Poky 6.0.4 / BitBake 2.18.0 / Raspberry Pi 4 Model B 8GB

설치·Layer 등록·설정 작업을 직접 해보고 싶다면 [수동 빌드 안내서](./yocto-rpi4-xen-manual.md)를 참고하세요.

## 1. 준비 사항

| 항목 | 요구 및 참고 |
| --- | --- |
| 운영체제 | Windows 11 + WSL2 + Ubuntu 24.04 LTS (`x86_64`) |
| 권한 | SSAFY GitLab 프로젝트 `S15P31E110` Clone 권한 |
| 파일시스템 | 프로젝트와 빌드 작업 모두 WSL Linux 경로(`~/...`) 사용 |
| 자원 | 첫 전체 이미지 빌드는 시간·RAM·디스크 사용량이 큼. 병렬 작업 기본값은 4개 |
| 주요 구성 | Yocto/Poky Wrynose 6.0, BitBake 2.18, `raspberrypi4-64`, Xen |

**Windows PowerShell(관리자 권한)** 에서 WSL이 없다면 다음을 실행한다.

```powershell
wsl --install -d Ubuntu-24.04
```

재부팅과 최초 Linux 계정 생성을 마친 뒤 다음으로 확인한다.

```powershell
wsl -l -v
wsl -d Ubuntu-24.04
```

이하 명령은 **WSL Ubuntu Bash**에서 실행한다.

```bash
cd ~
cat /etc/os-release
uname -m
free -h
```

### 선택: WSL2 자원 설정

32GB RAM PC에서는 Windows `%USERPROFILE%\.wslconfig`에 다음 값을 사용한 사례가 있다. 다른 PC에서는 상황에 맞춰 조정한다.

```ini
[wsl2]
memory=20GB
swap=8GB
```

PowerShell에서 설정 적용 시 `wsl --shutdown` 후 Ubuntu를 다시 실행한다. **20GB는 WSL 최대 사용 한도이지 즉시 점유량이 아니다.**

## 2. 빌드 호스트 패키지 설치

`setup-yocto.sh`는 Ubuntu 개발 패키지를 `apt install`하지 않는다. 처음 사용하는 Ubuntu라면 아래를 먼저 설치한다.

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

`systemd-binfmt.service`가 WSL2에서 읽기 전용 파일시스템 오류를 내는 경우가 있다. 패키지 설치 상태가 걱정되면 `sudo dpkg --audit`와 `sudo apt-get check`를 확인한다. 이 경고를 임의로 '해결' 처리하거나 빌드 전체 실패로 단정하지 않는다.

## 3. GitLab 프로젝트 Clone

WSL의 Linux 홈에서 GitLab 프로젝트를 Clone한다.

```bash
cd ~
git clone https://lab.ssafy.com/s15-final/S15P31E110.git
cd ~/S15P31E110
```

**주의:** `setup-yocto.sh`, `layers.lock`, `local.conf.fragment`, `meta-perseus`가 포함된 버전을 사용해야 한다. 해당 변경이 기본 브랜치에 병합되기 전이라면 `feat/platform/yocto-build-setup` 브랜치를 체크아웃한다.

```bash
# 기본 브랜치에 아직 없다면 (선택)
git fetch origin feat/platform/yocto-build-setup
git switch -c feat/platform/yocto-build-setup --track origin/feat/platform/yocto-build-setup
```

현재 버전의 파일 구성 확인:

```bash
ls -l scripts/setup-yocto.sh
ls -l platform/yocto/layers.lock
ls -l platform/yocto/config/local.conf.fragment
ls -l platform/yocto/meta-perseus/conf/layer.conf
ls -l platform/yocto/meta-perseus/recipes-bsp/xen-rpi-u-boot-scr/xen-rpi-u-boot-scr.bbappend
```

GitLab에서 Clone하면 파일 실행 권한이 일반적으로 보존된다. `Permission denied`가 발생하면:

```bash
chmod +x scripts/setup-yocto.sh
```

## 4. 한 명령으로 환경 구성 및 검증

```bash
cd ~/S15P31E110
./scripts/setup-yocto.sh --verify
```

이 스크립트가 수행하는 일:

| 순서 | 실제 동작 |
| --- | --- |
| 1 | `platform/yocto/layers.lock`의 URL·40자리 커밋 기준으로 외부 저장소 6개 검사; 없으면 Clone 후 커밋 고정 |
| 2 | `~/yocto-workspace/build-rpi4-xen-repro/` 빌드 환경 초기화 |
| 3 | 의존 Layer들과 GitLab의 `meta-perseus` 등록 |
| 4 | `local.conf.fragment`를 `conf/local.conf`의 관리 블록에 적용 |
| 5 (`--verify`) | `bitbake -p` 파싱, `.bbappend` 등록 여부, `S/UNPACKDIR`, `MACHINE`, 커널 제공자 및 `RPI_DOM0_MEM` 설정 확인 |

**이 명령은 실제 `xen-image-minimal` 이미지를 빌드하지 않는다.** `--verify`는 메타데이터·설정 검증이며, Xen 커널 컴파일이나 Raspberry Pi 부팅 테스트가 아니다.

정상적인 결과에서 확인할 예시는 다음과 같다.

```text
[OK] openembedded-core ...
[OK] meta-virtualization ...
[OK] Added: meta-perseus
[OK] Added project settings to local.conf
Parsing of 3208 .bb files complete (... 0 errors)
.../xen-rpi-u-boot-scr.bbappend
MACHINE="raspberrypi4-64"
PREFERRED_PROVIDER_virtual/kernel="linux-raspberrypi"
RPI_DOM0_MEM="1024M"
[setup-yocto] Setup complete.
```

출력 줄 수나 캐시 통계는 환경에 따라 달라질 수 있다.

## 5. 빌드 환경에 들어가고 이미지 생성하기

설정 스크립트는 별도 프로세스로 실행되므로 현재 셸에 BitBake 명령 환경을 지속시키지 않는다. 이미지 빌드가 필요하면:

```bash
source ~/yocto-workspace/layers/openembedded-core/oe-init-build-env \
  ~/yocto-workspace/build-rpi4-xen-repro

# 선택: 이미지 의존성 Dry Run
bitbake -n xen-image-minimal

# 실제 이미지 빌드 (시간·저장 공간 사용에 유의)
bitbake xen-image-minimal
```

`bitbake -n`은 실행 계획 검사이며 실제 컴파일을 수행하지 않는다. `bitbake xen-image-minimal`이 **실제 이미지 생성 명령**이다.

성공하면:

```bash
IMG_DIR=tmp/deploy/images/raspberrypi4-64
ls -lh "$IMG_DIR"
```

일반적으로 `xen`, Linux `Image`, `boot.scr`, Device Tree, `xen-image-minimal-raspberrypi4-64.rootfs.rpi-sdimg` 등을 확인한다. Wrynose 환경에서 첫 빌드와 정적 SD 이미지 검증에 성공한 사례는 **기존 수동 빌드 디렉터리**의 결과이며, 여기서 새로 생성한 환경의 전체 빌드 결과를 미리 보장하지 않는다.

## 6. 재실행·기존 결과 보존

같은 환경에서 설정 검증 없이 재실행할 수 있다.

```bash
cd ~/S15P31E110
./scripts/setup-yocto.sh
```

이미 존재하는 Layer는 건너뛰고, 기존 관리 블록이 `local.conf.fragment`와 일치하면 다시 삽입하지 않는다.

**안전 동작:**

- 외부 소스가 고정 커밋과 다르거나 로컬 변경이 있으면 자동으로 덮어쓰지 않고 중단한다.
- 기존 관리 블록이 프로젝트 설정과 다르면 덮어쓰지 않고 중단한다.
- 기본값에서 작업하는 빌드 디렉터리는 `build-rpi4-xen-repro`다. 과거에 성공한 `build-rpi4-xen`을 기본 실행으로 수정하지 않는다.
- 기존 캐시와 빌드 산출물은 터미널 종료나 Windows 재부팅만으로 사라지지 않는다. `cleanall`, 디렉터리 삭제 및 WSL 초기화에는 주의한다.

### 다른 경로에 작업하고 싶다면

환경변수로 절대경로를 지정할 수 있다. 단, `/mnt/c/...` 같은 Windows 마운트 경로 대신 Linux 파일시스템을 사용한다.

```bash
cd ~/S15P31E110
YOCTO_WORKSPACE="$HOME/yocto-workspace" \
YOCTO_BUILD_DIR="$HOME/yocto-workspace/build-my-test" \
  ./scripts/setup-yocto.sh --verify
```

실제 빌드를 할 때는 같은 `YOCTO_BUILD_DIR`을 지정해 `oe-init-build-env`를 실행한다.

## 7. 흔한 오류

| 오류·상황 | 확인할 사항 |
| --- | --- |
| `Permission denied` | `chmod +x scripts/setup-yocto.sh` 또는 `bash scripts/setup-yocto.sh --verify` |
| GitLab 인증 실패 | 프로젝트 접근 권한 및 HTTPS 인증 방식 확인 |
| `Missing ... local.conf.fragment` 또는 `.bbappend` | 올바른 프로젝트 브랜치인지 확인 |
| `origin mismatch` / `commit mismatch` | 스크립트는 외부 저장소를 임의 변경하지 않음. `layers.lock`과 실제 Clone 상태 비교 후 수동 조정 |
| `has local changes` | 외부 Layer의 변경사항 확인·보존 후 처리. 무작정 삭제 금지 |
| `existing explicit project settings` | 수동 설정이 들어 있는 `local.conf`에 중복 적용하려는지 확인. 새 빌드 디렉터리 권장 |
| `managed settings differ` | 프로젝트 fragment와 현재 설정 차이 확인. 자동 덮어쓰기하지 않음 |
| `Reconnecting to bitbake server` | 그 자체만으로 실패가 아님. 최종 `ERROR`와 종료 상태 확인 |
| WSL VHDX 크기 경고 | Windows 실제 디스크 여유를 확인하고 필요할 때 별도 최적화 검토 |

## 8. 관련 파일

```text
S15P31E110/
├── platform/yocto/
│   ├── layers.lock                         # 외부 저장소 URL과 커밋
│   ├── config/local.conf.fragment          # Raspberry Pi/Xen 설정
│   └── meta-perseus/                       # 프로젝트 전용 Layer
│       ├── conf/layer.conf
│       └── recipes-bsp/xen-rpi-u-boot-scr/
│           └── xen-rpi-u-boot-scr.bbappend
├── scripts/setup-yocto.sh                  # 이 문서의 자동 환경 준비 도구
└── docs/build-guides/
    ├── yocto-rpi4-xen-wsl2.md               # 이 문서
    └── yocto-rpi4-xen-manual.md             # 수동 빌드 안내서
```

## 참고

- [Yocto Project](https://docs.yoctoproject.org/)
- [Yocto 6.0 시스템 요구사항](https://docs.yoctoproject.org/6.0/ref-manual/system-requirements.html)
- [OpenEmbedded meta-virtualization Layer](https://layers.openembedded.org/layerindex/branch/wrynose/layer/meta-virtualization/)
