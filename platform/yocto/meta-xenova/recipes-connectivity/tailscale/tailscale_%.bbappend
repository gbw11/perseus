# Keep storage policy separate from the source/static-binary recipe selection.
FILESEXTRAPATHS:prepend := "${THISDIR}/files:"
SRC_URI += "file://20-xenova-persistence.conf file://xenova-tailscale-state"

SYSTEMD_AUTO_ENABLE:${PN}d = "enable"
RDEPENDS:${PN}d:append = " ${PN} python3-core python3-json python3-crypt python3-io python3-netclient python3-unixadmin"
FILES:${PN}d:append = " ${sbindir}/xenova-tailscale-state"

do_install:append() {
    install -d "${D}${systemd_system_unitdir}/tailscaled.service.d"
    install -m 0644 "${UNPACKDIR}/20-xenova-persistence.conf" \
        "${D}${systemd_system_unitdir}/tailscaled.service.d/20-xenova-persistence.conf"
    install -m 0755 "${UNPACKDIR}/xenova-tailscale-state" "${D}${sbindir}/xenova-tailscale-state"
}
