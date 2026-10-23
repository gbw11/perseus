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
next_dir="${DEPLOY_DIR}.next"
previous_dir="${DEPLOY_DIR}.previous"

# 기존 디렉터리에 덮어쓰면 Git에서 삭제되거나 이름이 바뀐 파일이 서버에 남는다.
# 매 배포마다 빈 준비 디렉터리를 만들고 소스를 교체해 서버 파일을 정확히 동기화한다.
ssh "${ssh_options[@]}" "${remote}" \
  "sudo rm -rf '${next_dir}' && sudo install -d -o '${DEPLOY_USER}' -g '${DEPLOY_USER}' '${next_dir}'"
tar \
  --exclude='.git' \
  --exclude='.env' \
  --exclude='node_modules' \
  --exclude='target' \
  --exclude='dist' \
  -czf - . | ssh "${ssh_options[@]}" "${remote}" "tar -xzf - -C '${next_dir}'"

scp "${ssh_options[@]}" "${DEPLOY_ENV_FILE}" "${remote}:${next_dir}/.env"

ssh "${ssh_options[@]}" "${remote}" \
  "DEPLOY_DIR='${DEPLOY_DIR}' NEXT_DIR='${next_dir}' PREVIOUS_DIR='${previous_dir}' IMAGE_TAG='${BUILD_NUMBER}' bash -s" <<'REMOTE'
set -Eeuo pipefail

# Jenkins 전용 환경 파일은 저장소에 포함되지 않으므로 기존 서버 파일을 보존한다.
if [[ -f "${DEPLOY_DIR}/deploy/jenkins/.env" ]]; then
  install -D -m 600 \
    "${DEPLOY_DIR}/deploy/jenkins/.env" \
    "${NEXT_DIR}/deploy/jenkins/.env"
fi

chmod 600 "${NEXT_DIR}/.env"
cd "${NEXT_DIR}"
docker compose --env-file .env config --quiet

sudo rm -rf "${PREVIOUS_DIR}"
if [[ -d "${DEPLOY_DIR}" ]]; then
  sudo mv "${DEPLOY_DIR}" "${PREVIOUS_DIR}"
fi
sudo mv "${NEXT_DIR}" "${DEPLOY_DIR}"
cd "${DEPLOY_DIR}"

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
