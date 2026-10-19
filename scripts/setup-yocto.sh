#!/usr/bin/env bash
# 고정된 외부 소스를 검사하고 별도 Yocto 빌드 환경을 구성한다. 이미지는 빌드하지 않는다.
set -Eeuo pipefail

log() { printf '\n[setup-yocto] %s\n' "$*"; }
die() { printf '\n[setup-yocto] ERROR: %s\n' "$*" >&2; exit 1; }

verify=0
check=0
[[ $# -le 1 ]] || die 'Expected at most one option'
case "${1:-}" in
  '') ;;
  --verify) verify=1 ;;
  --check) check=1 ;;
  --help|-h)
    cat <<'HELP'
Usage: bash scripts/setup-yocto.sh [--check|--verify]

Optional environment variables (absolute Linux paths without whitespace):
  YOCTO_WORKSPACE   default: $HOME/yocto-workspace
  YOCTO_BUILD_DIR   default: $YOCTO_WORKSPACE/build-rpi4-zenova

--check: read-only check of existing external URLs, commits and working trees.
--verify: configure a new/owned build directory, show layers and parse all images.
Existing unowned build directories and external source changes are refused.
No option builds an image; explicit BitBake commands are printed afterwards.
HELP
    exit 0 ;;
  *) die "Unknown argument: $1" ;;
esac

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
LOCK_FILE="$PROJECT_ROOT/platform/yocto/layers.lock"
FRAGMENT="$PROJECT_ROOT/platform/yocto/config/local.conf.fragment"
CUSTOM_LAYER="$PROJECT_ROOT/platform/yocto/meta-zenova"
WORKSPACE="${YOCTO_WORKSPACE:-$HOME/yocto-workspace}"
BUILD_DIR="${YOCTO_BUILD_DIR:-$WORKSPACE/build-rpi4-zenova}"

[[ -f "$LOCK_FILE" && -f "$FRAGMENT" ]] || die 'Missing lockfile or config fragment'
[[ -f "$CUSTOM_LAYER/conf/layer.conf" ]] || die 'Missing meta-zenova'
command -v git >/dev/null || die 'git is not installed'
command -v python3 >/dev/null || die 'python3 is not installed'
[[ "$(uname -s)" == Linux ]] || die 'Run in Linux or WSL, on a Linux filesystem'

# 잘못된 잠금 파일이나 경로는 clone/conf 생성 전에 거부한다.
python3 - "$LOCK_FILE" "$PROJECT_ROOT" "$WORKSPACE" "$BUILD_DIR" <<'PY'
from pathlib import Path
import re
import sys

for path in sys.argv[2:]:
    if not path.startswith("/") or any(c.isspace() or c in "\"'\\$" for c in path):
        sys.exit("ERROR: use absolute Linux paths without whitespace, quotes, backslash or $")
    if re.match(r"^/mnt/[a-zA-Z](?:/|$)", path):
        sys.exit("ERROR: keep project, sources and build on a Linux filesystem")
expected = {"openembedded-core", "bitbake", "meta-yocto", "meta-raspberrypi",
            "meta-openembedded", "meta-virtualization"}
seen = set()
for number, line in enumerate(Path(sys.argv[1]).read_text().splitlines(), 1):
    if not line.strip() or line.lstrip().startswith("#"):
        continue
    fields = line.split()
    if len(fields) != 3:
        sys.exit(f"ERROR: invalid layers.lock line {number}")
    repo, url, sha = fields
    if repo not in expected or repo in seen or not re.fullmatch(r"[0-9a-f]{40}", sha):
        sys.exit(f"ERROR: invalid or duplicate repository/commit at line {number}")
    if not re.fullmatch(r"https://[A-Za-z0-9.-]+/[A-Za-z0-9/_.-]+", url):
        sys.exit(f"ERROR: expected public HTTPS URL without credentials at line {number}")
    seen.add(repo)
if seen != expected:
    sys.exit("ERROR: layers.lock must contain exactly the six expected repositories")
PY
WORKSPACE="$(realpath -m -- "$WORKSPACE")"
BUILD_DIR="$(realpath -m -- "$BUILD_DIR")"
LAYERS_DIR="$WORKSPACE/layers"
if [[ "$check" -eq 0 && -e "$BUILD_DIR" ]]; then
  [[ -d "$BUILD_DIR" ]] || die "Not a directory: $BUILD_DIR"
  if [[ -n "$(find "$BUILD_DIR" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
    grep -Fq '# >>> ZENOVA MANAGED SETTINGS (setup-yocto.sh) >>>' "$BUILD_DIR/conf/local.conf" 2>/dev/null &&
      grep -Fq '# >>> ZENOVA MANAGED LAYERS (setup-yocto.sh) >>>' "$BUILD_DIR/conf/bblayers.conf" 2>/dev/null ||
      die "Existing build is not owned by this setup version; use a new build directory: $BUILD_DIR"
  fi
fi

[[ "$check" -eq 1 ]] || mkdir -p -- "$LAYERS_DIR"
log 'Check pinned external source repositories'
while read -r repo url sha extra || [[ -n "${repo:-}" ]]; do
  [[ -z "${repo:-}" || "$repo" == \#* ]] && continue
  dest="$LAYERS_DIR/$repo"
  if [[ ! -e "$dest" ]]; then
    [[ "$check" -eq 0 ]] || die "Missing checkout: $dest"
    branch=wrynose
    [[ "$repo" == bitbake ]] && branch=2.18
    log "Clone $repo (transport branch: $branch; version from layers.lock)"
    git clone --no-checkout --branch "$branch" --single-branch -- "$url" "$dest"
    git -C "$dest" cat-file -e "${sha}^{commit}" ||
      die "Pinned commit unavailable in cloned history: $repo"
    git -C "$dest" checkout --detach "$sha"
  fi
  actual_root="$(git -C "$dest" rev-parse --show-toplevel)" || die "Not a checkout: $dest"
  [[ "$actual_root" == "$(cd -- "$dest" && pwd -P)" ]] || die "Not a repository root: $dest"
  origin="$(git -C "$dest" remote get-url origin)"
  [[ "$origin" == "$url" ]] || die "$repo origin differs from layers.lock (URL withheld)"
  actual="$(git -C "$dest" rev-parse HEAD)"
  [[ "$actual" == "$sha" ]] || die "$repo at $actual (expected $sha); refusing to change existing checkout"
  [[ -z "$(git -C "$dest" status --porcelain --untracked-files=all)" ]] ||
    die "$repo has local changes; refusing to continue: $dest"
  printf '[OK] %-21s %s\n' "$repo" "$sha"
done < "$LOCK_FILE"
[[ "$check" -eq 0 ]] || { log 'Read-only source check passed'; exit 0; }

OE_INIT="$LAYERS_DIR/openembedded-core/oe-init-build-env"
[[ -f "$OE_INIT" ]] || die "Missing $OE_INIT"
layers=(
  "$LAYERS_DIR/openembedded-core/meta"
  "$LAYERS_DIR/meta-yocto/meta-poky"
  "$LAYERS_DIR/meta-openembedded/meta-oe"
  "$LAYERS_DIR/meta-openembedded/meta-python"
  "$LAYERS_DIR/meta-openembedded/meta-networking"
  "$LAYERS_DIR/meta-openembedded/meta-filesystems"
  "$LAYERS_DIR/meta-raspberrypi"
  "$LAYERS_DIR/meta-virtualization"
  "$CUSTOM_LAYER"
)
for layer in "${layers[@]}"; do
  [[ -f "$layer/conf/layer.conf" ]] || die "Layer config missing: $layer"
done

log "Initialize build environment: $BUILD_DIR"
(
  # 호출한 셸의 다른 Yocto 환경이나 TEMPLATECONF를 새 구성에 가져오지 않는다.
  unset BBPATH BUILDDIR OEROOT BBSERVER
  export TEMPLATECONF="$LAYERS_DIR/openembedded-core/meta/conf/templates/default"
  set +u
  source "$OE_INIT" "$BUILD_DIR" >/dev/null
  set -u
  [[ -f conf/local.conf && -f conf/bblayers.conf ]] || die 'Yocto conf files missing'

  # BSP·DISTRO·의존 레이어를 모두 구성한 뒤 BitBake를 실행한다.
  python3 - "$PWD/conf" "$FRAGMENT" "${layers[@]}" <<'PY'
from pathlib import Path
import re
import sys

conf = Path(sys.argv[1])
fragment = Path(sys.argv[2]).read_text().rstrip("\n")
local_begin = "# >>> ZENOVA MANAGED SETTINGS (setup-yocto.sh) >>>"
local_end = "# <<< ZENOVA MANAGED SETTINGS <<<"
layers_begin = "# >>> ZENOVA MANAGED LAYERS (setup-yocto.sh) >>>"
layers_end = "# <<< ZENOVA MANAGED LAYERS <<<"
layer_setting = 'BBLAYERS = " \\\n' + "".join(f"  {p} \\\n" for p in sys.argv[3:]) + '  "'
keys = ("MACHINE", "DISTRO", "INIT_MANAGER", "DISTRO_FEATURES:append",
        "PREFERRED_PROVIDER_virtual/kernel", "RPI_DOM0_MEM", "BB_NUMBER_THREADS", "PARALLEL_MAKE")
explicit_setting = re.compile(r"^\s*(?:" + "|".join(map(re.escape, keys)) + r")\s*(?:=|:=|\?=|\+=|=\+|\.=|=\.)", re.M)

def prepare(path, begin, body, end):
    original = path.read_text()
    block = f"{begin}\n{body}\n{end}"
    if begin in original or end in original:
        if original.count(begin) != 1 or original.count(end) != 1 or original.index(end) < original.index(begin):
            sys.exit(f"ERROR: damaged managed block: {path}")
        start, stop = original.index(begin), original.index(end) + len(end)
        if original[start:stop] != block:
            sys.exit(f"ERROR: managed settings differ; refusing to overwrite: {path}")
        outside = original[:start] + original[stop:]
        updated = original
    else:
        outside = original
        updated = original.rstrip("\n") + "\n\n" + block + "\n"
    if path.name == "local.conf" and explicit_setting.search(outside):
        sys.exit(f"ERROR: conflicting explicit project settings outside managed block: {path}")
    return updated

# 두 설정 모두 검사에 성공해야 파일을 쓴다. 반복 실행은 내용을 변경하지 않는다.
updates = [
    (conf / "local.conf", local_begin, fragment, local_end),
    (conf / "bblayers.conf", layers_begin, layer_setting, layers_end),
]
prepared = [(path, prepare(path, begin, body, end)) for path, begin, body, end in updates]
for path, content in prepared:
    if path.read_text() != content:
        path.write_text(content)
    print(f"[OK] Managed configuration: {path}")
PY

  if [[ "$verify" -eq 1 ]]; then
    log 'Verify registered layers and all image metadata (no compilation)'
    bitbake-layers show-layers
    bitbake -p
    bitbake-layers show-appends | grep -F -- "$CUSTOM_LAYER/recipes-bsp/xen-rpi-u-boot-scr/xen-rpi-u-boot-scr.bbappend" ||
      die 'Project U-Boot bbappend is not registered'
    bitbake -e xen-rpi-u-boot-scr | python3 -c '
import re, sys
values = dict(re.findall(r"^(S|UNPACKDIR)=\"(.*)\"$", sys.stdin.read(), re.M))
if not values.get("S") or values.get("S") != values.get("UNPACKDIR"):
    sys.exit("ERROR: Xen boot script requires S == UNPACKDIR")
for key, value in values.items():
    print(f"{key}={value}")
'
    for target in xen-image-minimal zenova-xen-bringup-image zenova-domu-initramfs; do
      bitbake -e "$target" | python3 -c '
import re, sys
values = dict(re.findall(r"^([A-Za-z0-9_:/.-]+)=\"(.*)\"$", sys.stdin.read(), re.M))
expected = {"PN": sys.argv[1], "MACHINE": "raspberrypi4-64", "DISTRO": "poky",
            "INIT_MANAGER": "systemd", "RPI_DOM0_MEM": "1024M",
            "PREFERRED_PROVIDER_virtual/kernel": "linux-raspberrypi"}
for key, value in expected.items():
    if values.get(key) != value:
        sys.exit(f"ERROR: {sys.argv[1]}: unexpected {key}: {values.get(key)}")
for key in (*expected, "IMAGE_FSTYPES", "DEPLOY_DIR_IMAGE"):
    print(f"{key}={values.get(key)}")
' "$target"
    done
  fi
)

log 'Setup complete (no image build or boot test performed)'
printf 'Enter this build environment:\n  source "%s" "%s"\n' "$OE_INIT" "$BUILD_DIR"
printf 'Baseline dom0: bitbake xen-image-minimal\n'
printf 'Bring-up dom0 + embedded test domU: bitbake zenova-xen-bringup-image\n'
printf 'Test domU only: bitbake zenova-domu-initramfs\n'
printf 'Artifacts: %s/tmp/deploy/images/raspberrypi4-64/\n' "$BUILD_DIR"
