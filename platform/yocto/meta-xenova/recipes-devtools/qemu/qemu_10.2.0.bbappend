# Xen libxl의 qemu-system-i386 -vnc none을 수용한다. VNC listener는 시작하지 않는다.
# 고정 OE qemu.inc는 pixman을 기본 PACKAGECONFIG에 넣지 않아 VNC auto 감지가 꺼진다.
PACKAGECONFIG[vnc] = "--enable-vnc,--disable-vnc"
PACKAGECONFIG:append:class-target = " pixman vnc"

# 조립기가 이전 바이너리를 새 기능으로 오인하지 않도록 실제 빌드 결과도 설치한다.
do_install:append:class-target() {
    grep -q '^#define CONFIG_VNC' "${B}/config-host.h" || bbfatal "dom0 QEMU requires VNC option support"
    grep -q '^#define CONFIG_XEN_BACKEND' "${B}/config-host.h" || bbfatal "dom0 QEMU requires Xen backend support"
    grep -q '^#define CONFIG_XEN 1' "${B}/i386-softmmu-config-target.h" || bbfatal "i386 Xen target missing"
    install -d "${D}${datadir}/xenova"
    install -m 0644 "${B}/config-host.h" "${D}${datadir}/xenova/qemu-config-host.h"
    install -m 0644 "${B}/i386-softmmu-config-target.h" "${D}${datadir}/xenova/qemu-i386-config-target.h"
}
