#!/usr/bin/env bash
# 브로커가 MQTT 계약 §9 대로 막고 통과시키는지 확인한다. 로컬·dom0 어디서든 같은 검사를 쓴다.
#
#   로컬:  scripts/check.sh                       (local/.env 의 개발용 비밀번호, 컨테이너 안 클라이언트 사용)
#   서버:  XENOVA_MQTT_HOST=192.168.77.1 XENOVA_MQTT_CREDENTIALS=/etc/mosquitto/passwd.credentials scripts/check.sh
#
# 환경변수
#   XENOVA_MQTT_HOST / PORT / WS_PORT   브로커 주소 (기본 127.0.0.1 / 1883 / 9001)
#   XENOVA_MQTT_DEV_PASSWORD            모든 계정 공통 비밀번호 (로컬)
#   XENOVA_MQTT_CREDENTIALS             user:password 줄 파일 (make-passwd.sh --random 결과)
#   XENOVA_MQTT_CONTAINER               호스트에 mosquitto 클라이언트가 없을 때 쓸 컨테이너 (기본 xenova-mosquitto)
set -uo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
if [[ -z "${XENOVA_MQTT_DEV_PASSWORD:-}" && -z "${XENOVA_MQTT_CREDENTIALS:-}" && -f "$HERE/local/.env" ]]; then
  XENOVA_MQTT_DEV_PASSWORD="$(sed -n 's/^XENOVA_MQTT_DEV_PASSWORD=//p' "$HERE/local/.env" | tail -n 1)"
fi
HOST="${XENOVA_MQTT_HOST:-127.0.0.1}"
PORT="${XENOVA_MQTT_PORT:-1883}"
WS_PORT="${XENOVA_MQTT_WS_PORT:-9001}"
CONTAINER="${XENOVA_MQTT_CONTAINER:-xenova-mosquitto}"
VM_A=11111111-1111-4111-8111-111111111111
VM_B=22222222-2222-4222-8222-222222222222
P=xenova/v1

# 클라이언트: 호스트에 있으면 그대로, 없으면 브로커 컨테이너 안에서 (그때 주소는 컨테이너 안 127.0.0.1:1883)
if command -v mosquitto_pub >/dev/null 2>&1; then
  run() { "$@"; }; CH="$HOST"; CP="$PORT"
else
  docker inspect "$CONTAINER" >/dev/null 2>&1 || { echo "mosquitto 클라이언트도, 컨테이너 $CONTAINER 도 없습니다." >&2; exit 2; }
  run() { MSYS_NO_PATHCONV=1 docker exec -i "$CONTAINER" "$@"; }; CH=127.0.0.1; CP=1883
fi

pw() {
  if [[ -n "${XENOVA_MQTT_CREDENTIALS:-}" ]]; then sed -n "s/^$1://p" "$XENOVA_MQTT_CREDENTIALS" | head -n 1
  else printf '%s' "${XENOVA_MQTT_DEV_PASSWORD:?XENOVA_MQTT_DEV_PASSWORD 또는 XENOVA_MQTT_CREDENTIALS 가 필요합니다}"; fi
}

pass=0; fail=0
ok()  { echo "PASS  $1"; pass=$((pass + 1)); }
bad() { echo "FAIL  $1"; fail=$((fail + 1)); }

# delivered <구독 계정> <토픽> <발행 계정> [발행 토픽] → 메시지가 도착하면 0
delivered() {
  local sub="$1" topic="$2" pub="$3" ptopic="${4:-$2}" out
  out="$(mktemp)"
  run mosquitto_sub -h "$CH" -p "$CP" -u "$sub" -P "$(pw "$sub")" -t "$topic" -C 1 -W 4 > "$out" 2>/dev/null &
  local spid=$!
  sleep 1
  run mosquitto_pub -h "$CH" -p "$CP" -u "$pub" -P "$(pw "$pub")" -t "$ptopic" -q 1 -m '{"check":true}' >/dev/null 2>&1
  wait "$spid" 2>/dev/null
  [[ -s "$out" ]]; local r=$?; rm -f "$out"; return $r
}

# 1. 인증
if run mosquitto_sub -h "$CH" -p "$CP" -t "$P/#" -C 1 -W 2 >/dev/null 2>&1; then bad "익명 접속이 허용됨"; else ok "익명 접속 거부"; fi
if run mosquitto_sub -h "$CH" -p "$CP" -u observer -P wrong-password -t "$P/#" -C 1 -W 2 >/dev/null 2>&1; then bad "틀린 비밀번호가 허용됨"; else ok "틀린 비밀번호 거부"; fi

# 2. 앱: 자기 app_id 토픽만
delivered gateway "$P/apps/autonomous/events" autonomous && ok "앱 → 자기 토픽 (autonomous)" || bad "앱 → 자기 토픽 (autonomous)"
delivered gateway "$P/apps/manual/events" autonomous && bad "앱이 남의 토픽에 발행됨 (autonomous → manual)" || ok "앱 → 남의 토픽 차단 (autonomous → manual)"
delivered autonomous "$P/apps/perception/events" perception && ok "젯슨 → perception 토픽" || bad "젯슨 → perception 토픽"

# 3. 에이전트: 자기 vm_id 토픽만
delivered backend "$P/vms/$VM_A/heartbeat" "$VM_A" && ok "에이전트 → 자기 heartbeat" || bad "에이전트 → 자기 heartbeat"
delivered backend "$P/vms/$VM_B/heartbeat" "$VM_A" && bad "에이전트가 남의 vm_id 로 발행됨" || ok "에이전트 → 남의 vm_id 차단"

# 4. 백엔드·차량 쪽
delivered "$VM_A" "$P/vms/$VM_A/command" backend && ok "백엔드 → 에이전트 command" || bad "백엔드 → 에이전트 command"
delivered manual "$P/apps/manual/input" backend && ok "백엔드 → 수동 입력" || bad "백엔드 → 수동 입력"
# ESP32 는 MQTT 에 없다 (계약 v1.1). vehicle/status 도 게이트웨이가 낸다.
delivered backend "$P/vehicle/status" gateway && ok "게이트웨이 → vehicle/status" || bad "게이트웨이 → vehicle/status"
delivered backend "$P/vehicle/status" manual && bad "앱이 vehicle/status 에 발행됨" || ok "앱 → vehicle/status 차단"
delivered manual "$P/vehicle/control" autonomous && bad "앱이 vehicle/control 에 발행됨" || ok "앱 → vehicle/control 차단"
delivered manual "$P/vehicle/control" gateway && ok "게이트웨이 → vehicle/control" || bad "게이트웨이 → vehicle/control"
delivered gateway "$P/vehicle/control" observer && bad "observer 가 발행됨" || ok "observer 발행 차단 (읽기 전용)"

# 5. 계약 §1 크기 상한 (64 KB 초과는 전달되지 않아야 한다)
out="$(mktemp)"
run mosquitto_sub -h "$CH" -p "$CP" -u gateway -P "$(pw gateway)" -t "$P/apps/autonomous/events" -C 1 -W 4 > "$out" 2>/dev/null &
spid=$!; sleep 1
head -c 70000 /dev/zero | tr '\0' a | run mosquitto_pub -h "$CH" -p "$CP" -u autonomous -P "$(pw autonomous)" -t "$P/apps/autonomous/events" -s >/dev/null 2>&1
wait "$spid" 2>/dev/null
[[ -s "$out" ]] && bad "70 KB 메시지가 전달됨" || ok "70 KB 메시지 거부 (상한 64 KB)"
rm -f "$out"

# 6. WebSocket (브라우저 경로). 호스트에 Python paho-mqtt 가 있을 때만
py=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import paho.mqtt' >/dev/null 2>&1; then py="$c"; break; fi
done
if [[ -n "$py" ]]; then
  if XENOVA_WS="$HOST:$WS_PORT" XENOVA_PW_OBS="$(pw observer)" XENOVA_PW_PUB="$(pw message-board)" "$py" - <<'PY'
import os, queue, sys, time
import paho.mqtt.client as mqtt
host, port = os.environ["XENOVA_WS"].rsplit(":", 1)
got, topic = queue.Queue(), "xenova/v1/apps/message-board/events"
def client(user, pw):
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, transport="websockets")
    c.username_pw_set(user, pw)
    c.connect(host, int(port), 10)
    c.loop_start()
    return c
sub = client("observer", os.environ["XENOVA_PW_OBS"])
sub.on_message = lambda c, u, m: got.put(m.payload)
sub.subscribe(topic, 1); time.sleep(1)
pub = client("message-board", os.environ["XENOVA_PW_PUB"])
pub.publish(topic, b'{"check":"ws"}', qos=1).wait_for_publish(5)
try:
    got.get(timeout=5); sys.exit(0)
except queue.Empty:
    sys.exit(1)
PY
  then ok "WebSocket($WS_PORT) 발행·구독"; else bad "WebSocket($WS_PORT) 발행·구독"; fi
else
  echo "SKIP  WebSocket — Python paho-mqtt 없음 (pip install paho-mqtt)"
fi

echo "결과: 통과 $pass, 실패 $fail"
(( fail == 0 ))
