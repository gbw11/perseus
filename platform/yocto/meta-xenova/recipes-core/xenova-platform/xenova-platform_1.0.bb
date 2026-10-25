SUMMARY = "Persistent Xenova Xen VM lifecycle and boot readiness"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"
SRC_URI = "file://xenova_cpu.py file://cpu-policy-preset.json file://xenova-cpu-prepare.service file://xenova_zephyr_control.py file://xenova_zephyr_transition.py file://xenova_resources.py file://xenova_execution.py file://xenova_guest_process.py file://xenova_recovery.py file://xenova_domain_identity.py file://xenova-domain-identity file://execution-policy.json file://xenova-host-admission.service file://verify-xenova-runtime-artifacts.py file://xenova_management.py file://xenova_management_cli.py file://xenova_catalog.py file://xenova_wifi.py file://artifact_io.py file://xenova-wifi file://xenova-wifi@.service file://xenova-wifi-modprobe.conf file://xenova_vm.py file://xenova_runtime.py file://xenova_trace.py file://xenova_zephyr.py file://xenova-vm file://defaults.json file://xenova-journald.conf file://xenova-system.conf \
           file://srv-xenova.mount file://xenova-xen-ready.service \
           file://xenova-vm-shutdown.service file://xenova-vms.service \
           file://xenova-auth.conf file://xenova-first-boot file://xenova_first_boot.py \
           file://first_install_contract.py file://xenova-data-prepare.service \
           file://xenova-first-boot.service file://first-install-capabilities.json file://xenova-sshd-keys.conf file://prepare_instance.py file://prepare_rootfs.py file://guest_identity.py file://update-xenova-guest-ssh.py file://xen_config.py file://ubuntu file://debian"
FILESEXTRAPATHS:prepend := "${THISDIR}/../../../../scripts:${THISDIR}/../../../../first-install:${THISDIR}/../../../../guests/common:${THISDIR}/../../../../guests:"
S = "${UNPACKDIR}"
inherit systemd

# 기존 이미지의 Python 코어에 사용자 승인한 표준 JSON 패키지를 추가한다.
RDEPENDS:${PN} = "python3-core python3-json python3-stringold python3-crypt python3-ctypes python3-unixadmin xen-tools-libxenmanage wpa-supplicant wpa-supplicant-cli linux-firmware-bcm43455 wireless-regdb-static iw kernel-module-brcmfmac kernel-module-brcmfmac-cyw iproute2 iproute2-ss util-linux-lsblk xen-tools-xl xen-tools-xenstore openssh-sshd systemd-extra-utils \
    util-linux-sfdisk util-linux-partx util-linux-blockdev util-linux-blkid \
    e2fsprogs-e2fsck e2fsprogs-resize2fs e2fsprogs-dumpe2fs \
    util-linux-losetup util-linux-mount util-linux-umount util-linux-findmnt e2fsprogs-tune2fs coreutils"
SYSTEMD_SERVICE:${PN} = "xenova-host-admission.service xenova-first-boot.service srv-xenova.mount xenova-vm-shutdown.service xenova-vms.service"
SYSTEMD_AUTO_ENABLE:${PN} = "enable"

do_install() {
    install -d "${D}${sbindir}" "${D}${libdir}/xenova" "${D}${sysconfdir}/xenova" \
        "${D}${sysconfdir}/ssh" "${D}${systemd_system_unitdir}" "${D}/srv/xenova"
    install -m 0755 "${S}/verify-xenova-runtime-artifacts.py" "${D}${sbindir}/xenova-verify-artifacts"
    install -m 0755 "${S}/xenova-vm" "${D}${sbindir}/xenova-vm"
    install -m 0755 "${S}/xenova-domain-identity" "${D}${sbindir}/xenova-domain-identity"
    install -m 0644 "${S}/cpu-policy-preset.json" "${D}${sysconfdir}/xenova/cpu-policy-preset.json"
    install -m 0644 "${S}/execution-policy.json" "${D}${sysconfdir}/xenova/execution-policy.json"
    install -m 0755 "${S}/xenova-wifi" "${D}${sbindir}/xenova-wifi"
    install -d "${D}${sysconfdir}/modprobe.d"
    install -m 0644 "${S}/xenova-wifi-modprobe.conf" "${D}${sysconfdir}/modprobe.d/xenova-wifi.conf"
    for module in xenova_cpu.py xenova_guest_process.py xenova_zephyr_control.py xenova_zephyr_transition.py xenova_resources.py xenova_execution.py xenova_recovery.py xenova_domain_identity.py xenova_vm.py xenova_runtime.py xenova_trace.py xenova_zephyr.py xenova_management.py xenova_management_cli.py xenova_catalog.py xenova_wifi.py artifact_io.py; do
        install -m 0644 "${S}/$module" "${D}${libdir}/xenova/$module"
    done
    install -d "${D}${sysconfdir}/systemd/journald.conf.d" "${D}${sysconfdir}/systemd/system.conf.d"
    install -m 0644 "${S}/xenova-journald.conf" "${D}${sysconfdir}/systemd/journald.conf.d/20-xenova.conf"
    install -m 0644 "${S}/xenova-system.conf" "${D}${sysconfdir}/systemd/system.conf.d/20-xenova.conf"
    install -m 0644 "${S}/defaults.json" "${D}${sysconfdir}/xenova/defaults.json"
    install -m 0644 "${S}/xenova-auth.conf" "${D}${sysconfdir}/ssh/xenova-auth.conf"
    install -m 0755 "${S}/xenova-first-boot" "${D}${sbindir}/xenova-first-boot"
    install -m 0644 "${S}/xenova_first_boot.py" "${D}${libdir}/xenova/xenova_first_boot.py"
    install -m 0644 "${S}/first_install_contract.py" "${D}${libdir}/xenova/first_install_contract.py"
    for module in prepare_instance.py prepare_rootfs.py guest_identity.py xen_config.py; do
        install -m 0644 "${S}/$module" "${D}${libdir}/xenova/$module"
    done
    for os in ubuntu debian; do
        install -d "${D}${libdir}/xenova/xen-templates/$os"
        install -m 0644 "${S}/$os/xen.cfg.in" "${D}${libdir}/xenova/xen-templates/$os/xen.cfg.in"
    done
    install -m 0644 "${S}/update-xenova-guest-ssh.py" "${D}${libdir}/xenova/update_guest_ssh.py"
    install -d "${D}${datadir}/xenova"
    install -m 0644 "${S}/first-install-capabilities.json" "${D}${datadir}/xenova/first-install-capabilities.json"
    install -d "${D}${sysconfdir}/systemd/system/sshd.service.d" "${D}${sysconfdir}/systemd/system-generators"
    install -m 0644 "${S}/xenova-sshd-keys.conf" "${D}${sysconfdir}/systemd/system/sshd.service.d/10-xenova-keys.conf"
    # systemd 259의 SSH generator가 AF_UNIX/VSOCK/추가 socket 경로를 재생성하지 않게 한다.
    ln -s /dev/null "${D}${sysconfdir}/systemd/system-generators/systemd-ssh-generator"
    for unit in xenova-cpu-prepare.service xenova-host-admission.service xenova-wifi@.service xenova-data-prepare.service xenova-first-boot.service srv-xenova.mount xenova-xen-ready.service xenova-vm-shutdown.service xenova-vms.service; do
        install -m 0644 "${S}/$unit" "${D}${systemd_system_unitdir}/$unit"
    done
}
# 소유권은 enable 목록과 별개다. static readiness unit도 반드시 패키징한다.
FILES:${PN} += "${systemd_system_unitdir}/xenova-cpu-prepare.service ${sysconfdir}/modprobe.d/xenova-wifi.conf ${systemd_system_unitdir}/xenova-host-admission.service ${libdir}/xenova ${datadir}/xenova /srv/xenova \
    ${sysconfdir}/systemd/system/sshd.service.d/10-xenova-keys.conf \
    ${sysconfdir}/systemd/system-generators/systemd-ssh-generator \
    ${systemd_system_unitdir}/xenova-data-prepare.service \
    ${systemd_system_unitdir}/xenova-first-boot.service \
    ${systemd_system_unitdir}/srv-xenova.mount \
    ${systemd_system_unitdir}/xenova-xen-ready.service \
    ${systemd_system_unitdir}/xenova-vm-shutdown.service \
    ${systemd_system_unitdir}/xenova-vms.service ${systemd_system_unitdir}/xenova-wifi@.service"
CONFFILES:${PN} += "${sysconfdir}/xenova/cpu-policy-preset.json ${sysconfdir}/modprobe.d/xenova-wifi.conf ${sysconfdir}/xenova/execution-policy.json ${sysconfdir}/xenova/defaults.json ${sysconfdir}/ssh/xenova-auth.conf ${sysconfdir}/systemd/journald.conf.d/20-xenova.conf ${sysconfdir}/systemd/system.conf.d/20-xenova.conf"
