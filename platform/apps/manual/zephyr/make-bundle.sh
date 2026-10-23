#!/bin/sh
# 수동주행 앱을 xenvm(Xen arm64 게스트)으로 빌드하고 dom0 에 가져갈 펌웨어 번들을 만든다 (S15P31E110-238).
# Zephyr 4.4.0 작업 공간(ZEPHYR_BASE)과 Zephyr SDK aarch64 도구체인(CROSS_COMPILE), python3 이 있는 Linux 에서 실행한다.
#   ./make-bundle.sh [출력 폴더=./dist] [빌드 디렉터리=/tmp/xenova-manual-xenvm]
# 브로커 계정은 run-native-sim.sh 와 같은 환경변수로 넣는다 (없으면 dom0 192.168.77.1:1883, manual, 익명).
#   XENOVA_MQTT_HOST      IPv4 주소 (기본 192.168.77.1 = dom0 xenbr0)
#   XENOVA_MQTT_PORT      (기본 1883)
#   XENOVA_MQTT_USERNAME  (기본 manual = app_id, ACL 계정)
#   XENOVA_MQTT_PASSWORD  (없으면 익명 접속) — zephyr.bin 에 평문으로 들어간다
# 그 밖에
#   CROSS_COMPILE         예: /opt/zephyr-sdk/aarch64-zephyr-elf/bin/aarch64-zephyr-elf-
#   XENOVA_MANUAL_VERSION 앱 펌웨어 버전 (기본 0.1.0, 뒤에 +g<커밋> 이 붙는다)
#   XENOVA_GIT_COMMIT     git 을 못 읽는 환경(컨테이너 안 worktree 등)에서 넘길 저장소 커밋
#   XENOVA_BUNDLE_DEST    dom0 에 둘 위치 (기본 /srv/xenova/apps/<번들 이름>) — xl cfg 의 kernel 경로
# 번들은 빌드 디렉터리 안에서 만든 뒤 출력 폴더로 복사한다. 출력 폴더의 같은 이름 번들은 지우고 새로 쓴다.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../../.." && pwd)"
OUT="${1:-$HERE/dist}"
BUILD="${2:-/tmp/xenova-manual-xenvm}"
HOST="${XENOVA_MQTT_HOST:-192.168.77.1}"
PORT="${XENOVA_MQTT_PORT:-1883}"
USERNAME="${XENOVA_MQTT_USERNAME:-manual}"
PASSWORD="${XENOVA_MQTT_PASSWORD:-}"
: "${ZEPHYR_BASE:?ZEPHYR_BASE 에 Zephyr 4.4.0 경로를 넣는다}"
: "${CROSS_COMPILE:?CROSS_COMPILE 에 aarch64-zephyr-elf- 접두사를 넣는다 (Zephyr SDK)}"
case "$BUILD" in /*) ;; *) echo "빌드 디렉터리는 절대 경로여야 한다: $BUILD" >&2; exit 1 ;; esac

if [ -n "$PASSWORD" ]; then
  echo "경고: 브로커 비밀번호가 zephyr.bin 에 평문으로 들어간다. 번들(디렉터리·tar.gz)을 저장소·공유 폴더에 올리지 않는다." >&2
fi

# 1. xenvm 빌드 (계정은 매번 네 값 모두 넘겨서 이전 빌드 값이 남지 않게 한다)
export ZEPHYR_TOOLCHAIN_VARIANT=cross-compile CROSS_COMPILE
cmake -S "$HERE" -B "$BUILD" -GNinja -DBOARD=xenvm \
  -DCONFIG_XENOVA_MANUAL_BROKER_HOST="\"$HOST\"" -DCONFIG_XENOVA_MANUAL_BROKER_PORT="$PORT" \
  -DCONFIG_XENOVA_MANUAL_BROKER_USERNAME="\"$USERNAME\"" -DCONFIG_XENOVA_MANUAL_BROKER_PASSWORD="\"$PASSWORD\"" >/dev/null
ninja -C "$BUILD" >/dev/null

# 2. 출처
COMMIT="${XENOVA_GIT_COMMIT:-$(git -C "$HERE" rev-parse HEAD 2>/dev/null || echo unknown)}"
DIRTY=unknown
if git -C "$HERE" rev-parse HEAD >/dev/null 2>&1; then
  if [ -n "$(git -C "$HERE" status --porcelain -- . 2>/dev/null)" ]; then DIRTY=true; else DIRTY=false; fi
fi
SHORT="$(printf '%.7s' "$COMMIT")"
VERSION="${XENOVA_MANUAL_VERSION:-0.1.0}+g$SHORT"
[ "$DIRTY" = true ] && VERSION="$VERSION.dirty"
NAME="xenova-manual-$VERSION"
DEST="${XENOVA_BUNDLE_DEST:-/srv/xenova/apps/$NAME}"
BUILT_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# 3. firmware/ : 기존 팀 앱 번들 관례 (platform/scripts/package-xenova-firmware.py → xenova-vm firmware-replace)
#    .config 의 비밀번호 줄은 가린 사본으로 넘긴다 (번들의 zephyr.config 에 평문이 남지 않게)
WORK="$BUILD/bundle"
INPUT="$BUILD/bundle-input/zephyr"
chmod -R u+w "$WORK" 2>/dev/null || true
rm -rf "$WORK" "$BUILD/bundle-input"
mkdir -p "$WORK/$NAME" "$INPUT/include/generated/zephyr"
cp "$BUILD/zephyr/zephyr.bin" "$BUILD/zephyr/zephyr.elf" "$BUILD/zephyr/zephyr.dts" "$INPUT/"
cp "$BUILD/zephyr/include/generated/zephyr/version.h" "$INPUT/include/generated/zephyr/"
if [ -n "$PASSWORD" ]; then
  sed 's/^CONFIG_XENOVA_MANUAL_BROKER_PASSWORD=.*/CONFIG_XENOVA_MANUAL_BROKER_PASSWORD="(redacted)"/' \
    "$BUILD/zephyr/.config" > "$INPUT/.config"
else
  cp "$BUILD/zephyr/.config" "$INPUT/.config"
fi
python3 -B "$REPO/platform/scripts/package-xenova-firmware.py" \
  --build-dir "$INPUT" --license "$ZEPHYR_BASE/LICENSE" --version "$VERSION" --app xenova-manual \
  --source-revision "$COMMIT" --build-id "$BUILT_AT" --output "$WORK/$NAME/firmware" --execute >/dev/null

# 4. 직접 xl create 용 사본과 예시 cfg (자동 시작은 아직 없다)
cp "$BUILD/zephyr/zephyr.bin" "$WORK/$NAME/zephyr.bin"
chmod 0644 "$WORK/$NAME/zephyr.bin"
chmod 0755 "$WORK/$NAME/firmware"
cat > "$WORK/$NAME/xenova-manual.cfg" <<EOF
# 수동주행 VM xl 설정 예시 ($NAME). 손으로 xl create 할 때만 쓴다 — 자동 시작은 아직 없다 (S15P31E110-238 범위 밖).
# 플랫폼 경로는 xenova-vm create (zephyr-xenvm-starter-arm64) + firmware-replace firmware/ 이고, 그때 이 파일은 쓰지 않는다.
# 값은 platform/guests/zephyr/xen.cfg.in 과 같다 (1 vCPU, 16 MiB, GICv2).
name = "xenova-manual"
# uuid = "<플랫폼 vm_id>"   # 넣으면 앱이 XenStore /vm/<uuid> 에서 vm_id 를 읽는다
kernel = "$DEST/zephyr.bin"
vcpus = 1
memory = 16
gic_version = "v2"
on_poweroff = "destroy"
on_reboot = "destroy"
on_crash = "preserve"
# PV net/block 미지원: vif/disk/initrd 를 지정하지 않는다. 브로커 경로는 vchan + dom0 중계 (S15P31E110-239·-240).
EOF

# 5. manifest.json (비밀번호는 넣지 않고 password_embedded 만)
B_DIR="$WORK/$NAME" B_NAME="$NAME" B_VERSION="$VERSION" B_COMMIT="$COMMIT" B_DIRTY="$DIRTY" B_BUILT_AT="$BUILT_AT" \
B_HOST="$HOST" B_PORT="$PORT" B_USERNAME="$USERNAME" B_EMBEDDED="$([ -n "$PASSWORD" ] && echo true || echo false)" \
B_ZCOMMIT="$(git -C "$ZEPHYR_BASE" rev-parse HEAD 2>/dev/null || echo unknown)" \
B_GCC="$("${CROSS_COMPILE}gcc" --version | head -n 1)" B_DEST="$DEST" \
python3 - "$BUILD/zephyr/include/generated/zephyr/version.h" <<'PY'
import hashlib, json, os, re, sys
from pathlib import Path
e = os.environ
root = Path(e["B_DIR"])
zver = re.search(r'KERNEL_VERSION_STRING\s+"([^"]+)"', Path(sys.argv[1]).read_text())[1]
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
files = {str(p.relative_to(root)): {"sha256": sha(p), "bytes": p.stat().st_size}
         for p in sorted(root.rglob("*")) if p.is_file()}
dirty = {"true": True, "false": False}.get(e["B_DIRTY"])
manifest = {
    "schema": 1, "kind": "xenova-app-firmware-bundle", "name": e["B_NAME"],
    "app": "manual", "app_id": "manual", "version": e["B_VERSION"],
    "source": {"git_commit": e["B_COMMIT"], "git_dirty": dirty},
    "zephyr": {"version": zver, "commit": e["B_ZCOMMIT"], "board": "xenvm", "toolchain": e["B_GCC"]},
    "built_at": e["B_BUILT_AT"],
    "guest": {"template_id": "zephyr-xenvm-starter-arm64", "template_version": "1.0.0",
              "vcpus": 1, "memory_mib": 16, "gic_version": "v2"},
    "broker": {"host": e["B_HOST"], "port": int(e["B_PORT"]), "username": e["B_USERNAME"],
               "password_embedded": e["B_EMBEDDED"] == "true"},
    "network": "none: xenvm has no PV network; broker path is vchan + dom0 relay (S15P31E110-239/-240)",
    "autostart": False, "hardware_boot_tested": False,
    "deploy": {"dest": e["B_DEST"], "xl_cfg": "xenova-manual.cfg",
               "firmware_replace": {"bundle": "firmware", "sha256": files["firmware/firmware.json"]["sha256"]}},
    "files": files,
}
(root / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
PY

# 6. tar.gz 와 출력 폴더
tar -C "$WORK" --owner=0 --group=0 -czf "$WORK/$NAME.tar.gz" "$NAME"
mkdir -p "$OUT"
chmod -R u+w "$OUT/$NAME" 2>/dev/null || true
rm -rf "${OUT:?}/$NAME" "${OUT:?}/$NAME.tar.gz"
cp -R "$WORK/$NAME" "$WORK/$NAME.tar.gz" "$OUT/"
echo "번들: $OUT/$NAME (+ .tar.gz)"
echo "  zephyr.bin $(wc -c < "$WORK/$NAME/zephyr.bin") 바이트, password_embedded=$([ -n "$PASSWORD" ] && echo true || echo false), 브로커 $USERNAME@$HOST:$PORT"
echo "  firmware-replace --sha256 $(sha256sum "$WORK/$NAME/firmware/firmware.json" | cut -d' ' -f1)"
