# 349 access support is installed but deliberately not auto-enabled.
# Keep the existing recipe and 351 native HDMI deployment support intact.
FILESEXTRAPATHS:prepend := "${THISDIR}/../../../../services/display/router/deploy/mobile-access:"
SRC_URI += "file://xenova-display-mobile.service file://mobile-access.config.example.json file://mobile-access.README.md"

do_install:append() {
    install -d "${D}${systemd_system_unitdir}" "${D}${datadir}/xenova/display/mobile-access"
    install -m 0644 "${S}/xenova-display-mobile.service" "${D}${systemd_system_unitdir}/"
    install -m 0644 "${S}/mobile-access.config.example.json" "${S}/mobile-access.README.md" \
        "${D}${datadir}/xenova/display/mobile-access/"
    # No active configuration, network assignment, credentials or boot symlink.
}

FILES:${PN} += "${systemd_system_unitdir}/xenova-display-mobile.service"
