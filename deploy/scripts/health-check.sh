#!/usr/bin/env bash
set -Eeuo pipefail

base_url="${1:?Usage: health-check.sh BASE_URL}"
base_url="${base_url%/}"

check() {
  local name="$1"
  local url="$2"
  echo "Checking ${name}: ${url}"
  curl \
    --fail \
    --silent \
    --show-error \
    --location \
    --retry 12 \
    --retry-all-errors \
    --retry-delay 5 \
    --connect-timeout 5 \
    --max-time 15 \
    "${url}" >/dev/null
}

check 'Nginx' "${base_url}/healthz"
check 'admin-console' "${base_url}/"
check 'vm-screens' "${base_url}/screens/healthz"
check 'backend' "${base_url}/actuator/health"

echo 'All production health checks passed.'
