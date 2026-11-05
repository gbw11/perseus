SUMMARY = "Non-production Raspberry Pi Xen bring-up helpers"
DESCRIPTION = "Isolated Xen bridge, test-only DHCP, one guest configuration and checklist"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

SRC_URI = " \
    file://xenova-bridge.service \
    file://xenova-dhcp.service \
    file://xenova-guest-ntp.service \
    file://xenova-guest-ntp \
    file://xenova-bridge-up \
    file://xenova-bringup-check \
    file://xenova-domu.cfg \
    file://dnsmasq-bringup.conf \
"
S = "${UNPACKDIR}"

inherit systemd

SYSTEMD_SERVICE:${PN} = "xenova-bridge.service xenova-dhcp.service xenova-guest-ntp.service"
# 내부 브릿지·DHCP는 외부 LAN이나 VM 저장소 없이도 준비한다.
SYSTEMD_AUTO_ENABLE:${PN} = "enable"

RDEPENDS:${PN} = "iproute2 dnsmasq kmod xenova-platform python3-core python3-netclient"
# If CONFIG_BRIDGE=m, prefer to install the packaged bridge kernel module.
# If CONFIG_BRIDGE=y, no module package exists and this weak recommendation is ignored.
RRECOMMENDS:${PN} += "kernel-module-bridge"

do_install() {
    install -d "${D}${sbindir}" "${D}${sysconfdir}/xenova" \
               "${D}${sysconfdir}/xen" "${D}${systemd_system_unitdir}"
    install -m 0755 "${S}/xenova-bridge-up" "${D}${sbindir}/xenova-bridge-up"
    install -m 0755 "${S}/xenova-bringup-check" "${D}${sbindir}/xenova-bringup-check"
    install -m 0755 "${S}/xenova-guest-ntp" "${D}${sbindir}/xenova-guest-ntp"
    install -m 0644 "${S}/xenova-domu.cfg" "${D}${sysconfdir}/xen/xenova-domu.cfg"
    install -m 0644 "${S}/dnsmasq-bringup.conf" "${D}${sysconfdir}/xenova/dnsmasq-bringup.conf"
    sed -e 's|@SBINDIR@|${sbindir}|g' "${S}/xenova-bridge.service" \
        > "${D}${systemd_system_unitdir}/xenova-bridge.service"
    sed -e 's|@BINDIR@|${bindir}|g' "${S}/xenova-dhcp.service" \
        > "${D}${systemd_system_unitdir}/xenova-dhcp.service"
    sed -e 's|@SBINDIR@|${sbindir}|g' "${S}/xenova-guest-ntp.service" \
        > "${D}${systemd_system_unitdir}/xenova-guest-ntp.service"
}

FILES:${PN} += "${sysconfdir}/xenova ${sysconfdir}/xen/xenova-domu.cfg"
