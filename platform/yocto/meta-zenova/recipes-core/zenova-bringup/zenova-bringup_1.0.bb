SUMMARY = "Non-production Raspberry Pi Xen bring-up helpers"
DESCRIPTION = "Isolated Xen bridge, test-only DHCP, one guest configuration and checklist"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

SRC_URI = " \
    file://zenova-bridge.service \
    file://zenova-dhcp.service \
    file://zenova-bridge-up \
    file://zenova-bringup-check \
    file://zenova-domu.cfg \
    file://dnsmasq-bringup.conf \
"
S = "${UNPACKDIR}"

inherit systemd

SYSTEMD_SERVICE:${PN} = "zenova-bridge.service zenova-dhcp.service"
# 내부 브릿지·DHCP는 외부 LAN이나 VM 저장소 없이도 준비한다.
SYSTEMD_AUTO_ENABLE:${PN} = "enable"

RDEPENDS:${PN} = "iproute2 dnsmasq kmod zenova-platform"
# If CONFIG_BRIDGE=m, prefer to install the packaged bridge kernel module.
# If CONFIG_BRIDGE=y, no module package exists and this weak recommendation is ignored.
RRECOMMENDS:${PN} += "kernel-module-bridge"

do_install() {
    install -d "${D}${sbindir}" "${D}${sysconfdir}/zenova" \
               "${D}${sysconfdir}/xen" "${D}${systemd_system_unitdir}"
    install -m 0755 "${S}/zenova-bridge-up" "${D}${sbindir}/zenova-bridge-up"
    install -m 0755 "${S}/zenova-bringup-check" "${D}${sbindir}/zenova-bringup-check"
    install -m 0644 "${S}/zenova-domu.cfg" "${D}${sysconfdir}/xen/zenova-domu.cfg"
    install -m 0644 "${S}/dnsmasq-bringup.conf" "${D}${sysconfdir}/zenova/dnsmasq-bringup.conf"
    sed -e 's|@SBINDIR@|${sbindir}|g' "${S}/zenova-bridge.service" \
        > "${D}${systemd_system_unitdir}/zenova-bridge.service"
    sed -e 's|@BINDIR@|${bindir}|g' "${S}/zenova-dhcp.service" \
        > "${D}${systemd_system_unitdir}/zenova-dhcp.service"
}

FILES:${PN} += "${sysconfdir}/zenova ${sysconfdir}/xen/zenova-domu.cfg"
