SUMMARY = "Pinned official ARM64 Tailscale client and daemon"
DESCRIPTION = "Official static release with TS-2026-013 fixes; Xenova persistence is a separate append"
HOMEPAGE = "https://tailscale.com/"
LICENSE = "BSD-3-Clause"
LIC_FILES_CHKSUM = "file://${UNPACKDIR}/tailscale-LICENSE-${PV};md5=cadeae10a8856ddfdb129866b75b33e3"

# The pinned meta-networking 1.94.2 recipe uses Go 1.26.8. TS-2026-013 requires
# the certificate distrust in 1.104.2 / Go 1.27.3. Use the official fixed binary
# rather than changing the shared compiler or external layer commits.
SRC_URI = "https://pkgs.tailscale.com/stable/tailscale_${PV}_arm64.tgz;name=archive \
           https://raw.githubusercontent.com/tailscale/tailscale/v${PV}/LICENSE;name=license;downloadfilename=tailscale-LICENSE-${PV}"
SRC_URI[archive.sha256sum] = "5776aaa901b5ef1916e66be5bb54b9f481cd399c9c28db123a8cab620efa237b"
SRC_URI[license.sha256sum] = "a7ca6186a7963a0a60740f6047760eecd7a0234e8c38bd7e1e0bbcb324bda45b"

S = "${UNPACKDIR}/tailscale_${PV}_arm64"
COMPATIBLE_HOST = "aarch64.*-linux"
PACKAGE_ARCH = "${TUNE_PKGARCH}"

inherit systemd
do_configure[noexec] = "1"
do_compile[noexec] = "1"

PACKAGES =+ "${PN}d"
SYSTEMD_PACKAGES = "${PN}d"
SYSTEMD_SERVICE:${PN}d = "tailscaled.service"
FILES:${PN}d = "${sbindir}/tailscaled ${sysconfdir}/default/tailscaled ${systemd_system_unitdir}"
FILES:${PN}:append = " ${datadir}/licenses/tailscale"
CONFFILES:${PN}d = "${sysconfdir}/default/tailscaled"
RDEPENDS:${PN}d = "iptables"
RRECOMMENDS:${PN}d = "kernel-module-xt-mark kernel-module-xt-tcpudp kernel-module-xt-masquerade"

# Upstream distributes stripped static Go executables. Keep arch/dependency QA.
INHIBIT_PACKAGE_STRIP = "1"
INHIBIT_PACKAGE_DEBUG_SPLIT = "1"
INSANE_SKIP:${PN} += "already-stripped"
INSANE_SKIP:${PN}d += "already-stripped"

do_install() {
    install -d "${D}${bindir}" "${D}${sbindir}" "${D}${sysconfdir}/default" "${D}${systemd_system_unitdir}"
    install -m 0755 "${S}/tailscale" "${D}${bindir}/tailscale"
    install -m 0755 "${S}/tailscaled" "${D}${sbindir}/tailscaled"
    install -m 0644 "${S}/systemd/tailscaled.service" "${D}${systemd_system_unitdir}/tailscaled.service"
    install -m 0644 "${S}/systemd/tailscaled.defaults" "${D}${sysconfdir}/default/tailscaled"
    install -d "${D}${datadir}/licenses/tailscale"
    install -m 0644 "${UNPACKDIR}/tailscale-LICENSE-${PV}" "${D}${datadir}/licenses/tailscale/LICENSE"
}
