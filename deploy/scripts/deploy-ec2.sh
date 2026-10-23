#!/usr/bin/env bash
set -Eeuo pipefail

: "${DEPLOY_HOST:?DEPLOY_HOST is required}"
: "${DEPLOY_USER:?DEPLOY_USER is required}"
: "${DEPLOY_DIR:?DEPLOY_DIR is required}"
: "${DEPLOY_SSH_KEY:?DEPLOY_SSH_KEY is required}"
: "${DEPLOY_KNOWN_HOSTS:?DEPLOY_KNOWN_HOSTS is required}"
: "${DEPLOY_ENV_FILE:?DEPLOY_ENV_FILE is required}"
: "${BUILD_NUMBER:?BUILD_NUMBER is required}"

ssh_options=(
  -i "${DEPLOY_SSH_KEY}"
  -o BatchMode=yes
  -o IdentitiesOnly=yes
  -o StrictHostKeyChecking=yes
  -o "UserKnownHostsFile=${DEPLOY_KNOWN_HOSTS}"
)
remote="${DEPLOY_USER}@${DEPLOY_HOST}"

# Git 이력, 로컬 산출물, 비밀 파일은 보내지 않고 배포에 필요한 소스만 전달한다.
ssh "${ssh_options[@]}" "${remote}" "sudo install -d -o '${DEPLOY_USER}' -g '${DEPLOY_USER}' '${DEPLOY_DIR}'"
tar \
  --exclude='.git' \
  --exclude='.env' \
  --exclude='node_modules' \
  --exclude='target' \
  --exclude='dist' \
  -czf - . | ssh "${ssh_options[@]}" "${remote}" "tar -xzf - -C '${DEPLOY_DIR}'"

scp "${ssh_options[@]}" "${DEPLOY_ENV_FILE}" "${remote}:${DEPLOY_DIR}/.env.next"

ssh "${ssh_options[@]}" "${remote}" \
  "DEPLOY_DIR='${DEPLOY_DIR}' IMAGE_TAG='${BUILD_NUMBER}' bash -s" <<'REMOTE'
set -Eeuo pipefail
cd "${DEPLOY_DIR}"

chmod 600 .env.next
mv .env.next .env

docker compose --env-file .env config --quiet
IMAGE_TAG="${IMAGE_TAG}" docker compose --env-file .env build
IMAGE_TAG="${IMAGE_TAG}" docker compose --env-file .env up -d --remove-orphans

deadline=$((SECONDS + 180))
while (( SECONDS < deadline )); do
  unhealthy="$(docker compose --env-file .env ps --format json | grep -E '"Health":"(starting|unhealthy)"|"State":"(exited|dead|restarting)"' || true)"
  running="$(docker compose --env-file .env ps --services --filter status=running | wc -l)"
  if [[ -z "${unhealthy}" && "${running}" -eq 6 ]]; then
    docker compose --env-file .env ps
    exit 0
  fi
  sleep 5
done

echo 'Containers did not become healthy within 180 seconds.' >&2
docker compose --env-file .env ps >&2
docker compose --env-file .env logs --tail=200 backend nginx >&2
exit 1
REMOTE
