#!/bin/sh
# native_sim 수동주행 앱을 로컬 Mosquitto(127.0.0.1:1883)에 붙여 N초 돌리고 받은 메시지를 센다.
# Zephyr 4.4.0 작업 공간(ZEPHYR_BASE)과 host gcc, mosquitto-clients 가 있는 Linux 에서 실행한다.
#   ./run-native-sim.sh [초=4] [빌드 디렉터리=/tmp/xenova-manual-native-sim]
set -eu
SECONDS_TO_RUN="${1:-4}"
BUILD="${2:-/tmp/xenova-manual-native-sim}"
HERE="$(cd "$(dirname "$0")" && pwd)"
export ZEPHYR_TOOLCHAIN_VARIANT=host
cmake -S "$HERE" -B "$BUILD" -GNinja -DBOARD=native_sim/native/64 >/dev/null
ninja -C "$BUILD" >/dev/null
timeout "$((SECONDS_TO_RUN + 1))" mosquitto_sub -h 127.0.0.1 -t 'xenova/v1/apps/manual/events' > "$BUILD/received.txt" &
sleep 0.3
"$BUILD/zephyr/zephyr.exe" -stop_at="$SECONDS_TO_RUN" | tee "$BUILD/app.log"
wait
python3 - "$BUILD/received.txt" "$SECONDS_TO_RUN" <<'PY'
import json, sys
rows = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
cmd = [r for r in rows if r["type"] == "drive.command"]
hb = [r for r in rows if r["type"] == "app.heartbeat"]
print(f"{len(cmd)} drive.command, {len(hb)} app.heartbeat in {sys.argv[2]} s")
if cmd: print("last drive.command payload:", cmd[-1]["payload"])
if hb: print("last app.heartbeat payload:", hb[-1]["payload"])
PY
