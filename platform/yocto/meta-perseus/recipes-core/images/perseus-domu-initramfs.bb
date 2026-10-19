SUMMARY = "Perseus ephemeral Arm64 Xen guest initramfs for bring-up"
DESCRIPTION = "Small initramfs with an interactive hvc0 shell and DHCP client; no disk image required"
LICENSE = "MIT"

# Must be defined before inheriting core-image/image classes.
IMAGE_FSTYPES = "cpio.gz"
IMAGE_FSTYPES:remove = "rpi-sdimg"
IMAGE_INSTALL = "busybox base-passwd iproute2 busybox-udhcpc perseus-domu-init"
IMAGE_FEATURES = ""
IMAGE_LINGUAS = ""
IMAGE_ROOTFS_SIZE = "16384"

inherit core-image
