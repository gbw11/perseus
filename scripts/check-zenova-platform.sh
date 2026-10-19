#!/usr/bin/env bash
# 이미지/마운트/실제 Xen 명령 없이 플랫폼 소스를 검사한다. --metadata도 파싱만 한다.
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
[[ $# -le 1 && ( $# -eq 0 || "$1" == --metadata ) ]] || { echo 'Usage: check-zenova-platform.sh [--metadata]' >&2; exit 2; }
export PYTHONDONTWRITEBYTECODE=1
cd "$ROOT"
python3 -B -m unittest discover -s tests/platform -p 'test*.py' -v
python3 -B tests/platform/check_zenova_bringup_regression.py
python3 -B tests/platform/check_zenova_first_install_units.py
bash -n scripts/setup-yocto.sh scripts/check-xen-bringup-prereqs.sh scripts/check-zenova-platform.sh
sh -n platform/yocto/meta-zenova/recipes-core/zenova-domu-init/files/init
sh -n platform/yocto/meta-zenova/recipes-core/zenova-domu-init/files/udhcpc.script
sh -n platform/yocto/meta-zenova/recipes-core/zenova-bringup/files/zenova-bridge-up
sh -n platform/yocto/meta-zenova/recipes-core/zenova-platform/files/zenova-wifi
sh -n platform/yocto/meta-zenova/recipes-core/zenova-platform/files/zenova-vm
sh -n platform/yocto/meta-zenova/recipes-core/zenova-platform/files/zenova-first-boot
git diff --check
[[ "${1:-}" == --metadata ]] || exit 0

WORKSPACE="${YOCTO_WORKSPACE:-$HOME/yocto-workspace}"
ORIGINAL="${YOCTO_BUILD_DIR:-$WORKSPACE/build-rpi4-xen}"
bash scripts/setup-yocto.sh --check
CHECK_DIR="$(mktemp -d /tmp/zenova-platform-static.XXXXXX)"
printf 'Isolated metadata check: %s\n' "$CHECK_DIR"
mkdir -p "$CHECK_DIR/logs"
YOCTO_BUILD_DIR="$CHECK_DIR/build" bash scripts/setup-yocto.sh > "$CHECK_DIR/logs/setup.log" 2>&1
cat >> "$CHECK_DIR/build/conf/local.conf" <<'CONF'
TMPDIR = "${TOPDIR}/tmp"
CACHE = "${TOPDIR}/cache"
PERSISTENT_DIR = "${TOPDIR}/cache"
DL_DIR = "${TOPDIR}/downloads"
SSTATE_DIR = "${TOPDIR}/sstate-cache"
BB_NO_NETWORK = "1"
CONNECTIVITY_CHECK_URIS = ""
BB_HASHSERVE = ""
BB_SIGNATURE_HANDLER = "OEBasicHash"
BB_SERVER_TIMEOUT = "0"
CONF
sha256sum "$ORIGINAL/conf/local.conf" "$ORIGINAL/conf/bblayers.conf" > "$CHECK_DIR/logs/original.sha256"
unset BBPATH BUILDDIR OEROOT BBSERVER
set +u
source "$WORKSPACE/layers/openembedded-core/oe-init-build-env" "$CHECK_DIR/build" > "$CHECK_DIR/logs/environment.log"
set -u
bitbake -p > "$CHECK_DIR/logs/parse.log" 2>&1
for recipe in qemu xen-rpi-u-boot-scr xen-tools openssh zenova-domu-init zenova-domu-initramfs zenova-bringup zenova-platform zenova-xen-bringup-image; do
    bitbake -e "$recipe" > "$CHECK_DIR/logs/$recipe.env" 2> "$CHECK_DIR/logs/$recipe.err"
done
bitbake-getvar -r zenova-xen-bringup-image --value -f depends do_image_wic > "$CHECK_DIR/logs/wic-depends.txt" 2> "$CHECK_DIR/logs/wic-depends.err"
sha256sum -c "$CHECK_DIR/logs/original.sha256"
python3 -B "$ROOT/tests/platform/check_zenova_metadata.py" "$CHECK_DIR/logs"
tail -n 12 "$CHECK_DIR/logs/parse.log"
