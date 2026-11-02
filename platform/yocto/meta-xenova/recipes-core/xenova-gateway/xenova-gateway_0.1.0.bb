SUMMARY = "Xenova Gateway authority and bounded device output"
LICENSE = "CLOSED"
FILESEXTRAPATHS:prepend := "${THISDIR}/../../../../services/gateway:${THISDIR}/../../../../apps/drive/world/python:${THISDIR}/files:"
SRC_URI = "file://xenova_gateway file://xenova_drive_world file://examples file://pyproject.toml file://xenova-gateway.service file://gateway.env.example"
S = "${UNPACKDIR}"

inherit allarch python3-dir systemd

PACKAGES =+ "${PN}-client"
RDEPENDS:${PN}-client = "python3-core python3-json python3-netclient python3-shell python3-math"
FILES:${PN}-client = "${PYTHON_SITEPACKAGES_DIR}/xenova_gateway/__init__.py ${PYTHON_SITEPACKAGES_DIR}/xenova_gateway/client.py ${PYTHON_SITEPACKAGES_DIR}/xenova_gateway/ipc_contract.py"
RDEPENDS:${PN} = "${PN}-client (= ${EXTENDPKGV}) python3-core python3-json python3-logging python3-threading python3-netclient python3-datetime python3-shell python3-math python3-paho-mqtt python3-pyserial python3-protobuf xenova-mqtt-client xenova-platform"
SYSTEMD_SERVICE:${PN} = "xenova-gateway.service"
# Installation does not initialize Authority, choose vehicle values, or grant output.
SYSTEMD_AUTO_ENABLE:${PN} = "disable"

do_install() {
    install -d "${D}${PYTHON_SITEPACKAGES_DIR}/xenova_gateway" "${D}${systemd_system_unitdir}" "${D}${datadir}/xenova"
    cd "${S}/xenova_gateway"
    find . -type f -name '*.py' | while IFS= read -r module; do
        install -D -m 0644 "${S}/xenova_gateway/$module" "${D}${PYTHON_SITEPACKAGES_DIR}/xenova_gateway/$module"
    done
    install -d "${D}${PYTHON_SITEPACKAGES_DIR}/xenova_drive_world"
    for module in "${S}/xenova_drive_world/"*.py; do
        install -m 0644 "$module" "${D}${PYTHON_SITEPACKAGES_DIR}/xenova_drive_world/"
    done
    install -m 0644 "${S}/pyproject.toml" "${D}${datadir}/xenova/gateway-pyproject.toml"
    install -m 0644 "${S}/gateway.env.example" "${D}${datadir}/xenova/gateway.env.example"
    install -m 0644 "${S}/xenova-gateway.service" "${D}${systemd_system_unitdir}/xenova-gateway.service"
    install -d "${D}${datadir}/xenova/gateway-examples"
    install -m 0644 "${S}/examples/"*.py "${D}${datadir}/xenova/gateway-examples/"
}

FILES:${PN} += "${PYTHON_SITEPACKAGES_DIR}/xenova_gateway ${PYTHON_SITEPACKAGES_DIR}/xenova_drive_world ${datadir}/xenova"
