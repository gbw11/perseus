#!/usr/bin/env bash
# 계정 목록(etc/accounts)으로 Mosquitto passwd 파일을 만든다.
#
#   로컬:  XENOVA_MQTT_DEV_PASSWORD=... scripts/make-passwd.sh            → local/passwd (모든 계정 같은 개발용 비밀번호)
#   서버:  scripts/make-passwd.sh --random etc/accounts /etc/mosquitto/passwd
#          → 계정마다 무작위 비밀번호, 평문은 <passwd>.credentials (0600) 에 한 번만 남긴다. 각 컴포넌트에 나눠 준 뒤 지운다.
#
# mosquitto_passwd 가 있으면 그것을, 없으면 eclipse-mosquitto 이미지를 Docker 로 돌려 해시한다.
set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
IMAGE="${XENOVA_MQTT_IMAGE:-eclipse-mosquitto:2.0.21}"

random=0
if [[ "${1:-}" == --random ]]; then random=1; shift; fi
accounts="${1:-$HERE/etc/accounts}"
out="${2:-$HERE/local/passwd}"

[[ -f "$accounts" ]] || { echo "계정 목록이 없습니다: $accounts" >&2; exit 2; }
if (( ! random )); then
  if [[ -z "${XENOVA_MQTT_DEV_PASSWORD:-}" && -f "$HERE/local/.env" ]]; then
    XENOVA_MQTT_DEV_PASSWORD="$(sed -n 's/^XENOVA_MQTT_DEV_PASSWORD=//p' "$HERE/local/.env" | tail -n 1)"
  fi
  [[ -n "${XENOVA_MQTT_DEV_PASSWORD:-}" ]] || {
    echo "XENOVA_MQTT_DEV_PASSWORD 가 없습니다. local/.env.example 을 local/.env 로 복사해 값을 넣으세요." >&2; exit 2; }
fi

mkdir -p "$(dirname -- "$out")"
umask 077
tmp="$out.tmp"
: > "$tmp"
creds=""
if (( random )); then creds="$out.credentials"; : > "$creds"; fi

while IFS= read -r line || [[ -n "$line" ]]; do
  name="${line%%#*}"; name="${name//[[:space:]]/}"
  [[ -z "$name" ]] && continue
  [[ "$name" == *:* ]] && { echo "계정 이름에 ':' 를 쓸 수 없습니다: $name" >&2; exit 2; }
  if (( random )); then
    # 넉넉히 읽은 뒤 기호를 지우고 자른다 (지운 만큼 짧아지지 않게)
    pw="$(head -c 48 /dev/urandom | base64 | tr -d '/+=\n' | head -c 24)"
    printf '%s:%s\n' "$name" "$pw" >> "$creds"
  else
    pw="$XENOVA_MQTT_DEV_PASSWORD"
  fi
  printf '%s:%s\n' "$name" "$pw" >> "$tmp"
done < "$accounts"

# 평문 user:password 파일을 그 자리에서 해시로 바꾼다 (mosquitto_passwd -U)
if command -v mosquitto_passwd >/dev/null 2>&1; then
  mosquitto_passwd -U "$tmp"
else
  dir="$(cd -- "$(dirname -- "$tmp")" && pwd -P)"
  if command -v cygpath >/dev/null 2>&1; then dir="$(cygpath -m "$dir")"; fi
  MSYS_NO_PATHCONV=1 docker run --rm -v "$dir:/work" "$IMAGE" mosquitto_passwd -U "/work/$(basename -- "$tmp")"
fi
mv -f "$tmp" "$out"
chmod 600 "$out" 2>/dev/null || true

echo "passwd 생성: $out ($(grep -c . "$out") 계정)"
[[ -n "$creds" ]] && echo "평문 비밀번호: $creds — 나눠 준 뒤 지우세요."
exit 0
