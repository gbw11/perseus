SUMMARY = "Persistent Perseus Xen VM lifecycle and boot readiness"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"
SRC_URI = "file://perseus_vm.py file://perseus-vm file://defaults.json \
           file://srv-perseus.mount file://perseus-xen-ready.service \
           file://perseus-vm-shutdown.service file://perseus-vms.service \
           file://perseus-auth.conf"
S = "${UNPACKDIR}"
inherit systemd

# 기존 이미지의 Python 코어에 사용자 승인한 표준 JSON 패키지를 추가한다.
RDEPENDS:${PN} = "python3-core python3-json iproute2 xen-tools-xl openssh-sshd"
SYSTEMD_SERVICE:${PN} = "srv-perseus.mount perseus-vm-shutdown.service perseus-vms.service"
SYSTEMD_AUTO_ENABLE:${PN} = "enable"

do_install() {
    install -d "${D}${sbindir}" "${D}${libdir}/perseus" "${D}${sysconfdir}/perseus" \
        "${D}${sysconfdir}/ssh" "${D}${systemd_system_unitdir}" "${D}/srv/perseus"
    install -m 0755 "${S}/perseus-vm" "${D}${sbindir}/perseus-vm"
    install -m 0644 "${S}/perseus_vm.py" "${D}${libdir}/perseus/perseus_vm.py"
    install -m 0644 "${S}/defaults.json" "${D}${sysconfdir}/perseus/defaults.json"
    install -m 0644 "${S}/perseus-auth.conf" "${D}${sysconfdir}/ssh/perseus-auth.conf"
    for unit in srv-perseus.mount perseus-xen-ready.service perseus-vm-shutdown.service perseus-vms.service; do
        install -m 0644 "${S}/$unit" "${D}${systemd_system_unitdir}/$unit"
    done
}
# 소유권은 enable 목록과 별개다. static readiness unit도 반드시 패키징한다.
FILES:${PN} += "${libdir}/perseus /srv/perseus \
    ${systemd_system_unitdir}/srv-perseus.mount \
    ${systemd_system_unitdir}/perseus-xen-ready.service \
    ${systemd_system_unitdir}/perseus-vm-shutdown.service \
    ${systemd_system_unitdir}/perseus-vms.service"
CONFFILES:${PN} += "${sysconfdir}/perseus/defaults.json ${sysconfdir}/ssh/perseus-auth.conf"
