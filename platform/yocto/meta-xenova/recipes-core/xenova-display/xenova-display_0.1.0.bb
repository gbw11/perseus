SUMMARY = "Xenova display feed, X11 kiosk and capture runtime"
LICENSE = "CLOSED"
FILESEXTRAPATHS:prepend := "${THISDIR}/../../../../services/display/router:${THISDIR}/../../../../services/display/router/deploy:${THISDIR}/../../../../services/display/router/deploy/native:${THISDIR}/../../../../services/display/router/examples:${XENOVA_DISPLAY_WEB_ASSETS}:"
XENOVA_DISPLAY_WEB_ASSETS ?= "${TOPDIR}/xenova-web-assets"
SRC_URI = "file://xenova_display file://pyproject.toml file://xenova-display-feed.service file://xenova-display-kiosk.service file://xenova-display-stream.service file://x11-runtime.json file://qemu-bindings.json file://display-web file://xenova-display-hdmi.service file://xenova-display.target file://display-Xorg.conf.example file://display-kiosk.env.example file://native-x11-runtime.json"
S = "${UNPACKDIR}"

inherit allarch python3-dir systemd useradd

USERADD_PACKAGES = "${PN}"
GROUPADD_PARAM:${PN} = "--system xenova-display"
USERADD_PARAM:${PN} = "--system --gid xenova-display --home-dir /var/lib/xenova-display --no-create-home --shell /sbin/nologin --password '!' xenova-display"


RDEPENDS:${PN} = "xenova-dom0-manager xenova-mqtt-client python3-playwright chromium-x11 xserver-xorg xrandr xauth xinit openbox dbus ffmpeg source-han-sans-kr-fonts fontconfig xf86-input-libinput xf86-video-fbdev xf86-video-modesetting util-linux-setpriv python3-core"

SYSTEMD_SERVICE:${PN} = "xenova-display-feed.service xenova-display.target"
SYSTEMD_AUTO_ENABLE:${PN} = "enable"

do_install() {
    test -s "${S}/display-web/index.html" || bbfatal "Build the pinned vm-screens frontend and set XENOVA_DISPLAY_WEB_ASSETS"
    install -d "${D}${PYTHON_SITEPACKAGES_DIR}/xenova_display" "${D}${datadir}/xenova/display/web"         "${D}${datadir}/xenova/display/examples" "${D}${systemd_system_unitdir}" "${D}${nonarch_libdir}/systemd/user"
    cd "${S}/xenova_display"
    find . -type f \( -name '*.py' -o -name '*.html' \) | while IFS= read -r asset; do
        install -D -m 0644 "$asset" "${D}${PYTHON_SITEPACKAGES_DIR}/xenova_display/$asset"
    done
    cp -R "${S}/display-web/." "${D}${datadir}/xenova/display/web/"
    install -m 0644 "${S}/pyproject.toml" "${D}${datadir}/xenova/display/pyproject.toml"
    install -m 0644 "${S}/xenova-display-feed.service" "${D}${systemd_system_unitdir}/"
    for unit in xenova-display-kiosk.service xenova-display-stream.service; do
        sed 's|/opt/xenova-display/bin/python|/usr/bin/python3|g' "${S}/$unit" > "${D}${nonarch_libdir}/systemd/user/$unit"
    done
    install -m 0644 "${S}/xenova-display-hdmi.service" "${S}/xenova-display.target" "${D}${systemd_system_unitdir}/"
    install -m 0644 "${S}/display-Xorg.conf.example" "${S}/display-kiosk.env.example" "${S}/native-x11-runtime.json" "${D}${datadir}/xenova/display/examples/"
    install -d -m 0700 "${D}/var/lib/xenova-display"
    chown xenova-display:xenova-display "${D}/var/lib/xenova-display"
    install -m 0644 "${S}/x11-runtime.json" "${S}/qemu-bindings.json" "${D}${datadir}/xenova/display/examples/"
}

FILES:${PN} += "${PYTHON_SITEPACKAGES_DIR}/xenova_display ${datadir}/xenova/display ${nonarch_libdir}/systemd/user /var/lib/xenova-display ${systemd_system_unitdir}/xenova-display-hdmi.service"
