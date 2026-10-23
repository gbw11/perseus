SUMMARY = "Shared MQTT contract v1 Python client"
LICENSE = "CLOSED"
FILESEXTRAPATHS:prepend := "${THISDIR}/../../../../services/vm-agent/mqtt-client:"
SRC_URI = "file://xenova_mqtt file://pyproject.toml"
S = "${UNPACKDIR}"

inherit allarch python3-dir

RDEPENDS:${PN} = "python3-core python3-paho-mqtt python3-json python3-logging python3-threading python3-netclient python3-datetime"

do_install() {
    install -d "${D}${PYTHON_SITEPACKAGES_DIR}/xenova_mqtt" "${D}${datadir}/xenova"
    cd "${S}/xenova_mqtt"
    find . -type f -name '*.py' | while IFS= read -r module; do
        install -D -m 0644 "${S}/xenova_mqtt/$module" "${D}${PYTHON_SITEPACKAGES_DIR}/xenova_mqtt/$module"
    done
    install -m 0644 "${S}/pyproject.toml" "${D}${datadir}/xenova/mqtt-client-pyproject.toml"
}

FILES:${PN} += "${PYTHON_SITEPACKAGES_DIR}/xenova_mqtt ${datadir}/xenova"
