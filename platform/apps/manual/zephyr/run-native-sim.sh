#!/bin/sh
# native_sim 수동주행 앱을 Mosquitto 에 붙여 N초 돌리고 받은 메시지를 센다.
# Zephyr 4.4.0 작업 공간(ZEPHYR_BASE)과 host gcc, mosquitto-clients 가 있는 Linux 에서 실행한다.
#   ./run-native-sim.sh [초=4] [빌드 디렉터리=/tmp/xenova-manual-native-sim]
# 브로커는 환경변수로 바꾼다 (없으면 127.0.0.1:1883 익명). 비밀번호는 빌드 설정으로 들어가므로 저장소에 쓰지 않는다.
#   XENOVA_MQTT_HOST      IPv4 주소 (기본 127.0.0.1)
#   XENOVA_MQTT_PORT      (기본 1883)
#   XENOVA_MQTT_USERNAME  (기본 manual = app_id, ACL 계정)
#   XENOVA_MQTT_PASSWORD  (없으면 익명 접속)
set -eu
SECONDS_TO_RUN="${1:-4}"
BUILD="${2:-/tmp/xenova-manual-native-sim}"
HERE="$(cd "$(dirname "$0")" && pwd)"
HOST="${XENOVA_MQTT_HOST:-127.0.0.1}"
PORT="${XENOVA_MQTT_PORT:-1883}"
USERNAME="${XENOVA_MQTT_USERNAME:-manual}"
PASSWORD="${XENOVA_MQTT_PASSWORD:-}"
export ZEPHYR_TOOLCHAIN_VARIANT=host
cmake -S "$HERE" -B "$BUILD" -GNinja -DBOARD=native_sim/native/64 \
  -DCONFIG_XENOVA_MANUAL_BROKER_HOST="\"$HOST\"" -DCONFIG_XENOVA_MANUAL_BROKER_PORT="$PORT" \
  -DCONFIG_XENOVA_MANUAL_BROKER_USERNAME="\"$USERNAME\"" -DCONFIG_XENOVA_MANUAL_BROKER_PASSWORD="\"$PASSWORD\"" >/dev/null
ninja -C "$BUILD" >/dev/null
# 구독도 같은 계정으로 한다 (ACL 은 모든 계정에 xenova/v1/# 읽기를 연다)
if [ -n "$PASSWORD" ]; then
  set -- -u "$USERNAME" -P "$PASSWORD"
else
  set --
fi
timeout "$((SECONDS_TO_RUN + 1))" mosquitto_sub -h "$HOST" -p "$PORT" "$@" -i "native-sim-observer-$$" \
  -t 'xenova/v1/apps/manual/events' > "$BUILD/received.txt" &
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
