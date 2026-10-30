#!/bin/sh
# One real app over an already isolated TAP; prefer the two-app acceptance harness.
# Bootstrap environment variables contain PRIVATE FILE PATHS, never passwords.
set -eu
HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
: "${ZEPHYR_BASE:?fixed Zephyr source required}"
: "${XT_TEST_BOOTSTRAP:?private v2 bootstrap path required}"
: "${XD_TEST_APP_BOOTSTRAP:?private separate v1 bootstrap path required}"
: "${XT_TEST_INSTANCE_ID:?test guest UUID required}"
: "${XD_TEST_TAP:?owned TAP in a new network namespace required}"
[ "$(readlink /proc/self/ns/net)" != "$(readlink /proc/1/ns/net)" ] || { echo 'new isolated network namespace required' >&2; exit 2; }
BUILD="${1:?new/existing owned absolute build directory required}"
export ZEPHYR_TOOLCHAIN_VARIANT=host
export XD_TEST_DURATION_MS="${XD_TEST_DURATION_MS:-18000}"
cmake -S "$HERE" -B "$BUILD" -GNinja -DBOARD=native_sim/native/64
cmake --build "$BUILD" -j 4
exec "$BUILD/zephyr/zephyr.exe" -rt -no-color -eth-if="$XD_TEST_TAP"
