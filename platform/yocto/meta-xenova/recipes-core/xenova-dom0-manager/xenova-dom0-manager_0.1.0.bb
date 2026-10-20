SUMMARY = "Xenova platform-backed dom0 HTTP management service"
LICENSE = "CLOSED"
FILESEXTRAPATHS:prepend := "${THISDIR}/../../../../../services/dom0-manager:"
SRC_URI = "file://dom0_manager file://pyproject.toml file://xenova-dom0-manager.service file://dom0-manager.env"
S = "${UNPACKDIR}"

inherit allarch python3-dir systemd

RDEPENDS:${PN} = "python3-core python3-fastapi python3-uvicorn python3-json python3-logging python3-threading python3-netclient python3-shell python3-crypt python3-math xenova-platform"
SYSTEMD_SERVICE:${PN} = "xenova-dom0-manager.service"
SYSTEMD_AUTO_ENABLE:${PN} = "enable"

do_install() {
    install -d "${D}${PYTHON_SITEPACKAGES_DIR}/dom0_manager" "${D}${systemd_system_unitdir}" "${D}${sysconfdir}/xenova" "${D}${datadir}/xenova"
    cd "${S}/dom0_manager"
    find . -type f -name '*.py' | while IFS= read -r module; do
        install -D -m 0644 "${S}/dom0_manager/$module" "${D}${PYTHON_SITEPACKAGES_DIR}/dom0_manager/$module"
    done
    install -m 0644 "${S}/pyproject.toml" "${D}${datadir}/xenova/dom0-manager-pyproject.toml"
    install -m 0644 "${S}/xenova-dom0-manager.service" "${D}${systemd_system_unitdir}/xenova-dom0-manager.service"
    install -m 0644 "${S}/dom0-manager.env" "${D}${sysconfdir}/xenova/dom0-manager.env"
}

FILES:${PN} += "${PYTHON_SITEPACKAGES_DIR}/dom0_manager ${datadir}/xenova"
CONFFILES:${PN} += "${sysconfdir}/xenova/dom0-manager.env"
