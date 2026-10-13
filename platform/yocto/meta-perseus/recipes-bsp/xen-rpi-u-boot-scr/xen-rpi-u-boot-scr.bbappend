
# Perseus - Xen Raspberry Pi 4 U-Boot script compatibility
# Yocto 6.0 Wrynose
#
# The upstream recipe uses S = "${WORKDIR}/sources"
# and UNPACKDIR = "${S}", which fails do_unpack validation.
# Keep source unpacking under WORKDIR/sources explicitly.

UNPACKDIR = "${WORKDIR}/sources"
S = "${UNPACKDIR}"
