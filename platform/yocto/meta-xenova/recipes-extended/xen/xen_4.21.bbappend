FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

# 현재 배포 DTB의 BCM2711 PM 노드를 기존 watchdog 리셋 경로에 연결한다.
SRC_URI:append = " file://0001-arm-rpi4-find-bcm2711-power-management-watchdog.patch"
