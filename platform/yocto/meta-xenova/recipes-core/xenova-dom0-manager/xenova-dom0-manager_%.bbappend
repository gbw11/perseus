# Install one local journal collector alongside the existing Manager package.
# Keep the base recipe, service token setup and recovery service unchanged.
FILESEXTRAPATHS:prepend := "${THISDIR}/files/runtime-logs:"
SRC_URI += "file://xenova-runtime-log-collector.service file://runtime-log-db.env file://runtime-logs-manager.conf file://runtime-logs-image.README.md"

# The base recipe already supplies the collector's Python stdlib/sqlite3.
# journalctl belongs to systemd in the locked OE 259.5 recipe (there is no
# systemd-journalctl split package). Do not install a second logging runtime.
RDEPENDS:${PN}:append = " systemd"
SYSTEMD_SERVICE:${PN}:append = " xenova-runtime-log-collector.service"

do_install:append() {
    install -d "${D}${systemd_system_unitdir}/xenova-dom0-manager.service.d" \
        "${D}${sysconfdir}/xenova/runtime-logs" "${D}${datadir}/xenova/runtime-logs"
    chmod 0700 "${D}${sysconfdir}/xenova/runtime-logs"
    install -m 0644 "${S}/xenova-runtime-log-collector.service" \
        "${D}${systemd_system_unitdir}/xenova-runtime-log-collector.service"
    install -m 0644 "${S}/runtime-logs-manager.conf" \
        "${D}${systemd_system_unitdir}/xenova-dom0-manager.service.d/30-runtime-logs.conf"
    install -m 0600 "${S}/runtime-log-db.env" \
        "${D}${sysconfdir}/xenova/runtime-logs/collector.env"
    install -m 0644 "${S}/runtime-logs-image.README.md" "${D}${datadir}/xenova/runtime-logs/"
}

FILES:${PN}:append = " ${systemd_system_unitdir}/xenova-dom0-manager.service.d/30-runtime-logs.conf ${sysconfdir}/xenova/runtime-logs"
CONFFILES:${PN}:append = " ${sysconfdir}/xenova/runtime-logs/collector.env"
# The base recipe's SYSTEMD_AUTO_ENABLE=enable applies to the appended unit.
# Ingest/shipper listeners, producer credentials and network rules are not enabled.
