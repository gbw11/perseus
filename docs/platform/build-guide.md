# Xenova 빌드 가이드

환경 준비 → Linux·Zephyr 템플릿 준비·게시 → 기본 선택 확정 → 회귀·입력 고정 → dom0 빌드·검증·게시 → seed·최종 이미지 조립·검증 → PC 인계 순서로 진행한다. 빌드 절차와 명령이 바뀌면 이 문서를 갱신한다.

소스 대조 기준: [하위 플랫폼·backend 통합 입력 `347d935`](https://lab.ssafy.com/s15-final/S15P31E110/-/tree/347d935cb7ef09e07f3082a13af1733bb6acd2cb). 빌드할 때는 팀에서 승인한 통합 커밋을 선택하고, 아래 입력 고정 단계에서 실제 HEAD·파일·설정 해시를 기록한다.

## 1. 빌드 구성과 경로

| 항목 | 기준 |
|---|---|
| 호스트 | Windows 11 + WSL2 Ubuntu 24.04, x86_64 |
| 타깃 | Raspberry Pi 4, `raspberrypi4-64` |
| dom0 | `xenova-xen-bringup-image` |
| dom0 설치 | Manager·CLI·복구/신원·CPU 정책, FastAPI/Uvicorn·통계·heartbeat worker, Paho·공용 MQTT 클라이언트, xentop·Mosquitto |
| 기본 CPU 배치 | Zephyr A→pCPU 1, B→2, dom0·일반 VM의 모든 vCPU→{0,3}. Credit2·기존 vCPU 수 유지 |
| 외부 레이어 | [layers.lock](../../platform/yocto/layers.lock)의 6개 URL·commit |
| Linux 원본 | [sources.lock.json](../../platform/guests/sources.lock.json)의 Ubuntu·Debian cloud image |
| 기본 선택 | [default-templates.lock.json](../../platform/guests/default-templates.lock.json): Ubuntu·Debian 1.0.2, Network Starter 1.1.0 |
| 기본 VM | Debian Interaction 1대 + Zephyr A/B 2대 |
| Linux 게스트 | Paho·공용 MQTT 클라이언트·Agent, 8GiB root 디스크 |
| Zephyr 게스트 | 1 vCPU·16MiB·GICv2, Xen Ethernet·DHCP·ICMP·협조형 stop/reboot |
| 이미지 구성 | FAT boot + dom0 rootfs + `xenova-data` ext4 |

소스·빌드·캐시는 WSL의 Linux 파일시스템에 둔다. Windows 경로는 완성된 압축 이미지와 로그를 전달할 때 사용한다.

**PowerShell:**

```powershell
wsl --install -d Ubuntu-24.04
wsl --list --verbose
wsl -d Ubuntu-24.04
```

이하 명령은 별도 표시가 없으면 **WSL Ubuntu Bash**에서 실행한다. 처음 설치한 Ubuntu의 사용자 계정으로 로그인한 뒤 준비한다.

```bash
sudo apt-get update
sudo apt-get install -y build-essential chrpath cpio debianutils diffstat file \
  gawk git bzip2 patch iputils-ping libacl1 liblz4-tool locales python3 python3-pip \
  python3-pexpect python3-venv python3-yaml socat texinfo unzip wget curl \
  xz-utils zstd mtools dosfstools e2fsprogs util-linux qemu-utils \
  qemu-user-static binfmt-support device-tree-compiler rpm2cpio

sudo locale-gen en_US.UTF-8
export LANG=en_US.UTF-8
export LC_ALL=en_US.UTF-8

git clone https://lab.ssafy.com/s15-final/S15P31E110.git "$HOME/S15P31E110"
cd "$HOME/S15P31E110"
read -r -p "빌드할 통합 커밋 SHA: " SOURCE_REV
git switch --detach "$SOURCE_REV"
```

GitLab HTTPS 인증에는 팀 저장소 접근 권한이 있는 계정을 사용한다. 기존 clone에서는 작업 변경을 확인한 뒤 팀에서 정한 통합 소스를 선택한다.

매 실행은 새 `RUN_ID`로 시작한다. 아래 경로 변수는 같은 셸에서 이후 단계까지 유지한다.

```bash
set -Eeuo pipefail
export PYTHONDONTWRITEBYTECODE=1
export REPO="$(git rev-parse --show-toplevel)"
export YOCTO_WORKSPACE="$HOME/yocto-workspace"
export XENOVA_ARTIFACTS="$HOME/xenova-artifacts"
export CACHE="$XENOVA_ARTIFACTS/cache"
export RELEASE_ROOT="$XENOVA_ARTIFACTS/releases"
export RUN_ID="xenova-$(date +%Y%m%d-%H%M%S)"
export RUN="$XENOVA_ARTIFACTS/runs/$RUN_ID"
export YOCTO_BUILD_DIR="$YOCTO_WORKSPACE/build-rpi4-$RUN_ID"
export DEPLOY="$YOCTO_BUILD_DIR/tmp/deploy/images/raspberrypi4-64"
export ZEPHYR_SOURCE="$CACHE/source/zephyr"
export ZEPHYR_TOOLS="$CACHE/zephyr-sdk-1.0.0"
export SPDX="$CACHE/downloads/zephyr-v4.4.0.spdx"
export SDK_ARCHIVE="$CACHE/downloads/toolchain_gnu_linux-x86_64_aarch64-zephyr-elf.tar.xz"

mkdir -p "$CACHE/downloads" "$CACHE/source" "$RELEASE_ROOT" "$RUN/logs" "$RUN/tools"
git status --short
git rev-parse HEAD
df -h "$RUN" /mnt/c
```

`RUN`에는 실행 명령·종료 코드·전체 stdout/stderr와 입력 해시를 모은다. 다음 기록 도구를 한 번 작성하고 `run 이름 명령 인자…`로 사용한다.

```bash
cat >"$RUN/tools/run.py" <<'PY'
import datetime, json, os, subprocess, sys, time
from pathlib import Path
run = Path(os.environ["RUN"])
label, *argv = sys.argv[1:]
assert label and "/" not in label and argv
start = time.monotonic()
with (run/"logs"/(label+".stdout")).open("x") as out, \
     (run/"logs"/(label+".stderr")).open("x") as err:
    result = subprocess.run(argv, stdout=out, stderr=err)
record = {"argv": argv, "cwd": os.getcwd(), "rc": result.returncode,
          "elapsed_seconds": time.monotonic()-start,
          "finished_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
(run/(label+".command.json")).write_text(json.dumps(record, indent=2)+"\n")
print(label, "rc="+str(result.returncode), flush=True)
if result.returncode:
    for suffix in ("stdout", "stderr"):
        print((run/"logs"/(label+"."+suffix)).read_text()[-8000:])
raise SystemExit(result.returncode)
PY
run() { python3 -B "$RUN/tools/run.py" "$@"; }
```

## 2. 공통 빌드·검증 도구 준비

### Yocto 레이어와 새 빌드 구성

`setup-yocto.sh`는 lock에 맞는 외부 소스를 준비하고 새 빌드의 설정을 생성한다. download·sstate는 실행 간 공유한다.

```bash
cd "$REPO"
run setup bash platform/scripts/setup-yocto.sh --verify

cat >>"$YOCTO_BUILD_DIR/conf/local.conf" <<EOF
DL_DIR = "$CACHE/yocto-downloads"
SSTATE_DIR = "$CACHE/yocto-sstate"
PTEST_ENABLED:pn-python3-fastapi = "0"
EOF
```

FastAPI·Pydantic Core·MQTT와 필수 호스트 빌드 도구는 이미지 의존성에 따라 준비된다. 위 설정은 FastAPI의 upstream ptest를 제외하는 정규 빌드 설정이다.

### ARM64 사용자 공간 실행

Linux 템플릿의 apt·SSH·Agent 검사는 ARM64 chroot에서 실행한다.

```bash
sudo service binfmt-support start
sudo update-binfmts --enable qemu-aarch64
cat /proc/sys/fs/binfmt_misc/qemu-aarch64
qemu-aarch64-static --version
```

handler가 `enabled`이고 `F` 플래그를 포함하는지 확인한다.

### systemd 분석기

회귀와 이미지 unit 검사는 systemd 259 이상의 실제 `systemd-analyze`를 사용한다. 호스트에 해당 버전이 설치돼 있으면 다음처럼 지정한다.

```bash
systemd-analyze --version
export XENOVA_DOM0_SYSTEMD_ANALYZE='["/usr/bin/systemd-analyze"]'
export XENOVA_SYSTEMD_ANALYZE="$XENOVA_DOM0_SYSTEMD_ANALYZE"
```

Ubuntu 24.04 빌드 PC의 정규 구성은 기존 완료 dom0 빌드의 rootfs archive와 같은 빌드에서 나온 `systemd-analyze` RPM을 도구 캐시에 추출해 사용한다. 두 입력은 완료 빌드 담당자가 전달한 파일 경로로 지정한다.

```bash
export BASELINE_ROOTFS=/absolute/completed-build/xenova-xen-bringup-image.rootfs.tar.bz2
export ANALYZE_RPM=/absolute/completed-build/systemd-analyze-259.5-r0.cortexa72.rpm
export VERIFY_ROOT="$CACHE/systemd-259"
mkdir "$VERIFY_ROOT"
tar --no-same-owner -xjf "$BASELINE_ROOTFS" -C "$VERIFY_ROOT"
(
  cd "$VERIFY_ROOT"
  rpm2cpio "$ANALYZE_RPM" | cpio -idm --quiet --no-absolute-filenames
)
sha256sum "$BASELINE_ROOTFS" "$ANALYZE_RPM" >"$RUN/verifier-inputs.sha256"

export XENOVA_DOM0_SYSTEMD_ANALYZE="$(python3 - <<'PY'
import json, os
from pathlib import Path
r = Path(os.environ["VERIFY_ROOT"])
print(json.dumps(["qemu-aarch64-static", "-L", "/",
    str(r/"lib/ld-linux-aarch64.so.1"), "--library-path",
    str(r/"usr/lib")+":"+str(r/"usr/lib/systemd"),
    str(r/"usr/bin/systemd-analyze")]))
PY
)"
export XENOVA_SYSTEMD_ANALYZE="$XENOVA_DOM0_SYSTEMD_ANALYZE"
python3 - <<'PY'
import json, os, subprocess
subprocess.run(json.loads(os.environ["XENOVA_DOM0_SYSTEMD_ANALYZE"])+["--version"],
               check=True)
PY
```

다음 실행에서는 준비된 `VERIFY_ROOT`를 재사용하고 두 분석기 환경변수만 지정한다.

### Zephyr 소스·SDK·Python 도구

고정 입력은 [source.lock.json](../../platform/guests/zephyr/source.lock.json)에 있다. Linux cloud image도 함께 받아 원본 digest를 검사한다. 다운로드 캐시가 있으면 같은 파일을 검사해 재사용한다.

```bash
run download-inputs python3 -B - <<'PY'
import hashlib, json, os, urllib.request
from pathlib import Path
repo = Path(os.environ["REPO"])
out = Path(os.environ["CACHE"])/"downloads"
sources = json.loads((repo/"platform/guests/sources.lock.json").read_text())
z = json.loads((repo/"platform/guests/zephyr/source.lock.json").read_text())
items = [(v["url"], v["hash_algorithm"], v["digest"]) for v in sources.values() if isinstance(v, dict)]
items += [(z["spdx_url"], "sha256", z["spdx_sha256"]),
          (z["toolchain_url"], "sha256", z["toolchain_sha256"])]
records = []
for url, algorithm, expected in items:
    target = out/url.rsplit("/", 1)[1]
    if not target.exists():
        partial = target.with_name(target.name+".part")
        request = urllib.request.Request(url, headers={"User-Agent": "xenova-build"})
        with urllib.request.urlopen(request, timeout=90) as src, partial.open("xb") as dst:
            while block := src.read(8*1024*1024):
                dst.write(block)
        with partial.open("rb") as stream:
            assert hashlib.file_digest(stream, algorithm).hexdigest() == expected
        partial.rename(target)
    with target.open("rb") as stream:
        actual = hashlib.file_digest(stream, algorithm).hexdigest()
    assert actual == expected, str(target)
    records.append({"path": str(target), "url": url, "algorithm": algorithm,
                    "digest": actual, "bytes": target.stat().st_size})
(Path(os.environ["RUN"])/"official-inputs.json").write_text(json.dumps(records, indent=2)+"\n")
PY

git clone --branch v4.4.0 --depth 1 \
  https://github.com/zephyrproject-rtos/zephyr.git "$ZEPHYR_SOURCE"
mkdir -p "$ZEPHYR_TOOLS"
tar -xJf "$SDK_ARCHIVE" -C "$ZEPHYR_TOOLS"
python3 -m pip install --target "$ZEPHYR_TOOLS/python" \
  --report "$RUN/zephyr-python-install.json" \
  cmake==3.31.6 ninja==1.11.1.4 west==1.5.0 \
  -r "$ZEPHYR_SOURCE/scripts/requirements-base.txt"
PYTHONPATH="$ZEPHYR_TOOLS/python" \
  "$ZEPHYR_TOOLS/python/bin/west" init -l "$ZEPHYR_SOURCE"
```

다음 실행부터는 기존 `ZEPHYR_SOURCE`·`ZEPHYR_TOOLS`·다운로드 캐시를 지정한다.

## 3. Linux·Network Starter 템플릿 준비

현재 선택한 게시본이 `RELEASE_ROOT`에 준비돼 있으면 아래 검증·선택 확정으로 이어간다. 새 게시본을 만드는 실행은 새 출력 경로를 사용한다. Linux의 준비 버전은 각 `recipe.json`, Zephyr의 앱 출처는 `network-starter.lock.json`을 따른다.

### Ubuntu·Debian 준비

```bash
mkdir -p "$RELEASE_ROOT/linux/ubuntu-24.04-20260926" \
  "$RELEASE_ROOT/linux/debian-13-20261015"

for sid in ubuntu-24.04-20260926 debian-13-20261015; do
  version="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' \
    "$REPO/platform/guests/linux/$sid/recipe.json")"
  input="$(python3 - "$sid" <<'PY'
import json, os, sys
from pathlib import Path
r = Path(os.environ["REPO"])
v = json.loads((r/"platform/guests/sources.lock.json").read_text())[sys.argv[1]]
print(Path(os.environ["CACHE"])/"downloads"/v["url"].rsplit("/", 1)[1])
PY
)"
  run "prepare-$sid" sudo unshare --mount --propagation private \
    python3 -B "$REPO/platform/scripts/prepare-xenova-guests.py" \
    --execute template --source "$sid" --input "$input" \
    --output "$RELEASE_ROOT/linux/$sid/$version"
done
```

이 경로에서 새 rootfs에 `python3-paho-mqtt`를 설치하고, 저장소의 기존 Agent installer를 호출한다. Python import·Agent enable·설치 영수증·커널/부트 자산을 검사한 번들이 게시된다. `template.json`의 `guest_agent`와 `/etc/xenova/agent-install.json`에 패키지 버전과 실제 소스·deb 해시가 기록된다.

### Zephyr Network Starter 빌드·게시

```bash
run network-starter-build python3 -B \
  "$REPO/platform/scripts/build-xenova-network-starter.py" \
  --zephyr-source "$ZEPHYR_SOURCE" --tools "$ZEPHYR_TOOLS" \
  --spdx "$SPDX" --toolchain-archive "$SDK_ARCHIVE" \
  --output "$RUN/network-build" --execute

run network-starter-package python3 -B \
  "$REPO/platform/scripts/package-xenova-network-starter.py" \
  --build-root "$RUN/network-build" --output "$RUN/network-candidate"

run network-starter-publish python3 -B - <<'PY'
import json, os, shutil, sys
from pathlib import Path
repo = Path(os.environ["REPO"])
sys.path[:0] = [str(repo/"platform/first-install"),
    str(repo/"platform/yocto/meta-xenova/recipes-core/xenova-platform/files")]
from artifact_io import sha
from xenova_zephyr import bundle_info
source = Path(os.environ["RUN"])/"network-candidate"
info = bundle_info(source, sha(source/"firmware.json"))
m = json.loads((source/"firmware.json").read_text())
target = Path(os.environ["RELEASE_ROOT"])/"zephyr"/m["id"]/m["template_version"]
target.parent.mkdir(parents=True, exist_ok=True)
assert not target.exists()
shutil.copytree(source, target, copy_function=shutil.copyfile)
for p in source.iterdir():
    assert sha(p) == sha(target/p.name)
    (target/p.name).chmod(0o444)
bundle_info(target, sha(target/"firmware.json"))
target.chmod(0o555)
print(json.dumps({"directory": str(target), "manifest_sha256": sha(target/"firmware.json")}))
PY
```

빌드 영수증의 ELF·DTS·config·앱/런타임 해시와 고정 Zephyr 원본을 함께 보관한다.

### 실제 manifest로 기본 선택 확정

새 게시본을 준비한 실행에서는 실제 manifest와 배포 버전을 읽어 기본 lock·factory 버전을 맞춘다.

```bash
run select-templates python3 -B - <<'PY'
import hashlib, json, os
from pathlib import Path
repo = Path(os.environ["REPO"])
release = Path(os.environ["RELEASE_ROOT"])
path = repo/"platform/guests/default-templates.lock.json"
lock = json.loads(path.read_text())
versions = {}
for entry in lock["templates"]:
    if entry["guest_kind"] == "linux":
        recipe = json.loads((repo/"platform/guests/linux"/entry["id"]/"recipe.json").read_text())
        version = recipe["version"]
        directory = Path("linux")/entry["id"]/version
        manifest = release/directory/"template.json"
    else:
        z = json.loads((repo/"platform/guests/zephyr/network-starter.lock.json").read_text())
        version = z["version"]
        directory = Path("zephyr")/z["id"]/version
        manifest = release/directory/"firmware.json"
        entry["id"] = z["id"]
    entry.update(version=version, directory=str(directory),
                 manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest())
    versions[entry["template_id"]] = version
path.write_text(json.dumps(lock, indent=2)+"\n")
path = repo/"platform/yocto/meta-xenova/recipes-core/xenova-platform/files/defaults.json"
defaults = json.loads(path.read_text())
for record in defaults["instances"]+defaults["firmware_instances"]:
    record["template_version"] = versions[record["template_id"]]
path.write_text(json.dumps(defaults, indent=2)+"\n")
PY
git diff -- platform/guests/default-templates.lock.json \
  platform/yocto/meta-xenova/recipes-core/xenova-platform/files/defaults.json
```

기존 선택 게시본을 재사용하는 실행에서는 다음 resolver로 lock과 실제 번들을 대조한다.

```bash
run template-references python3 -B "$REPO/platform/scripts/resolve-xenova-templates.py" \
  --release-root "$RELEASE_ROOT"
cp "$RUN/logs/template-references.stdout" "$RUN/template-references.json"

python3 -B - <<'PY'
import json, os, subprocess
from pathlib import Path
repo = Path(os.environ["REPO"])
run = Path(os.environ["RUN"])
refs = json.loads((run/"template-references.json").read_text())
for row in refs["linux_templates"]:
    subprocess.run(["python3", "-B", str(repo/"platform/scripts/verify-xenova-template.py"),
                    "--template", row["template_dir"],
                    "--sha256", row["template_manifest_sha256"]], check=True)
PY
```

## 4. 회귀 검사와 입력 고정

새 빌드 셸에서 Yocto 환경에 진입한다. 위에서 준비한 분석기 JSON과 모든 경로 변수는 export된 상태를 유지한다.

```bash
cd "$REPO"
unset BBPATH BUILDDIR OEROOT BBSERVER
set +u
source "$YOCTO_WORKSPACE/layers/openembedded-core/oe-init-build-env" "$YOCTO_BUILD_DIR"
set -u
cd "$REPO"
```

다음 도구는 소스 내용·mode·Git 상태, 두 conf, 외부 레이어 commit·작업 상태를 한 번 저장하고 각 단계에서 재대조한다.

```bash
cat >"$RUN/tools/inputs.py" <<'PY'
import hashlib, json, os, subprocess, sys
from pathlib import Path
repo, run = Path(os.environ["REPO"]), Path(os.environ["RUN"])
def git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()
def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()
names = set(subprocess.check_output(
    ["git", "-C", str(repo), "ls-files", "-co", "--exclude-standard", "-z"]
).decode().split("\0"))-{""}
files = {n: {"sha256": sha(repo/n), "mode": oct((repo/n).stat().st_mode & 0o777)}
         for n in sorted(names) if (repo/n).is_file()}
layers = {}
for line in (repo/"platform/yocto/layers.lock").read_text().splitlines():
    if line.strip() and not line.lstrip().startswith("#"):
        name, url, revision = line.split()
        path = Path(os.environ["YOCTO_WORKSPACE"])/"layers"/name
        assert git(path, "rev-parse", "HEAD") == revision
        status = git(path, "status", "--porcelain", "--untracked-files=all")
        assert not status, name
        layers[name] = revision
build = Path(os.environ["YOCTO_BUILD_DIR"])
data = {"source_commit": git(repo, "rev-parse", "HEAD"),
        "branch": git(repo, "branch", "--show-current"),
        "status": git(repo, "status", "--porcelain", "--untracked-files=all"),
        "files": files, "layers": layers,
        "config_sha256": {str(build/"conf"/n): sha(build/"conf"/n)
                          for n in ("local.conf", "bblayers.conf")}}
target = run/"BUILD-INPUT.json"
if sys.argv[1] == "record":
    assert not target.exists()
    target.write_text(json.dumps(data, indent=2)+"\n")
    (run/"source.patch").write_bytes(subprocess.check_output(
        ["git", "-C", str(repo), "diff", "--binary", "HEAD"]))
else:
    assert data == json.loads(target.read_text()), "build input changed"
print("INPUTS OK", len(files), "files", len(layers), "layers")
PY

# 선택한 입력에 CPU·worker·토큰 준비 구현이 포함됐는지 먼저 확인한다.
test -f platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_cpu.py
test -f platform/services/dom0-manager/dom0_manager/service_token.py
test -f platform/services/dom0-manager/deploy/xenova-recovery-worker.service
run freeze-inputs python3 -B "$RUN/tools/inputs.py" record
run regression bash "$REPO/platform/scripts/check-xenova-platform.sh" --metadata
run check-after-regression python3 -B "$RUN/tools/inputs.py" check
```

`regression.command.json`에 회귀 종료 코드가, stdout에 별도 metadata 작업 디렉터리가 기록된다. 분석기 실행 파일·Python·compiler·SDK 버전과 다운로드 해시도 같은 `RUN`에 보관한다.

## 5. dom0 빌드와 실제 WIC 검증

```bash
run kernel-config bitbake -c configure linux-raspberrypi
run kernel-prereqs bash "$REPO/platform/scripts/check-xen-bringup-prereqs.sh"
run dom0-build bitbake xenova-xen-bringup-image
run check-after-build python3 -B "$RUN/tools/inputs.py" check

export WIC="$(readlink -f "$DEPLOY/xenova-xen-bringup-image-raspberrypi4-64.rootfs.wic.bz2")"
run wic-boot python3 -B "$REPO/platform/scripts/check-xenova-wic.py" \
  --deploy-dir "$DEPLOY" --image "$WIC" --output "$RUN/wic-boot"

bzcat "$WIC" >"$RUN/dom0.wic"
```

빌드 진행은 다른 WSL 창에서 `tail -f "$RUN/logs/dom0-build.stdout"`로 확인한다. 같은 `RUN` 경로를 지정하면 전체 로그를 이어서 볼 수 있다.

WIC의 실제 FAT에서 `boot.scr`·`xen`·`Image`·U-Boot와 UART/GIC·무선 SDIO PIO 설정을 확인한다. 다음 공통 검사 도구는 실제 ext4를 읽기 전용으로 열어 소스 일치, 패키지, ARM64 import와 unit graph를 확인하며 최종 이미지에서도 재사용한다.

```bash
cat >"$RUN/tools/check-image.py" <<'PY'
import argparse, json, os, subprocess, sys, time
from contextlib import contextmanager
from pathlib import Path
repo = Path(os.environ["REPO"])
sys.path[:0] = [str(repo/"platform/first-install"),
    str(repo/"platform/guests/common"),
    str(repo/"platform/yocto/meta-xenova/recipes-core/xenova-platform/files")]
import assemble
from prepare_instance import Workspace
from prepare_rootfs import verify_guest_agent
from xenova_catalog import entries, describe
parser = argparse.ArgumentParser()
parser.add_argument("image", type=Path)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--final", action="store_true")
args = parser.parse_args()
assert os.geteuid() == 0
args.output.mkdir()
work = Workspace(args.output)
passed = []
analyze = json.loads(os.environ["XENOVA_DOM0_SYSTEMD_ANALYZE"])
def check(value, label):
    assert value, label
    passed.append(label)
def command(argv):
    result = subprocess.run(list(map(str, argv)), capture_output=True, text=True)
    index = len(list(args.output.glob("*.stdout")))
    (args.output/(str(index)+".stdout")).write_text(result.stdout)
    (args.output/(str(index)+".stderr")).write_text(result.stderr)
    check(result.returncode == 0, "command "+str(argv))
    return result.stdout+result.stderr
@contextmanager
def guest_disk(image, name):
    device = work.loop(image, readonly=True)
    target = work.path/name
    try:
        work.mount(device, target, readonly=True)
        try:
            yield target
        finally:
            command(["umount", target])
            work.mounts.remove(target)
    finally:
        command(["losetup", "--detach", device])
        work.loops.remove(device)
def graph(root, units):
    text = command(analyze+["verify", "--recursive-errors=yes", "--generators=no",
                   "--man=no", "--root="+str(root), *units])
    check("Found ordering cycle" not in text, "unit ordering "+root.name)
def linux(root, seed):
    result = verify_guest_agent(root, seed=seed, required=True)
    check(result["installed"] and result["enabled"] and result["identity_gate"] == seed,
          "Paho/Agent imports, enable, identity "+root.name)
    graph(root, ["multi-user.target", "ssh.service", "xenova-agent.service"])
    check(not (root/"etc/xenova/agent.env").exists(), "neutral Agent config "+root.name)
    check(not list((root/"etc/ssh").glob("ssh_host_*_key"))
          and not (root/"etc/xenova/instance-id").exists(), "deferred guest identity "+root.name)
try:
    with args.image.open("rb") as stream:
        layout = assemble.layout(stream.read(512), args.image.stat().st_size,
                                 with_data=args.final)
    dev = work.loop(args.image, readonly=True, partitions=True)
    for number in ([2, 3] if args.final else [2]):
        for attempt in range(40):
            if Path(dev+"p"+str(number)).exists():
                break
            time.sleep(.25)
        command(["e2fsck", "-fn", dev+"p"+str(number)])
    root = work.path/"dom0"
    work.mount(dev+"p2", root, readonly=True)
    installed = assemble.platform_root_ok(root)
    check(bool(installed), "actual dom0 source, units, cfg, Wi-Fi, identity")
    command(["chroot", root, "/usr/bin/python3", "-B", "-c",
        "import fastapi,uvicorn,pydantic,pydantic_core,paho.mqtt.client;"
        "import tty,termios,fcntl,websockets,dom0_manager,xenova_mqtt;"
        "import sys;sys.path.insert(0,'/usr/lib/xenova');"
        "import xenova_management,xenova_execution,xenova_recovery,xenova_cpu;"
        "import dom0_manager.drivers.xentop,dom0_manager.recovery,dom0_manager.service_token"])
    graph(root, ["multi-user.target", "sshd.service", "xenova-vms.service",
                 "xenova-dom0-manager.service", "xenova-recovery-worker.service",
                 "xenova-cpu-prepare.service", "xenova-host-admission.service"])
    for service in ("xenova-dom0-manager.service", "xenova-recovery-worker.service"):
        check((root/"etc/systemd/system/multi-user.target.wants"/service).is_symlink(),
              "enabled "+service)
    preset = json.loads((root/"etc/xenova/cpu-policy-preset.json").read_text())
    check(preset == json.loads((repo/"platform/yocto/meta-xenova/recipes-core/"
          "xenova-platform/files/cpu-policy-preset.json").read_text()), "CPU preset source match")
    check(not (root/"etc/xenova/dom0-manager-token.env").exists(),
          "service token generated per board")
    env = (root/"etc/xenova/dom0-manager.env").read_text().splitlines()
    check(not any(line.startswith("XENOVA_SERVICE_TOKEN=") and line.split("=",1)[1].strip()
                  for line in env), "no shared HTTP token")
    check((root/"etc/machine-id").read_text() == "uninitialized\n",
          "per-board dom0 machine-id")
    check(not list((root/"etc/ssh").glob("ssh_host_*_key")), "per-board SSH keys")
    wifi = root/"etc/xenova/wifi"
    check(not wifi.exists() or not list(wifi.iterdir()), "neutral Wi-Fi config")
    check(not (root/"etc/systemd/network/25-xenova-wifi.network").exists(),
          "no personal Wi-Fi network profile")
    for name in ("usr/lib/xenova", "usr/sbin", "usr/share/xenova"):
        check(not any(("probe" in p.name.lower() or "lab" in p.name.lower())
                      and ("qemu" in p.name.lower() or "xenstore-g1" in p.name.lower())
                      for p in (root/name).iterdir()), "development probe/lab excluded "+name)
    if args.final:
        data = work.path/"data"
        work.mount(dev+"p3", data, readonly=True)
        factory = json.loads((data/".xenova-install.json").read_text())
        check(not list((data/"instances").iterdir()), "factory registration pending")
        check({p.name for p in (data/"seeds").iterdir() if not p.name.startswith(".")} ==
              {"debian-dev", "zephyr-drive-a", "zephyr-drive-b"}, "three default seeds")
        check(len(entries(data)) == 3, "Ubuntu/Debian/Zephyr catalog")
        for entry in entries(data):
            descriptor = json.loads((data/"catalog"/entry["template_id"]/
                                    (entry["template_version"]+".json")).read_text())
            describe(data, descriptor, full=True)
        for item in factory["templates"]:
            directory = data/"templates"/item["id"]
            assemble.template_info(directory, item["id"], item["template_manifest_sha256"])
            image = directory/"root.raw"
            command(["e2fsck", "-fn", image])
            with guest_disk(image, item["id"]) as target:
                linux(target, False)
                check(not (target/"etc/machine-id").read_text().strip(), "neutral Linux machine-id")
        for item in factory["instances"]:
            record = item["record"]
            check(record["id"] == "debian-dev" and record["identity_state"] == "pending",
                  "Debian seed identity")
            image = data/"seeds"/record["id"]/"root.raw"
            command(["e2fsck", "-fn", image])
            with guest_disk(image, "debian-seed") as target:
                linux(target, True)
                check((target/"etc/machine-id").read_text() == "uninitialized\n",
                      "fresh Debian seed machine-id")
        for item in factory["firmware_instances"]:
            record = item["record"]
            assemble.zephyr.validate_instance(record, data/"seeds"/record["id"])
            check(record["capabilities"] == assemble.zephyr.NET_CAPABILITIES
                  and record["board_shutdown_policy"] == "graceful",
                  "network/cooperative firmware "+record["id"])
    (args.output/"RESULT.json").write_text(json.dumps(
        {"image": str(args.image), "layout": layout, "passed": passed}, indent=2)+"\n")
finally:
    work.close()
print("PASS", len(passed), "actual image checks")
PY

run dom0-rootfs sudo env REPO="$REPO" \
  XENOVA_DOM0_SYSTEMD_ANALYZE="$XENOVA_DOM0_SYSTEMD_ANALYZE" \
  unshare --mount --propagation private python3 -B "$RUN/tools/check-image.py" \
  "$RUN/dom0.wic" --output "$RUN/dom0-inspection"
```

실제 package manifest와 필수 의존성도 대조한다.

```bash
run wic-packages python3 -B - <<'PY'
import os
from pathlib import Path
deploy = Path(os.environ["DEPLOY"])
path = (deploy/"xenova-xen-bringup-image-raspberrypi4-64.rootfs.manifest").resolve()
names = {line.split()[0] for line in path.read_text().splitlines() if line.strip()}
required = {"xenova-platform", "xenova-bringup", "xenova-dom0-manager",
    "xenova-mqtt-client", "python3-fastapi", "python3-uvicorn", "python3-paho-mqtt",
    "python3-shell", "python3-terminal", "python3-websockets", "xen-tools-xl",
    "xen-tools-xenstat", "libxenmanage1", "mosquitto", "mosquitto-clients",
    "qemu-system-i386"}
assert required <= names, sorted(required-names)
assert any(n.startswith("kernel-module-brcmfmac-cyw-") for n in names)
assert "python3-fastapi-ptest" not in names
print(path)
print("PASS", len(required)+1, "required packages including kernel-versioned CYW module")
PY
```

## 6. 완료 dom0 게시

빌드와 WIC 검사를 통과한 입력으로 `BUILD-RESULT.json`을 작성한다. WIC 경로는 timestamp가 있는 실제 파일을 기록한다.

```bash
run record-build-result python3 -B - <<'PY'
import datetime, hashlib, json, os
from pathlib import Path
run = Path(os.environ["RUN"])
build = Path(os.environ["YOCTO_BUILD_DIR"])
image = Path(os.environ["WIC"]).resolve()
assert json.loads((run/"dom0-build.command.json").read_text())["rc"] == 0
for label in ("wic-boot", "dom0-rootfs", "wic-packages"):
    assert json.loads((run/(label+".command.json")).read_text())["rc"] == 0
inputs = json.loads((run/"BUILD-INPUT.json").read_text())
with image.open("rb") as stream:
    digest = hashlib.file_digest(stream, "sha256").hexdigest()
record = {"schema": 1, "build_exit_code": 0,
    "finished_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "build_directory": str(build), "source_commit": inputs["source_commit"],
    "build_input_sha256": hashlib.sha256((run/"BUILD-INPUT.json").read_bytes()).hexdigest(),
    "source_dirty": bool(inputs["status"]),
    "source_patch_sha256": hashlib.sha256((run/"source.patch").read_bytes()).hexdigest(),
    "wic": {"path": str(image), "bytes": image.stat().st_size, "sha256": digest}}
(run/"BUILD-RESULT.json").write_text(json.dumps(record, indent=2)+"\n")
PY

run check-before-publish python3 -B "$RUN/tools/inputs.py" check
record_sha="$(sha256sum "$RUN/BUILD-RESULT.json" | cut -d' ' -f1)"
run publish-dom0-plan python3 -B "$REPO/platform/scripts/publish-xenova-platform.py" \
  --build-record "$RUN/BUILD-RESULT.json" --build-record-sha256 "$record_sha" \
  --output-dir "$RUN/published-dom0"
run publish-dom0 python3 -B "$REPO/platform/scripts/publish-xenova-platform.py" \
  --build-record "$RUN/BUILD-RESULT.json" --build-record-sha256 "$record_sha" \
  --output-dir "$RUN/published-dom0" --execute
```

조립기는 게시된 WIC·완료 기록·release manifest·해시를 입력으로 받는다.

## 7. 조립 설정·용량 계획

resolver가 확인한 템플릿 참조와 게시된 dom0 정보를 조립 설정으로 합친다.

```bash
run assembly-config python3 -B - <<'PY'
import json, os, sys
from pathlib import Path
repo, run = Path(os.environ["REPO"]), Path(os.environ["RUN"])
sys.path.insert(0, str(repo/"platform/first-install"))
from completed_artifacts import platform_config
config = {"schema": 1, "template_policy": "full",
          "operating_reserve_bytes": 2*1024**3,
          **platform_config(run/"published-dom0"),
          **json.loads((run/"template-references.json").read_text())}
(run/"deployment.config.json").write_text(json.dumps(config, indent=2)+"\n")
PY

export CARD_BYTES=63864569856
run assembly-plan python3 -B "$REPO/platform/scripts/assemble-xenova-first-install.py" \
  plan --config "$RUN/deployment.config.json" --output-dir "$RUN/final-image" \
  --work-dir "$RUN/assembly-work" --card-bytes "$CARD_BYTES"
cp "$RUN/logs/assembly-plan.stdout" "$RUN/assembly-plan.json"
```

`CARD_BYTES`는 사용할 카드의 실제 바이트 수다. 위 값은 기존 64GB 카드에서 측정한 값이다. 계획의 `capacity`에서 최종 이미지 크기, 최소 카드 용량, WSL scratch, VM 향후 증가량·운영 여유를 확인한다. Ubuntu·Debian 원본 템플릿 2종을 보관하고 기본 Linux seed는 Debian 1개를 만든다.

WSL 가상 디스크가 저장된 Windows 볼륨도 함께 확인한다.

```bash
python3 -B - <<'PY'
import json, os, shutil
from pathlib import Path
run = Path(os.environ["RUN"])
p = json.loads((run/"assembly-plan.json").read_text())
c = p["capacity"]
allocated = sum(v["allocated_bytes"] for v in p["linux_templates"]+p["guests"])
stage = (allocated+c["filesystem_metadata_allowance_bytes"]+
         c["boot_catalog_bytes"]+c["retained_firmware_bytes"]+1024**3)
host_peak = c["image_bytes"]+c["platform_bytes"]+stage+3*1024**3
free = {"WSL": shutil.disk_usage(run).free, "Windows": shutil.disk_usage("/mnt/c").free}
print(json.dumps({"capacity": c, "available": free,
                  "host_peak_plus_compression_bytes": host_peak}, indent=2))
assert free["WSL"] > c["wsl_scratch_upper_bound_bytes"]+3*1024**3
assert free["Windows"] > host_peak+3*1024**3
PY
```

## 8. seed 생성과 최종 조립·검증

조립 실행 안에서 Debian seed와 Zephyr A/B의 독립 firmware 자산, 카탈로그, factory metadata가 준비된다. Debian seed의 machine-id·SSH 키와 각 VM의 UUID/MAC는 최초 보드 부팅에서 확정한다. CPU 프리셋은 공통 이미지에 보드 UUID를 넣지 않고, 첫 identity plan에서 확정한 A/B UUID에 연결한다. 기존 identity plan·CPU 정책을 새 기본값으로 덮어쓰지 않는다.

```bash
run check-before-assembly python3 -B "$RUN/tools/inputs.py" check
run assemble sudo env GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory \
  GIT_CONFIG_VALUE_0="$REPO" unshare --mount --propagation private \
  python3 -B "$REPO/platform/scripts/assemble-xenova-first-install.py" \
  assemble --config "$RUN/deployment.config.json" \
  --output-dir "$RUN/final-image" --work-dir "$RUN/assembly-work" \
  --card-bytes "$CARD_BYTES" --execute

run verify-final python3 -B "$REPO/platform/scripts/assemble-xenova-first-install.py" \
  verify --manifest "$RUN/final-image/xenova-first-install.manifest.json"

run inspect-final sudo env REPO="$REPO" \
  XENOVA_DOM0_SYSTEMD_ANALYZE="$XENOVA_DOM0_SYSTEMD_ANALYZE" \
  unshare --mount --propagation private python3 -B "$RUN/tools/check-image.py" \
  "$RUN/final-image/xenova-first-install.img" --output "$RUN/final-inspection" --final
run check-after-assembly python3 -B "$RUN/tools/inputs.py" check
```

최종 검사는 boot/root/data 파티션 해시, 실제 설치 파일과 source, 전체 카탈로그 payload, Linux 템플릿·Debian seed의 Paho/Agent import·enable·unit graph, A/B network/graceful firmware와 보드별 초기 신원 조건을 확인한다.

## 9. 압축·해시·PC 인계

Windows 사용자 이름과 전달 위치를 지정한다.

```bash
export EXPORT="/mnt/c/Users/<Windows사용자>/Documents/Codex/$RUN_ID"
mkdir -p "$EXPORT"
test ! -e "$EXPORT/xenova-first-install.img.zst.partial"
zstd --no-progress -T4 -3 --stdout "$RUN/final-image/xenova-first-install.img" \
  >"$EXPORT/xenova-first-install.img.zst.partial"
zstd -t "$EXPORT/xenova-first-install.img.zst.partial"
python3 -B - <<'PY'
import hashlib, json, os, subprocess
from pathlib import Path
r = Path(os.environ["RUN"])
m = json.loads((r/"final-image/xenova-first-install.manifest.json").read_text())
archive = Path(os.environ["EXPORT"])/"xenova-first-install.img.zst.partial"
digest, size = hashlib.sha256(), 0
with subprocess.Popen(["zstd", "-dc", str(archive)], stdout=subprocess.PIPE) as process:
    while block := process.stdout.read(8*1024*1024):
        digest.update(block)
        size += len(block)
    assert process.wait() == 0
assert digest.hexdigest() == m["image_sha256"]
assert size == m["capacity"]["image_bytes"]
result = {"bytes": size, "sha256": digest.hexdigest()}
(r/"decompressed-image.json").write_text(json.dumps(result, indent=2)+"\n")
print("PASS full decompressed image", result)
PY
wslpath -w "$EXPORT"
```

**PowerShell**에서 위에 출력된 전달 경로로 파일 이름을 확정한다.

```powershell
$delivery = 'C:\Users\<Windows사용자>\Documents\Codex\<RUN_ID>'
[System.IO.File]::Move(
    (Join-Path $delivery 'xenova-first-install.img.zst.partial'),
    (Join-Path $delivery 'xenova-first-install.img.zst')
)
Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $delivery 'xenova-first-install.img.zst')
```

다시 **WSL Bash**에서 이미지와 함께 입력·명령·로그·manifest·소스 상태를 보관한다.

```bash
cp "$RUN/final-image/xenova-first-install.manifest.json" \
   "$RUN/BUILD-INPUT.json" "$RUN/BUILD-RESULT.json" "$RUN/deployment.config.json" \
   "$RUN/source.patch" "$EXPORT/"
cp -a "$RUN/logs" "$EXPORT/"
cp "$RUN/"*.command.json "$EXPORT/"
python3 -B - <<'PY'
import os, shutil
from pathlib import Path
run, out = Path(os.environ["RUN"]), Path(os.environ["EXPORT"])
for name in ("tools", "dom0-inspection", "final-inspection", "wic-boot"):
    dest = out/name
    dest.mkdir()
    for p in (run/name).iterdir():
        if p.is_file():
            shutil.copyfile(p, dest/p.name)
for pattern in ("official-inputs.json", "verifier-inputs.sha256",
                "zephyr-python-install.json", "template-references.json",
                "assembly-plan.json", "decompressed-image.json"):
    p = run/pattern
    if p.is_file():
        shutil.copyfile(p, out/p.name)
PY
sha256sum "$EXPORT/xenova-first-install.img.zst" \
  >"$RUN/compressed-image.sha256"
(
  cd "$EXPORT"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum >SHA256SUMS
  sha256sum -c SHA256SUMS
)
```

### 완료 산출물

- `final-image/xenova-first-install.img`와 조립 manifest: 최종 원본 이미지·파티션/공통 신원·카탈로그 근거
- `published-dom0/`: 검증한 독립 WIC·완료 기록·release manifest
- `RELEASE_ROOT`: 검증한 Linux·Zephyr 불변 게시본
- `BUILD-INPUT.json`·`source.patch`: 실제 소스·레이어·설정 입력
- `*.command.json`·`logs/`·검사 `RESULT.json`: 단계별 종료 코드·전체 출력·검사 결과
- PC 전달 폴더의 `xenova-first-install.img.zst`·`SHA256SUMS`: 압축 이미지와 전달물 해시

이미지 인계 이후에는 같은 이미지 해시 기준으로 다음을 확인한다. 자세한 명령·반환값은 [CLI·Manager 명세](cli-manager-spec.md)를 따른다.

| 확인 대상 | 수락 근거 |
|---|---|
| 최초 등록·신원 | 기본 세 VM의 UUID/MAC, Linux machine-id·SSH 공개키, seed 등록 완료 |
| CPU 준비·자동 시작 | first-boot·Xen ready → CPU prepare → VM autostart → host admission 순서. `cpu-layout --json`의 host.ready와 실제 모든 vCPU hard mask |
| 재부팅·복구 | A/B 협조형 reboot·Linux 일반 reboot·recover_once·host 재부팅 뒤 같은 정책 revision과 실제 배치, 새로운 실행 신원 |
| 서비스 | dom0-manager·recovery worker enable/active, xentop 통계 조회, broker 인증·구독·건강 상태 게시 |
| 보드별 비밀 | 최초 API 서비스 시작에서 root 전용 HTTP 토큰 생성, 기존 토큰 보존. MQTT 계정과 별도로 취급 |
| 접속·게스트 | Wi-Fi 자동 재연결·SSH, Paho/Agent import·enable, 실제 broker 종단 연결, A/B Ethernet·협조형 종료 |

CPU 정책이 없는 기존 설치의 활성화는 모든 관리 게스트가 정지한 유지보수 창에서 `cpu-policy-set` 계획·실행 → 내부 `cpu-host-prepare` → 실제 배치 조회 순서로 진행한다. 정책 저장과 현재 host 적용은 서로 다른 단계다.

일반 UART 콘솔의 커널 출력은 `loglevel=3`이며 연결 중 채널 진단도 `dmesg`·journal에서 조회한다. 진단용 부팅의 상세 로그 설정은 별도로 유지한다. Wi-Fi 연결 요청 응답과 실제 인증·DHCP 완료는 구분한다.

공통 이미지에는 HTTP 토큰·MQTT 암호·개인 Wi-Fi 설정을 넣지 않는다. 설치 후 `xenova-dom0-manager.service`가 `dom0_manager.service_token`을 실행해 `/etc/xenova/dom0-manager-token.env`를 0600으로 준비한다. `/etc/xenova/dom0-manager.env`의 기존 서비스 토큰은 우선하며 자동 회전하지 않는다. worker는 `/etc/xenova/recovery-worker.env`의 `XENOVA_RECOVERY_*` 설정을 읽고, broker 연결 문제로 VM 자동 시작이나 host admission을 외부 online 대기에 묶지 않는다.

pinning 기능 수락과 성능 효과는 나누어 기록한다. 동일 펌웨어·입력·broker 설정에서 pinning 전/후 × Debian 부하 유무를 비교하고, guest 계산 주기와 Gateway 지연 각각의 p95/p99·관측 최대·deadline miss·오류/손실을 보관한다. 실제 주행 앱이 없는 경우 타이밍 harness 결과로 범위를 명시한다.

## 10. XenStore G1 시험 인계물

G1은 기본 A/B와 분리한 XenStore·VIF 관측 시험이다. 별도 펌웨어와 같은 소스 기준의
플랫폼 런타임을 하나의 인계물로 준비한다. 1·2절에서 정한 `REPO`, `RUN`,
고정 Zephyr·SDK·SPDX 입력과 기록 도구를 사용한다.

**WSL 빌드 PC:**

```bash
run g1-build python3 -B "$REPO/platform/scripts/build-xenova-xenstore-g1.py" \
  --zephyr-source "$ZEPHYR_SOURCE" --tools "$ZEPHYR_TOOLS" \
  --spdx "$SPDX" --toolchain-archive "$SDK_ARCHIVE" \
  --output "$RUN/g1-build" --execute

run g1-handoff-plan python3 -B \
  "$REPO/platform/scripts/package-xenova-xenstore-g1-handoff.py" package \
  --source-root "$REPO" --firmware "$RUN/g1-build/firmware" \
  --output "$RUN/g1-handoff"

run g1-handoff-package python3 -B \
  "$REPO/platform/scripts/package-xenova-xenstore-g1-handoff.py" package \
  --source-root "$REPO" --firmware "$RUN/g1-build/firmware" \
  --output "$RUN/g1-handoff" --execute

run g1-handoff-verify python3 -B \
  "$REPO/platform/scripts/package-xenova-xenstore-g1-handoff.py" verify \
  --directory "$RUN/g1-handoff"

(cd "$RUN/g1-handoff" && sha256sum -c SHA256SUMS)
```

`package` 기본은 계획 모드이며 출력·staging을 만들지 않는다. `--execute`는 새 인계
디렉터리에만 게시한다. 펌웨어의 앱/모듈·빌드 스크립트·reference lock이 현재 소스와
일치해야 하므로 문서가 들어 있는 소스 디렉터리의 변경도 빌드 입력으로 기록한다.
기존 G1 빌드 영수증이나 게시 파일은 수정하지 않고 새 실행 경로로 준비한다.

인계물은 다음 구조를 유지한다.

| 경로 | 내용 |
|---|---|
| `RUNBOOK.md` | 이 절과 CLI 명세를 연결하는 시작 문서 |
| `docs/platform/build-guide.md` | 현재 빌드·G1 인계 절차의 원문 |
| `docs/platform/cli-manager-spec.md` | 공통 VM 제어·신원·복구 계약의 원문 |
| `platform/`의 lock 파일 | 문서에서 참조하는 고정 빌드·템플릿 입력 |
| `firmware/` | 실제 G1 ELF/bin/map/config/DTS·라이선스·`g1-build.json` |
| `runtime/` | 지연 import까지 포함한 독립 시험용 플랫폼 라이브러리 |
| `scripts/xenova-xenstore-g1-lab.py`, `test.cfg.in` | 소유 시험 도메인 제어 도구·설정 원형 |
| `handoff-manifest.json`, `SHA256SUMS` | 문서를 포함한 파일 목록·크기·해시 |

**dom0에서의 인계 확인:** 빌드 PC가 검증한 전체 인계 디렉터리를 새 경로에 전달하고
`HANDOFF`를 보드의 실제 절대 경로로 지정한다. 독립 런타임은 `--platform-dir`로 선택한다.

```bash
test -n "$HANDOFF"
(cd "$HANDOFF" && sha256sum -c SHA256SUMS)
python3 -B "$HANDOFF/scripts/xenova-xenstore-g1-lab.py" \
  --platform-dir "$HANDOFF/runtime" --help
```

실보드 시험에서는 아래 도구의 각 하위 명령을 사용한다. 모든 명령의
`--platform-dir`, `--log-directory`는 하위 명령 앞에 둔다.

| 하위 명령 | 입력·동작 |
|---|---|
| `preflight` | 도구·Xen·브리지·현재 도메인을 읽어 기준선 기록 |
| `prepare` | `--firmware`, 새 `--directory`, `xg1-*` `--name`, 새 `--uuid`·`--mac`으로 중복 검사 후 시험 설정 생성 |
| `launch --execute` | `--directory`의 소유 도메인을 paused 상태로 생성 |
| `inspect` | `--directory`의 UUID/name/domid·VIF frontend/backend/MAC 대조 |
| `resume --execute` | `--directory`의 같은 도메인을 unpause |
| `signal --execute` | `--directory`와 게스트가 출력한 `--path`, 시험용 `--value`로 해당 control 노드만 변경 |
| `cleanup --execute` | `--directory`, `--confirm-uuid`로 같은 시험 도메인 회수. `--remove-config`는 소유 설정 두 파일도 정리 |

실보드 실행은 승인된 시험 범위에서 수행하고 기본 VM·등록 자산과 구분한다.
게스트의 `watch-ready path=...`가 출력되면 60초 예산 안에 그 경로로 `signal`을 보낸다.
`watch-reread`의 변경 값과 단계별 rc, `exercise-result`, `test-node-cleanup`,
`store-cleanup` 및 도메인 회수 증거를 함께 보관한다. 알림 수신 뒤에도 값을 다시 읽는다.
신호를 보내지 않는 별도 시험에서는 `watch-timeout`과 자원 회수를 확인한다.
부모 `/local/domain/<self>/data`가 없거나 권한이 거절되면 해당 오류를 기록하고 중단한다.
Connected·ring-ref·도메인 루트 권한을 대신 써 넣어 통과시키지 않는다.

G1은 Store·권한·transaction·watch·회수를 검증한다. Ethernet packet 송수신·DHCP·
MQTT·앱 준비는 Network Starter와 해당 앱의 별도 수락 근거로 기록한다.
