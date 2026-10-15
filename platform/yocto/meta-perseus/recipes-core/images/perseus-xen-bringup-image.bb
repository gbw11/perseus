# A separate Raspberry Pi 4 / Xen test image. Does NOT overwrite xen-image-minimal.
require recipes-extended/images/xen-image-minimal.bb

SUMMARY = "Perseus Raspberry Pi 4 Xen minimal bring-up image"
DESCRIPTION = "xen-image-minimal plus diagnostic tools, isolated bridge/DHCP and a small preloaded Arm64 domU"

IMAGE_INSTALL:append = " perseus-bringup iproute2 bridge-utils dnsmasq kmod iputils-ping"

# BENCH IMAGE ONLY: local UART/root debugging. Do not connect to untrusted networks.
# This is limited to this image recipe; it does not alter xen-image-minimal.
IMAGE_FEATURES:append = " empty-root-password allow-empty-password"

# One guest uses the same Arm64 Image as dom0 (guest support is a preflight gate).
# Its tiny initramfs is embedded in the dom0 root filesystem during do_rootfs.
do_rootfs[depends] += "perseus-domu-initramfs:do_image_complete virtual/kernel:do_deploy"
ROOTFS_POSTPROCESS_COMMAND:append = " perseus_install_guest_test_assets; "

perseus_install_guest_test_assets() {
    guest_ramdisk="${DEPLOY_DIR_IMAGE}/perseus-domu-initramfs-${MACHINE}.rootfs.cpio.gz"
    guest_kernel="${DEPLOY_DIR_IMAGE}/Image"
    [ -f "$guest_ramdisk" ] || bbfatal "Missing domU test initramfs: $guest_ramdisk"
    [ -f "$guest_kernel" ] || bbfatal "Missing kernel Image: $guest_kernel"
    install -d "${IMAGE_ROOTFS}/opt/perseus/bringup"
    install -m 0644 "$guest_ramdisk" "${IMAGE_ROOTFS}/opt/perseus/bringup/domU-initramfs.cpio.gz"
    install -m 0644 "$guest_kernel" "${IMAGE_ROOTFS}/opt/perseus/bringup/Image"
    # Explicit getty on Xen dom0's hvc0 console, even if getty-generator misses it.
    # This only applies to the bring-up image, not the original image.
    getty_template="${IMAGE_ROOTFS}${systemd_system_unitdir}/serial-getty@.service"
    if [ -e "$getty_template" ]; then
        install -d "${IMAGE_ROOTFS}${sysconfdir}/systemd/system/getty.target.wants"
        ln -snf "${systemd_system_unitdir}/serial-getty@.service" \
            "${IMAGE_ROOTFS}${sysconfdir}/systemd/system/getty.target.wants/serial-getty@hvc0.service"
    else
        bbwarn "systemd serial-getty@.service was not installed; hvc0 login still needs checking"
    fi
}
