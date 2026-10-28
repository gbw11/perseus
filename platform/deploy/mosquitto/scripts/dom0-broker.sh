#!/bin/sh
# dom0 Mosquitto 설치와 계정 관리. dom0(Yocto)에는 bash 가 없을 수 있어 POSIX sh 로 쓴다. root 로 실행한다.
#
#   sh scripts/dom0-broker.sh install                 설정 배치, 서비스 계정 발급, 자동 기동 (다시 실행해도 기존 비밀번호 유지)
#   xenova-broker add-account <vm_id>                 VM 에이전트 계정 + agent.env 생성 (이미 있으면 그대로 둔다)
#   xenova-broker add-account <이름>                   그 밖의 계정 (accounts 에 없는 앱 등)
#   xenova-broker remove-account <이름|vm_id>
#   xenova-broker list
#   xenova-broker tailscale-listener                  서비스 시작 직전에 자동 실행. Tailscale 주소로 리스너를 만든다
#
# install 은 이 스크립트를 /usr/local/sbin/xenova-broker 로 복사하므로 그 뒤에는 저장소 사본이 없어도 된다.
# 비밀번호는 명령행·화면에 내지 않는다. 평문은 root 전용 파일(passwd.credentials, agent-env/)에만 남는다.
#
# 환경변수 (시험용): XENOVA_MQTT_ETC(기본 /etc/mosquitto), XENOVA_BROKER_BIN, XENOVA_SYSTEMD_DIR, XENOVA_TS_WAIT(초, 기본 20)
set -eu

ETC=${XENOVA_MQTT_ETC:-/etc/mosquitto}
PASSWD=$ETC/passwd
CREDS=$ETC/passwd.credentials
AGENT_ENV_DIR=$ETC/agent-env
TS_CONF=$ETC/conf.d/tailscale.conf
SELF=${XENOVA_BROKER_BIN:-/usr/local/sbin/xenova-broker}
DROPIN_DIR=${XENOVA_SYSTEMD_DIR:-/etc/systemd/system}/mosquitto.service.d
TS_WAIT=${XENOVA_TS_WAIT:-20}
UUID_RE='^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'

umask 077

die() { echo "$*" >&2; exit 2; }
has() { command -v "$1" >/dev/null 2>&1; }
is_uuid() { printf '%s\n' "$1" | grep -Eq "$UUID_RE"; }
known() { [ -f "$PASSWD" ] && grep -q "^$1:" "$PASSWD"; }

# 계정 이름: 토픽 안의 id 와 글자까지 같아야 ACL 의 %u 가 맞는다. vm_id 는 소문자 표준형으로 (에이전트가 그렇게 보낸다)
account_name() {
  name=$1
  if printf '%s\n' "$name" | grep -Eiq "$UUID_RE"; then name=$(printf '%s' "$name" | tr '[:upper:]' '[:lower:]'); fi
  case "$name" in ''|*[!A-Za-z0-9._-]*) die "계정 이름은 영문·숫자·. _ - 만 쓸 수 있습니다: '$1'";; esac
  printf '%s' "$name"
}

# 36자 16진수. busybox 에도 있는 od 만 쓴다
gen_pw() { od -An -N18 -tx1 /dev/urandom | tr -d ' \n'; }

# 파일에서 그 계정 줄을 지운다 (없으면 그대로). sh 에는 local 이 없어 함수마다 변수 이름을 따로 쓴다
drop_line() {
  [ -f "$2" ] || return 0
  dl_tmp=$(mktemp "$2.XXXXXX")
  grep -v "^$1:" "$2" > "$dl_tmp" || true
  mv -f "$dl_tmp" "$2"
}

fix_perms() {
  for f in "$PASSWD" "$CREDS"; do [ -f "$f" ] && chmod 600 "$f"; done
  # 브로커는 root 로 시작해 mosquitto 사용자로 내려간 뒤 SIGHUP 때 passwd 를 다시 읽는다
  if [ -f "$PASSWD" ] && id mosquitto >/dev/null 2>&1; then chown mosquitto "$PASSWD"; fi
  return 0
}

# 새 비밀번호를 발급해 passwd(해시)와 credentials(평문)에 쓴다
issue() {
  has mosquitto_passwd || die "mosquitto_passwd 가 없습니다 (mosquitto 패키지)."
  mkdir -p "$ETC"
  pw=$(gen_pw)
  is_tmp=$(mktemp "$ETC/.pw.XXXXXX")
  printf '%s:%s\n' "$1" "$pw" > "$is_tmp"
  mosquitto_passwd -U "$is_tmp"
  drop_line "$1" "$PASSWD"; cat "$is_tmp" >> "$PASSWD"; rm -f "$is_tmp"
  drop_line "$1" "$CREDS"; printf '%s:%s\n' "$1" "$pw" >> "$CREDS"
  fix_perms
  if is_uuid "$1"; then
    mkdir -p "$AGENT_ENV_DIR"
    printf 'XENOVA_AGENT_BROKER_USERNAME=%s\nXENOVA_AGENT_BROKER_PASSWORD=%s\n' "$1" "$pw" > "$AGENT_ENV_DIR/$1.env"
    chmod 600 "$AGENT_ENV_DIR/$1.env"
  fi
}

# passwd·acl 은 SIGHUP 으로 다시 읽는다 (접속 중인 클라이언트는 끊지 않는다)
reload() {
  if has systemctl && systemctl is-active --quiet mosquitto 2>/dev/null; then
    systemctl kill -s HUP mosquitto && echo "브로커에 계정 변경을 반영했습니다 (SIGHUP)."
  else
    echo "브로커가 실행 중이 아닙니다. 다음 기동 때 반영됩니다."
  fi
}

cmd_tailscale_listener() {
  mkdir -p "$(dirname "$TS_CONF")"
  ip=""
  if has tailscale; then
    i=0
    while [ "$i" -lt "$TS_WAIT" ]; do
      ip=$(tailscale ip -4 2>/dev/null | head -n 1) || ip=""
      [ -n "$ip" ] && break
      sleep 1; i=$((i + 1))
    done
  fi
  if [ -n "$ip" ]; then
    printf '# xenova-broker tailscale-listener 가 서비스 시작 때마다 다시 쓴다. 손으로 고치지 않는다.\n# 외부 서버(Spring)가 Tailscale 로 붙는 주소.\nlistener 1883 %s\n' "$ip" > "$TS_CONF"
    echo "Tailscale 리스너: $ip:1883"
  else
    # 비어 있어도 브로커는 뜬다. VM 쪽(192.168.77.1)을 Tailscale 상태에 묶지 않는다
    printf '# Tailscale 주소를 얻지 못해 외부 리스너를 열지 않았다. tailscale up 뒤 systemctl restart mosquitto.\n' > "$TS_CONF"
    echo "Tailscale 주소 없음 — 외부 리스너 없이 시작합니다." >&2
  fi
  chmod 644 "$TS_CONF"
}

cmd_install() {
  here=$(cd "$(dirname "$0")/.." && pwd -P)
  [ -f "$here/etc/mosquitto.conf" ] && [ -f "$here/listeners/dom0.conf" ] \
    || die "install 은 저장소의 platform/deploy/mosquitto/scripts/ 에서 실행합니다."
  mkdir -p "$ETC/conf.d"
  # umask 077 로 새로 만든 디렉터리면 mosquitto 사용자가 못 들어간다
  chmod 755 "$ETC" "$ETC/conf.d"
  cp "$here/etc/mosquitto.conf" "$ETC/mosquitto.conf"
  cp "$here/etc/acl" "$ETC/acl"
  cp "$here/listeners/dom0.conf" "$ETC/conf.d/listeners.conf"
  chmod 644 "$ETC/mosquitto.conf" "$ETC/acl" "$ETC/conf.d/listeners.conf"
  [ -f "$TS_CONF" ] || { : > "$TS_CONF"; chmod 644 "$TS_CONF"; }
  mkdir -p "$(dirname "$SELF")"
  cp "$0" "$SELF"; chmod 755 "$SELF"

  # 서비스·앱 계정. accounts 의 vm_id 줄은 로컬 예시라 건너뛴다 (실제 VM 은 add-account 로)
  added=""
  while IFS= read -r line || [ -n "$line" ]; do
    name=${line%%#*}; name=$(printf '%s' "$name" | tr -d ' \t\r')
    [ -z "$name" ] && continue
    is_uuid "$name" && continue
    known "$name" || { issue "$name"; added="$added $name"; }
  done < "$here/etc/accounts"
  echo "새로 발급한 계정:${added:- 없음 (기존 비밀번호 유지)}"

  if has systemctl; then
    mkdir -p "$DROPIN_DIR"
    cat > "$DROPIN_DIR/xenova.conf" <<EOF
# platform/deploy/mosquitto/scripts/dom0-broker.sh install 이 만든다.
[Unit]
# 192.168.77.1 은 xenova-bridge 가 붙인다. 주소가 없으면 그 리스너를 못 연다
After=xenova-bridge.service tailscaled.service
Wants=xenova-bridge.service
StartLimitIntervalSec=0

[Service]
ExecStartPre=$SELF tailscale-listener
Restart=on-failure
RestartSec=3
EOF
    chmod 644 "$DROPIN_DIR/xenova.conf"
    systemctl daemon-reload
    systemctl enable mosquitto >/dev/null 2>&1 || true
    systemctl restart mosquitto
    sleep 1
    if systemctl is-active --quiet mosquitto; then
      echo "mosquitto: active"
    else
      systemctl status mosquitto --no-pager >&2 || true
      die "mosquitto 가 뜨지 않았습니다."
    fi
  else
    echo "systemctl 이 없어 서비스 등록은 건너뜁니다." >&2
    cmd_tailscale_listener
  fi

  cat <<EOF

리스너: 192.168.77.1:1883 (VM), 127.0.0.1:1883 (dom0), $(sed -n 's/^listener 1883 //p' "$TS_CONF" | sed 's/$/:1883 (Tailscale)/')
평문 비밀번호: $CREDS (root 전용)
  Spring:          MQTT_USERNAME=backend, MQTT_PASSWORD=<backend 줄>
  Recovery Worker: /etc/xenova/recovery-worker.env 에 dom0-server 계정
VM 마다:          $SELF add-account <vm_id>
검사:             README "검사" 절 (bash 필요)
EOF
}

cmd_add() {
  name=$(account_name "${1:-}")
  if known "$name"; then
    echo "$name 계정은 이미 있습니다. 비밀번호를 바꾸지 않습니다 (바꾸려면 remove-account 뒤 add-account)."
  else
    issue "$name"
    echo "$name 계정을 만들었습니다."
    reload
  fi
  if is_uuid "$name"; then
    if [ -f "$AGENT_ENV_DIR/$name.env" ]; then
      cat <<EOF

에이전트 설정: $AGENT_ENV_DIR/$name.env
  1) 그 VM 의 /etc/xenova/instance-id 가 $name 인지 확인
  2) 위 파일의 두 줄을 그 VM 의 /etc/xenova/agent.env 에 넣는다 (root 0600)
  3) 그 VM 에서 systemctl restart xenova-agent
EOF
    else
      echo "agent.env 사본이 없습니다 (평문을 지운 계정). 새로 받으려면 remove-account 뒤 add-account." >&2
    fi
  fi
}

cmd_remove() {
  name=$(account_name "${1:-}")
  known "$name" || die "$name 계정이 없습니다."
  drop_line "$name" "$PASSWD"; drop_line "$name" "$CREDS"; rm -f "$AGENT_ENV_DIR/$name.env"
  fix_perms
  echo "$name 계정을 지웠습니다. 이미 접속 중인 연결은 다시 접속할 때 거부됩니다."
  reload
}

cmd_list() {
  [ -f "$PASSWD" ] || die "$PASSWD 가 없습니다. install 을 먼저 실행하세요."
  cut -d: -f1 "$PASSWD" | while IFS= read -r n; do
    note=""; is_uuid "$n" && note="  (VM)"
    grep -q "^$n:" "$CREDS" 2>/dev/null || note="$note  [평문 없음]"
    echo "$n$note"
  done
}

[ -n "${XENOVA_MQTT_ETC:-}" ] || [ "$(id -u)" = 0 ] || die "root 로 실행하세요."
case "${1:-}" in
  install) cmd_install ;;
  add-account) cmd_add "${2:-}" ;;
  remove-account) cmd_remove "${2:-}" ;;
  list) cmd_list ;;
  tailscale-listener) cmd_tailscale_listener ;;
  *) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
