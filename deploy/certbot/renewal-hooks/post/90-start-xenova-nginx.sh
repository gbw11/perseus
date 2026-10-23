#!/usr/bin/env bash
set -Eeuo pipefail

cd /opt/xenova
/usr/bin/docker compose --env-file .env -f compose.yaml up -d nginx
