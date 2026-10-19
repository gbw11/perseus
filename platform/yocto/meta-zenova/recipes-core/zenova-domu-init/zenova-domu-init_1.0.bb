SUMMARY = "Minimal /init and DHCP hook for the Zenova Xen test domU"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

SRC_URI = "file://init file://udhcpc.script"
S = "${UNPACKDIR}"

RDEPENDS:${PN} = "busybox iproute2 busybox-udhcpc"

do_install() {
    install -d "${D}"
    install -m 0755 "${S}/init" "${D}/init"
    install -d "${D}${sysconfdir}/zenova"
    install -m 0755 "${S}/udhcpc.script" "${D}${sysconfdir}/zenova/udhcpc.script"
}

FILES:${PN} += "/init ${sysconfdir}/zenova/udhcpc.script"
