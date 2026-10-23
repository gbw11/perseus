SUMMARY = "Xenova platform-backed dom0 HTTP management service"
LICENSE = "CLOSED"
FILESEXTRAPATHS:prepend := "${THISDIR}/../../../../services/dom0-manager:${THISDIR}/../../../../services/dom0-manager/deploy:"
SRC_URI = "file://dom0_manager file://pyproject.toml file://xenova-dom0-manager.service file://dom0-manager.env file://xenova-recovery-worker.service"
S = "${UNPACKDIR}"

inherit allarch python3-dir systemd

# PTY 콘솔의 tty/termios와 pyproject에 선언된 WebSocket 전송을 설치한다.
RDEPENDS:${PN} = "python3-core python3-fastapi python3-uvicorn python3-websockets python3-terminal python3-json python3-logging python3-threading python3-netclient python3-shell python3-crypt python3-math python3-datetime xenova-platform xen-tools-xenstat xenova-mqtt-client"
# 통계/감시기 시험 소스를 옮겨도 xentop·공용 MQTT runtime을 다시 설치하지 않게 한다.
SYSTEMD_SERVICE:${PN} = "xenova-dom0-manager.service xenova-recovery-worker.service"
SYSTEMD_AUTO_ENABLE:${PN} = "enable"

do_install() {
    install -d "${D}${PYTHON_SITEPACKAGES_DIR}/dom0_manager" "${D}${systemd_system_unitdir}" "${D}${sysconfdir}/xenova" "${D}${datadir}/xenova"
    cd "${S}/dom0_manager"
    find . -type f -name '*.py' | while IFS= read -r module; do
        install -D -m 0644 "${S}/dom0_manager/$module" "${D}${PYTHON_SITEPACKAGES_DIR}/dom0_manager/$module"
    done
    install -m 0644 "${S}/pyproject.toml" "${D}${datadir}/xenova/dom0-manager-pyproject.toml"
    install -m 0644 "${S}/xenova-dom0-manager.service" "${D}${systemd_system_unitdir}/xenova-dom0-manager.service"
    install -m 0644 "${S}/xenova-recovery-worker.service" "${D}${systemd_system_unitdir}/xenova-recovery-worker.service"
    install -m 0600 "${S}/dom0-manager.env" "${D}${sysconfdir}/xenova/dom0-manager.env"
}

FILES:${PN} += "${PYTHON_SITEPACKAGES_DIR}/dom0_manager ${datadir}/xenova"
CONFFILES:${PN} += "${sysconfdir}/xenova/dom0-manager.env"
