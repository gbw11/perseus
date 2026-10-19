
# Xenova - Xen Raspberry Pi 4 U-Boot script compatibility
# Yocto 6.0 Wrynose
#
# The upstream recipe uses S = "${WORKDIR}/sources"
# and UNPACKDIR = "${S}", which fails do_unpack validation.
# Keep source unpacking under WORKDIR/sources explicitly.

UNPACKDIR = "${WORKDIR}/sources"
S = "${UNPACKDIR}"

# 실보드에서 검증한 working FDT 복사와 MMC 선택을 프로젝트 템플릿에 반영한다.
FILESEXTRAPATHS:prepend := "${THISDIR}/files:"
