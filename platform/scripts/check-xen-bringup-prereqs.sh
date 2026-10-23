#!/usr/bin/env bash
# 현재 BitBake 환경의 커널 .config에서 필수 옵션을 검사한다. 빌드 태스크는 실행하지 않는다.
set -euo pipefail
command -v bitbake >/dev/null || { echo 'Please source the Yocto environment first' >&2; exit 1; }
B="$(bitbake -e linux-raspberrypi | sed -n 's/^B="\([^"]*\)"$/\1/p')"
[[ -n "$B" ]] || { echo 'Could not resolve kernel B. Check the active BitBake environment.' >&2; exit 1; }
if [[ ! -f "$B/.config" ]]; then
    printf 'Kernel .config is missing: %s\n' "$B/.config" >&2
    printf 'Run in this build environment: bitbake -c configure linux-raspberrypi\nThen rerun this check.\n' >&2
    exit 1
fi
echo "Kernel config: $B/.config"
errors=0
for symbol in XEN XEN_DOM0 XEN_NETDEV_FRONTEND XEN_NETDEV_BACKEND HVC_XEN BRIDGE BLK_DEV_INITRD RD_GZIP DEVTMPFS; do
    line="$(grep -E "^CONFIG_${symbol}=" "$B/.config" || true)"
    [[ -n "$line" ]] || line="$(grep -E "^# CONFIG_${symbol} is not set" "$B/.config" || true)"
    printf '%-35s %s\n' "$symbol" "${line:-NOT PRESENT}"
    case "$symbol" in
        XEN|XEN_DOM0|XEN_NETDEV_FRONTEND|HVC_XEN|BLK_DEV_INITRD|RD_GZIP|DEVTMPFS)
            if [[ "$line" != "CONFIG_${symbol}=y" ]]; then
                echo "  [BLOCKER] ${symbol} must be built-in for the provided tiny domU initramfs"
                errors=$((errors + 1))
            fi
            ;;
        XEN_NETDEV_BACKEND|BRIDGE)
            if [[ "$line" != "CONFIG_${symbol}=y" && "$line" != "CONFIG_${symbol}=m" ]]; then
                echo "  [BLOCKER] ${symbol} must be y or m in dom0"
                errors=$((errors + 1))
            fi
            ;;
    esac
done
printf '\nKernel prereq blockers: %s\n' "$errors"
if [[ "$errors" -ne 0 ]]; then
    echo 'Do NOT build this bring-up image unchanged yet. Share this report for kernel/guest adjustments.' >&2
    exit 2
fi
printf '\n[OK] Config prerequisite gate passed (does NOT prove real-board boot).\n'
