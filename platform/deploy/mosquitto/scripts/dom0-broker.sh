#!/bin/sh
# 기존 dom0-broker.sh의 install/add-account/remove-account/list 진입점.
# 계정 작성은 Yocto 패키지의 공통 Python 구현만 수행한다.
# 원 구현: backend 759c916, S15P31E110-258. Tailscale 연결은 broker 시작과 분리한다.
set -eu
TOOL=/usr/sbin/xenova-broker
[ -x "$TOOL" ] || { echo 'xenova-mqtt-broker 패키지를 먼저 설치하세요. 기존 계정은 변경하지 않았습니다.' >&2; exit 2; }
case "${1:-}" in
  install)
    shift
    "$TOOL" initialize "$@"
    echo '계정 저장에는 --execute가 필요합니다. 검증 뒤 systemctl enable --now mosquitto를 실행하세요.' >&2
    ;;
  initialize|add-account|remove-account|list|check|export-account|import-legacy) exec "$TOOL" "$@" ;;
  tailscale-listener)
    echo 'broker는 Tailnet 주소를 기다리지 않습니다. 문서의 tailscale serve 절차를 사용하세요.' >&2
    exit 2 ;;
  *) echo '사용법: dom0-broker.sh install|add-account|remove-account|list|check|export-account|import-legacy' >&2; exit 2 ;;
esac
