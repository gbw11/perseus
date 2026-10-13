SUMMARY = "Non-production Raspberry Pi Xen bring-up helpers"
DESCRIPTION = "Isolated Xen bridge, test-only DHCP, one guest configuration and checklist"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

SRC_URI = " \
    file://perseus-bridge.service \
    file://perseus-dhcp.service \
    file://perseus-bridge-up \
    file://perseus-bringup-check \
    file://perseus-domu.cfg \
    file://dnsmasq-bringup.conf \
"
S = "${UNPACKDIR}"

inherit systemd

SYSTEMD_SERVICE:${PN} = "perseus-bridge.service perseus-dhcp.service"
# On first boot verify Xen/dom0 BEFORE starting guest-network services manually.
SYSTEMD_AUTO_ENABLE:${PN} = "disable"

RDEPENDS:${PN} = "iproute2 dnsmasq kmod"
# If CONFIG_BRIDGE=m, prefer to install the packaged bridge kernel module.
# If CONFIG_BRIDGE=y, no module package exists and this weak recommendation is ignored.
RRECOMMENDS:${PN} += "kernel-module-bridge"

do_install() {
    install -d "${D}${sbindir}" "${D}${sysconfdir}/perseus" \
               "${D}${sysconfdir}/xen" "${D}${systemd_system_unitdir}"
    install -m 0755 "${S}/perseus-bridge-up" "${D}${sbindir}/perseus-bridge-up"
    install -m 0755 "${S}/perseus-bringup-check" "${D}${sbindir}/perseus-bringup-check"
    install -m 0644 "${S}/perseus-domu.cfg" "${D}${sysconfdir}/xen/perseus-domu.cfg"
    install -m 0644 "${S}/dnsmasq-bringup.conf" "${D}${sysconfdir}/perseus/dnsmasq-bringup.conf"
    sed -e 's|@SBINDIR@|${sbindir}|g' "${S}/perseus-bridge.service" \
        > "${D}${systemd_system_unitdir}/perseus-bridge.service"
    sed -e 's|@SBINDIR@|${sbindir}|g' "${S}/perseus-dhcp.service" \
        > "${D}${systemd_system_unitdir}/perseus-dhcp.service"
}

FILES:${PN} += "${sysconfdir}/perseus ${sysconfdir}/xen/perseus-domu.cfg"
