SUMMARY = "Xenova common drive Gateway and serial transport"
LICENSE = "CLOSED"
FILESEXTRAPATHS:prepend := "${THISDIR}/../../../../services/gateway:"
SRC_URI = "file://xenova_gateway file://pyproject.toml file://README.md file://xenova-gateway.service"
S = "${UNPACKDIR}"

inherit allarch python3-dir systemd

RDEPENDS:${PN} = "xenova-platform xenova-mqtt-client python3-pyserial python3-terminal python3-logging python3-threading"
SYSTEMD_SERVICE:${PN} = "xenova-gateway.service"
# UUID/broker credentials, original authority and serial device are installation inputs.
SYSTEMD_AUTO_ENABLE:${PN} = "disable"

do_install() {
    install -d "${D}${PYTHON_SITEPACKAGES_DIR}/xenova_gateway" "${D}${datadir}/xenova/gateway" "${D}${systemd_system_unitdir}"
    cd "${S}/xenova_gateway"
    find . -type f -name '*.py' | while IFS= read -r module; do
        install -D -m 0644 "$module" "${D}${PYTHON_SITEPACKAGES_DIR}/xenova_gateway/$module"
    done
    install -m 0644 "${S}/pyproject.toml" "${S}/README.md" "${D}${datadir}/xenova/gateway/"
    install -m 0644 "${S}/xenova-gateway.service" "${D}${systemd_system_unitdir}/"
}

FILES:${PN} += "${PYTHON_SITEPACKAGES_DIR}/xenova_gateway ${datadir}/xenova/gateway"
