# 351: optional phone-first deployment support; preserve the HDMI recipe/defaults.
FILESEXTRAPATHS:prepend := "${THISDIR}/../../../../services/display/router/deploy/mobile:"
SRC_URI += "file://mobile-first.conf file://mobile-feed.env.example file://mobile-first.README.md"

do_install:append() {
    install -d "${D}${systemd_system_unitdir}/xenova-display-hdmi.service.d" \
               "${D}${datadir}/xenova/display/mobile"
    install -m 0644 "${S}/mobile-first.conf" \
        "${D}${systemd_system_unitdir}/xenova-display-hdmi.service.d/50-mobile-first.conf"
    install -m 0644 "${S}/mobile-feed.env.example" "${S}/mobile-first.README.md" \
        "${D}${datadir}/xenova/display/mobile/"
    # Deliberately do not create /etc/xenova/display-mobile-first in the image.
    # An operator opts in after checking the actual deployment and issue 349.
}

FILES:${PN} += "${systemd_system_unitdir}/xenova-display-hdmi.service.d"
