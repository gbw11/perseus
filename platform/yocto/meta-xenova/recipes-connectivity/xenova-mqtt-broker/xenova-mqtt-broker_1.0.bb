SUMMARY = "Xenova MQTT configuration and persistent account management"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

FILESEXTRAPATHS:prepend := "${THISDIR}/files:${THISDIR}/../../../../deploy/mosquitto/scripts:${THISDIR}/../../../../deploy/mosquitto/etc:"
SRC_URI = "file://mosquitto.conf file://mosquitto.service file://20-xenova-broker.conf file://20-xenova-mqtt.preset file://xenova-mqtt-init file://xenova-broker file://xenova_broker.py file://policy.json"
S = "${UNPACKDIR}"
inherit allarch

# All tools are already used by the dom0 image; no compiler or additional daemon.
RDEPENDS:${PN} = "mosquitto mosquitto-clients xenova-bringup python3-core python3-json python3-unixadmin"

do_install() {
    install -d "${D}${sysconfdir}/xenova/mqtt/conf.d" "${D}${sbindir}" "${D}${libdir}/xenova" "${D}${datadir}/xenova/mqtt" \
        "${D}${systemd_system_unitdir}/mosquitto.service.d" \
        "${D}${systemd_unitdir}/system-preset" \
        "${D}${sysconfdir}/systemd/system/multi-user.target.wants"
    install -m 0644 "${S}/mosquitto.conf" "${D}${sysconfdir}/xenova/mqtt/mosquitto.conf"
    install -m 0644 "${S}/mosquitto.service" "${D}${sysconfdir}/systemd/system/mosquitto.service"
    install -m 0644 "${S}/20-xenova-broker.conf" "${D}${systemd_system_unitdir}/mosquitto.service.d/20-xenova-broker.conf"
    install -m 0644 "${S}/20-xenova-mqtt.preset" "${D}${systemd_unitdir}/system-preset/20-xenova-mqtt.preset"
    install -m 0755 "${S}/xenova-mqtt-init" "${D}${sbindir}/xenova-mqtt-init"
    install -m 0755 "${S}/xenova-broker" "${D}${sbindir}/xenova-broker"
    install -m 0644 "${S}/xenova_broker.py" "${D}${libdir}/xenova/xenova_broker.py"
    install -m 0644 "${S}/policy.json" "${D}${datadir}/xenova/mqtt/policy.json"
    # This configuration package owns enablement; a bare mosquitto package stays disabled.
    ln -s "${systemd_system_unitdir}/mosquitto.service" \
        "${D}${sysconfdir}/systemd/system/multi-user.target.wants/mosquitto.service"
}

FILES:${PN} += "${libdir}/xenova ${datadir}/xenova/mqtt ${systemd_system_unitdir}/mosquitto.service.d ${systemd_unitdir}/system-preset/20-xenova-mqtt.preset"
CONFFILES:${PN} = "${sysconfdir}/xenova/mqtt/mosquitto.conf ${sysconfdir}/systemd/system/mosquitto.service"
