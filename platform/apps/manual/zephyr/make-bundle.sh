#!/bin/sh
# Build a new authenticated Xen netfront candidate. No credentials in firmware.
# Usage: XENOVA_ZEPHYR_TOOLS=/absolute/fixed/host-tools ./make-bundle.sh /new/output
set -eu
HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO="$(CDPATH= cd -- "$HERE/../../../.." && pwd)"
: "${ZEPHYR_BASE:?fixed Zephyr source required}"
: "${XENOVA_ZEPHYR_TOOLS:?existing fixed toolchain root required}"
exec python3 -B "$REPO/platform/scripts/build-xenova-drive-app.py" --app manual \
  --zephyr-source "$ZEPHYR_BASE" --tools "$XENOVA_ZEPHYR_TOOLS" --output "${1:?new absolute output required}" --execute
