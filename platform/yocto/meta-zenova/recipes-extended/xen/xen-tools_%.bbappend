# xen-tools-net-conf의 광범위한 NIC 브릿지 편입과 자동 브릿지 생성을 대체한다.
# meta-zenova에서 xen-tools를 사용하는 이미지 전체에 적용된다.
FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

# 브릿지는 zenova-bridge.service가 생성하고 고정 IP를 설정한다.
# virt_networking.bbclass에는 networkd 비관리 규칙만 설치하도록 전달한다.
VIRT_NETWORKING_FILES = "${UNPACKDIR}/10-ether.network ${UNPACKDIR}/10-xenbr0.network"
SRC_URI:remove = "file://10-xenbr0.netdev"
