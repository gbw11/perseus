#!/usr/bin/env bash
# Perseus: prepare a pinned Yocto / Raspberry Pi 4 + Xen build environment.
# Run with: bash scripts/setup-yocto.sh [--verify]
# This script NEVER builds an image or modifies existing source checkouts.
set -Eeuo pipefail

log() { printf '\n[setup-yocto] %s\n' "$*"; }
die() { printf '\n[setup-yocto] ERROR: %s\n' "$*" >&2; exit 1; }

verify=0
case "${1:-}" in
  '') ;;
  --verify) verify=1 ;;
  --help|-h)
    cat <<'HELP'
Usage: bash scripts/setup-yocto.sh [--verify]

Optional environment variables:
  YOCTO_WORKSPACE   default: $HOME/yocto-workspace
  YOCTO_BUILD_DIR   default: $YOCTO_WORKSPACE/build-rpi4-xen-repro

--verify: parse recipes and verify the custom U-Boot bbappend metadata.
The existing build-rpi4-xen directory is not touched by default.
HELP
    exit 0 ;;
  *) die "Unknown argument: $1" ;;
esac

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
LOCK_FILE="$PROJECT_ROOT/platform/yocto/layers.lock"
FRAGMENT="$PROJECT_ROOT/platform/yocto/config/local.conf.fragment"
CUSTOM_LAYER="$PROJECT_ROOT/platform/yocto/meta-perseus"
WORKSPACE="${YOCTO_WORKSPACE:-$HOME/yocto-workspace}"
BUILD_DIR="${YOCTO_BUILD_DIR:-$WORKSPACE/build-rpi4-xen-repro}"
LAYERS_DIR="$WORKSPACE/layers"

[[ -f "$LOCK_FILE" ]] || die "Missing $LOCK_FILE"
[[ -f "$FRAGMENT" ]] || die "Missing $FRAGMENT"
[[ -f "$CUSTOM_LAYER/conf/layer.conf" ]] || die "Missing meta-perseus/conf/layer.conf"
[[ -f "$CUSTOM_LAYER/recipes-bsp/xen-rpi-u-boot-scr/xen-rpi-u-boot-scr.bbappend" ]] || \
  die "Missing Xen U-Boot compatibility .bbappend"
command -v git >/dev/null || die 'git is not installed'
command -v python3 >/dev/null || die 'python3 is not installed'
[[ "$WORKSPACE" == /* && "$BUILD_DIR" == /* ]] || die 'Workspace and build paths must be absolute'
case "$WORKSPACE" in /mnt/[a-zA-Z]/*) die 'Use a WSL Linux filesystem path, not /mnt/c/...';; esac

mkdir -p -- "$LAYERS_DIR"
log 'Check pinned external source repositories'
count=0
while read -r repo url sha extra || [[ -n "${repo:-}" ]]; do
  [[ -z "${repo:-}" || "$repo" == \#* ]] && continue
  [[ -z "${extra:-}" && -n "${url:-}" && "$sha" =~ ^[0-9a-f]{40}$ ]] || \
    die "Invalid layers.lock entry: ${repo:-} ${url:-} ${sha:-} ${extra:-}"
  [[ "$repo" =~ ^[a-zA-Z0-9._-]+$ ]] || die "Invalid repository name: $repo"
  dest="$LAYERS_DIR/$repo"
  if [[ -e "$dest" ]]; then
    git -C "$dest" rev-parse --is-inside-work-tree >/dev/null 2>&1 || \
      die "Not a Git checkout: $dest"
    origin="$(git -C "$dest" remote get-url origin)"
    [[ "$origin" == "$url" ]] || die "$repo origin mismatch: $origin (expected $url)"
    actual="$(git -C "$dest" rev-parse HEAD)"
    [[ "$actual" == "$sha" ]] || \
      die "$repo at $actual (expected $sha). Review and checkout pinned commit manually; refusing to change it."
    [[ -z "$(git -C "$dest" status --porcelain)" ]] || \
      die "$repo has local changes; refusing to continue: $dest"
    printf '[OK] %-21s %s\n' "$repo" "${sha:0:12}"
  else
    branch=wrynose
    [[ "$repo" == bitbake ]] && branch=2.18
    log "Clone $repo ($branch)"
    git clone --branch "$branch" --single-branch -- "$url" "$dest"
    git -C "$dest" cat-file -e "${sha}^{commit}" 2>/dev/null || \
      die "Pinned commit $sha is not in cloned history for $repo"
    git -C "$dest" checkout --detach "$sha"
    printf '[OK] %-21s %s\n' "$repo" "${sha:0:12}"
  fi
  count=$((count + 1))
done < "$LOCK_FILE"
[[ "$count" -eq 6 ]] || die "Expected 6 external repositories in lockfile, found $count"

OE_INIT="$LAYERS_DIR/openembedded-core/oe-init-build-env"
[[ -f "$OE_INIT" ]] || die "Missing $OE_INIT"

log "Initialize build environment: $BUILD_DIR"
# 'source' only affects this subshell, not the user's interactive terminal.
(
  set +u  # Yocto environment initialization may reference unset shell vars.
  source "$OE_INIT" "$BUILD_DIR" >/dev/null
  set -u
  [[ -f conf/local.conf && -f conf/bblayers.conf ]] || die 'Yocto conf files missing after initialization'

  log 'Register layers (skip already registered paths)'
  layers=(
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
    if grep -Fq -- "$layer" conf/bblayers.conf; then
      printf '[OK] Already registered: %s\n' "${layer##*/}"
    else
      bitbake-layers add-layer "$layer"
      printf '[OK] Added: %s\n' "${layer##*/}"
    fi
  done

  log 'Apply local.conf.fragment in an idempotent managed block'
  python3 - "$PWD/conf/local.conf" "$FRAGMENT" <<'PY'
from pathlib import Path
import re
import sys

local_conf = Path(sys.argv[1])
fragment = Path(sys.argv[2]).read_text(encoding="utf-8").rstrip("\n")
original = local_conf.read_text(encoding="utf-8")
begin = "# >>> PERSEUS MANAGED SETTINGS (setup-yocto.sh) >>>"
end = "# <<< PERSEUS MANAGED SETTINGS <<<"
block = f"{begin}\n{fragment}\n{end}"

if begin in original or end in original:
    if original.count(begin) != 1 or original.count(end) != 1:
        sys.exit("ERROR: damaged or duplicated Perseus managed block in local.conf")
    current = original[original.index(begin): original.index(end) + len(end)]
    if current != block:
        sys.exit("ERROR: managed local.conf settings differ from committed fragment; refusing to overwrite")
    print("[OK] Existing managed settings match project fragment")
else:
    keys = ("MACHINE", "DISTRO", "INIT_MANAGER", "DISTRO_FEATURES:append",
            "PREFERRED_PROVIDER_virtual/kernel", "RPI_DOM0_MEM", "BB_NUMBER_THREADS", "PARALLEL_MAKE")
    pattern = re.compile(r"^\s*(?:" + "|".join(map(re.escape, keys)) + r")\s*=", re.M)
    if pattern.search(original):
        sys.exit("ERROR: existing explicit project settings found in local.conf; review before appending")
    local_conf.write_text(original.rstrip("\n") + "\n\n" + block + "\n", encoding="utf-8")
    print("[OK] Added project settings to local.conf")
PY

  if [[ "$verify" -eq 1 ]]; then
    log 'Verify BitBake metadata and Xen U-Boot bbappend'
    bitbake -p
    bitbake-layers show-appends | grep -F -- "$CUSTOM_LAYER/recipes-bsp/xen-rpi-u-boot-scr/xen-rpi-u-boot-scr.bbappend" || \
      die 'Project bbappend is not registered'
    bitbake -e xen-rpi-u-boot-scr | grep -E '^(S|UNPACKDIR)=' || \
      die 'Cannot resolve Xen U-Boot recipe source paths'
    bitbake -e xen-image-minimal | grep -E '^(MACHINE|RPI_DOM0_MEM|PREFERRED_PROVIDER_virtual/kernel)=' || \
      die 'Cannot resolve Xen image settings'
  fi
)

log 'Setup complete. The existing build-rpi4-xen directory is unchanged.'
printf 'To enter the new BitBake environment in your terminal, run:\n'
printf '  source "%s" "%s"\n' "$OE_INIT" "$BUILD_DIR"
printf 'To build afterwards, run: bitbake xen-image-minimal\n'
