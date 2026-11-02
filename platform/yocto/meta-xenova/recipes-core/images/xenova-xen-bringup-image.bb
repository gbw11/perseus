# A separate Raspberry Pi 4 / Xen test image. Does NOT overwrite xen-image-minimal.
require recipes-extended/images/xen-image-minimal.bb

SUMMARY = "Xenova Raspberry Pi 4 Xen minimal bring-up image"
DESCRIPTION = "xen-image-minimal plus diagnostic tools, isolated bridge/DHCP and a small preloaded Arm64 domU"

IMAGE_INSTALL:append = " xenova-platform xenova-bringup xenova-dom0-manager xenova-mqtt-client xenova-mqtt-broker mosquitto mosquitto-clients iproute2 bridge-utils dnsmasq kmod iputils-ping tzdata-asia"
# Gateway is packaged but remains disabled until approved instance settings exist.
IMAGE_INSTALL:append = " xenova-gateway"

# External management is optional at runtime; never a prerequisite of VM startup.
IMAGE_INSTALL:append = " tailscale tailscaled kernel-module-tun kernel-module-ipv6"

# BSP의 DEPLOYPAYLOAD는 rpi-sdimg 전용이다. WIC에도 boot.scr이 읽는 xen을 넣는다.
IMAGE_BOOT_FILES:append = " xen-${MACHINE};xen"
do_image_wic[depends] += "xen:do_deploy"

# 팀 개발망 최초 로그인. 기존 사용자 암호는 부팅 시 재설정하지 않는다.
IMAGE_FEATURES:remove = "empty-root-password allow-empty-password debug-tweaks serial-autologin-root"
IMAGE_FEATURES:append = " allow-root-login"
inherit extrausers
# 1234의 SHA-512 crypt 해시. 개발용 공개 초기값이며 런타임 reset 서비스는 없다.
EXTRA_USERS_PARAMS = "usermod -p '\$6\$perseus-dev\$uqz/PNLD2CDIZm1VIVOFPkMANu8CHr2KHPKO3th9aIL6nu6f.aLwxZ8oHjw3haeQHSsLzWokvwcuaAcMVpy800' root;"
ROOTFS_POSTPROCESS_COMMAND:append = " xenova_platform_policy; "

xenova_platform_policy() {
    # Xen의 기본 저장/복원·자동 시작과 Xenova의 중복 실행을 막는다.
    install -d "${IMAGE_ROOTFS}${sysconfdir}/systemd/system"
    ln -snf /dev/null "${IMAGE_ROOTFS}${sysconfdir}/systemd/system/xendomains.service"
    # 공통 SD에서 PID1이 부팅 초기에 machine-id를 확정한다. 런타임에 ID를 교체하지 않는다.
    printf 'uninitialized\n' > "${IMAGE_ROOTFS}${sysconfdir}/machine-id"
    install -d "${IMAGE_ROOTFS}${localstatedir}/lib/dbus"
    ln -snf /etc/machine-id "${IMAGE_ROOTFS}${localstatedir}/lib/dbus/machine-id"
    rm -f "${IMAGE_ROOTFS}${sysconfdir}"/ssh/ssh_host_*_key* \
        "${IMAGE_ROOTFS}${localstatedir}/lib/systemd/random-seed"
    # OpenSSH는 먼저 읽은 값을 사용한다. 다른 include보다 앞에 초기 정책을 둔다.
    for config in sshd_config sshd_config_readonly; do
        target="${IMAGE_ROOTFS}${sysconfdir}/ssh/$config"
        [ -f "$target" ] || continue
        { printf 'Include /etc/ssh/xenova-auth.conf\n'; cat "$target"; } > "$target.xenova"
        mv "$target.xenova" "$target"
    done
}

# One guest uses the same Arm64 Image as dom0 (guest support is a preflight gate).
# Its tiny initramfs is embedded in the dom0 root filesystem during do_rootfs.
do_rootfs[depends] += "xenova-domu-initramfs:do_image_complete virtual/kernel:do_deploy"
ROOTFS_POSTPROCESS_COMMAND:append = " xenova_install_guest_test_assets; "

xenova_install_guest_test_assets() {
    guest_ramdisk="${DEPLOY_DIR_IMAGE}/xenova-domu-initramfs-${MACHINE}.rootfs.cpio.gz"
    guest_kernel="${DEPLOY_DIR_IMAGE}/Image"
    [ -f "$guest_ramdisk" ] || bbfatal "Missing domU test initramfs: $guest_ramdisk"
    [ -f "$guest_kernel" ] || bbfatal "Missing kernel Image: $guest_kernel"
    install -d "${IMAGE_ROOTFS}/opt/xenova/bringup"
    install -m 0644 "$guest_ramdisk" "${IMAGE_ROOTFS}/opt/xenova/bringup/domU-initramfs.cpio.gz"
    install -m 0644 "$guest_kernel" "${IMAGE_ROOTFS}/opt/xenova/bringup/Image"
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

# 표시 시간대만 설정한다. UTC 시각 보정은 timesyncd가 별도로 수행한다.
ROOTFS_POSTPROCESS_COMMAND:append = " xenova_set_timezone; "
xenova_set_timezone() {
    zone="${IMAGE_ROOTFS}${datadir}/zoneinfo/Asia/Seoul"
    [ -f "$zone" ] || bbfatal "Missing Asia/Seoul timezone data"
    install -d "${IMAGE_ROOTFS}${sysconfdir}"
    ln -snf "${datadir}/zoneinfo/Asia/Seoul" "${IMAGE_ROOTFS}${sysconfdir}/localtime"
    printf 'Asia/Seoul\n' > "${IMAGE_ROOTFS}${sysconfdir}/timezone"
}
