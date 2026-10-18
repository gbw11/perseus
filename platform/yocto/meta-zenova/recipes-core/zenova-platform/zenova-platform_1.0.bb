SUMMARY = "Persistent Zenova Xen VM lifecycle and boot readiness"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"
SRC_URI = "file://verify-zenova-runtime-artifacts.py file://zenova_vm.py file://zenova_runtime.py file://zenova_zephyr.py file://zenova-vm file://defaults.json file://zenova-journald.conf file://zenova-system.conf \
           file://srv-zenova.mount file://zenova-xen-ready.service \
           file://zenova-vm-shutdown.service file://zenova-vms.service \
           file://zenova-auth.conf file://zenova-first-boot file://zenova_first_boot.py \
           file://first_install_contract.py file://zenova-data-prepare.service \
           file://zenova-first-boot.service file://first-install-capabilities.json file://zenova-sshd-keys.conf file://prepare_instance.py file://prepare_rootfs.py file://guest_identity.py file://update-zenova-guest-ssh.py"
FILESEXTRAPATHS:prepend := "${THISDIR}/../../../../../scripts:${THISDIR}/../../../../first-install:${THISDIR}/../../../../guests/common:"
S = "${UNPACKDIR}"
inherit systemd

# 기존 이미지의 Python 코어에 사용자 승인한 표준 JSON 패키지를 추가한다.
RDEPENDS:${PN} = "python3-core python3-json python3-crypt iproute2 iproute2-ss util-linux-lsblk xen-tools-xl openssh-sshd \
    util-linux-sfdisk util-linux-partx util-linux-blockdev util-linux-blkid \
    e2fsprogs-e2fsck e2fsprogs-resize2fs e2fsprogs-dumpe2fs \
    util-linux-losetup util-linux-mount util-linux-umount util-linux-findmnt e2fsprogs-tune2fs coreutils"
SYSTEMD_SERVICE:${PN} = "zenova-first-boot.service srv-zenova.mount zenova-vm-shutdown.service zenova-vms.service"
SYSTEMD_AUTO_ENABLE:${PN} = "enable"

do_install() {
    install -d "${D}${sbindir}" "${D}${libdir}/zenova" "${D}${sysconfdir}/zenova" \
        "${D}${sysconfdir}/ssh" "${D}${systemd_system_unitdir}" "${D}/srv/zenova"
    install -m 0755 "${S}/verify-zenova-runtime-artifacts.py" "${D}${sbindir}/zenova-verify-artifacts"
    install -m 0755 "${S}/zenova-vm" "${D}${sbindir}/zenova-vm"
    for module in zenova_vm.py zenova_runtime.py zenova_zephyr.py; do
        install -m 0644 "${S}/$module" "${D}${libdir}/zenova/$module"
    done
    install -d "${D}${sysconfdir}/systemd/journald.conf.d" "${D}${sysconfdir}/systemd/system.conf.d"
    install -m 0644 "${S}/zenova-journald.conf" "${D}${sysconfdir}/systemd/journald.conf.d/20-zenova.conf"
    install -m 0644 "${S}/zenova-system.conf" "${D}${sysconfdir}/systemd/system.conf.d/20-zenova.conf"
    install -m 0644 "${S}/defaults.json" "${D}${sysconfdir}/zenova/defaults.json"
    install -m 0644 "${S}/zenova-auth.conf" "${D}${sysconfdir}/ssh/zenova-auth.conf"
    install -m 0755 "${S}/zenova-first-boot" "${D}${sbindir}/zenova-first-boot"
    install -m 0644 "${S}/zenova_first_boot.py" "${D}${libdir}/zenova/zenova_first_boot.py"
    install -m 0644 "${S}/first_install_contract.py" "${D}${libdir}/zenova/first_install_contract.py"
    for module in prepare_instance.py prepare_rootfs.py guest_identity.py; do
        install -m 0644 "${S}/$module" "${D}${libdir}/zenova/$module"
    done
    install -m 0644 "${S}/update-zenova-guest-ssh.py" "${D}${libdir}/zenova/update_guest_ssh.py"
    install -d "${D}${datadir}/zenova"
    install -m 0644 "${S}/first-install-capabilities.json" "${D}${datadir}/zenova/first-install-capabilities.json"
    install -d "${D}${sysconfdir}/systemd/system/sshd.service.d" "${D}${sysconfdir}/systemd/system-generators"
    install -m 0644 "${S}/zenova-sshd-keys.conf" "${D}${sysconfdir}/systemd/system/sshd.service.d/10-zenova-keys.conf"
    # systemd 259의 SSH generator가 AF_UNIX/VSOCK/추가 socket 경로를 재생성하지 않게 한다.
    ln -s /dev/null "${D}${sysconfdir}/systemd/system-generators/systemd-ssh-generator"
    for unit in zenova-data-prepare.service zenova-first-boot.service srv-zenova.mount zenova-xen-ready.service zenova-vm-shutdown.service zenova-vms.service; do
        install -m 0644 "${S}/$unit" "${D}${systemd_system_unitdir}/$unit"
    done
}
# 소유권은 enable 목록과 별개다. static readiness unit도 반드시 패키징한다.
FILES:${PN} += "${libdir}/zenova ${datadir}/zenova /srv/zenova \
    ${sysconfdir}/systemd/system/sshd.service.d/10-zenova-keys.conf \
    ${sysconfdir}/systemd/system-generators/systemd-ssh-generator \
    ${systemd_system_unitdir}/zenova-data-prepare.service \
    ${systemd_system_unitdir}/zenova-first-boot.service \
    ${systemd_system_unitdir}/srv-zenova.mount \
    ${systemd_system_unitdir}/zenova-xen-ready.service \
    ${systemd_system_unitdir}/zenova-vm-shutdown.service \
    ${systemd_system_unitdir}/zenova-vms.service"
CONFFILES:${PN} += "${sysconfdir}/zenova/defaults.json ${sysconfdir}/ssh/zenova-auth.conf ${sysconfdir}/systemd/journald.conf.d/20-zenova.conf ${sysconfdir}/systemd/system.conf.d/20-zenova.conf"
