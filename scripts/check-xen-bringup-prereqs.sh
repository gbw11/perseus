#!/usr/bin/env bash
# Run only AFTER `source .../oe-init-build-env ...` in your successful build dir.
set -euo pipefail
command -v bitbake >/dev/null || { echo 'Please source the Yocto environment first' >&2; exit 1; }
B="$(bitbake -e linux-raspberrypi | sed -n 's/^B="\([^"]*\)"$/\1/p')"
[[ -n "$B" && -f "$B/.config" ]] || { echo 'Kernel .config missing. Use the original successful build dir.' >&2; exit 1; }
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
