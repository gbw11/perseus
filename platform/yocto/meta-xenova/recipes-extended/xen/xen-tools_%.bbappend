# xen-tools-net-conf의 광범위한 NIC 브릿지 편입과 자동 브릿지 생성을 대체한다.
# meta-xenova에서 xen-tools를 사용하는 이미지 전체에 적용된다.
FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

# 브릿지는 xenova-bridge.service가 생성하고 고정 IP를 설정한다.
# virt_networking.bbclass에는 networkd 비관리 규칙만 설치하도록 전달한다.
VIRT_NETWORKING_FILES = "${UNPACKDIR}/10-ether.network ${UNPACKDIR}/10-xenbr0.network"
SRC_URI:remove = "file://10-xenbr0.netdev"
SRC_URI += "file://0002-console-filter-xenova-evidence.patch file://xenova_console_evidence.h file://xenova_console_evidence_file.h"
python do_patch:append() {
    import shutil
    shutil.copyfile(d.getVar('UNPACKDIR') + '/xenova_console_evidence.h',
                    d.getVar('S') + '/tools/console/daemon/xenova_console_evidence.h')
    shutil.copyfile(d.getVar('UNPACKDIR') + '/xenova_console_evidence_file.h',
                    d.getVar('S') + '/tools/console/daemon/xenova_console_evidence_file.h')
}
