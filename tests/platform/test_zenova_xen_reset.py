"""고정 Xen 리셋 소스에 패치를 적용하고 실제 함수의 DT 매칭/MMIO 순서를 native 대역으로 검사한다."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from first_install_fixture import ROOT


class XenResetTest(unittest.TestCase):
    def test_pinned_source_patch_preserves_legacy_and_accepts_deployed_bcm2711_node(self):
        patch = ROOT / 'platform/yocto/meta-zenova/recipes-extended/xen/files/0001-arm-rpi4-find-bcm2711-power-management-watchdog.patch'
        fixture = ROOT / 'tests/platform/fixtures/xen-4.21/brcm-raspberry-pi.c'
        with tempfile.TemporaryDirectory(prefix='zenova-reset-source-') as directory:
            root = Path(directory)
            target = root / 'xen/arch/arm/platforms/brcm-raspberry-pi.c'
            target.parent.mkdir(parents=True)
            target.write_bytes(fixture.read_bytes())
            result = subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(patch)],
                                    cwd=root, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            text = target.read_text()
            self.assertIn('DT_MATCH_COMPATIBLE("brcm,bcm2711-pm")', text)
            body = text[text.index('#define PM_PASSWORD'):text.index('PLATFORM_START')]
            prefix = r"""
#include <assert.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#define __iomem
#define PAGE_MASK (~4095UL)
#define PAGE_SIZE 4096
#define printk(...) ((void)0)
typedef unsigned long paddr_t;
struct dt_device_node { int unused; };
static struct dt_device_node node;
static const char *wanted;
static int fail_address, fail_map, writes;
static uint32_t registers[1024];
static struct dt_device_node *dt_find_compatible_node(void *a, void *b, const char *compat)
{ (void)a; (void)b; return !strcmp(compat, wanted) ? &node : NULL; }
static int dt_device_get_paddr(struct dt_device_node *n, int i, paddr_t *start, paddr_t *len)
{ (void)n; (void)i; *start=0xfe100000; *len=0x114; return fail_address; }
static void *ioremap_nocache(paddr_t address, int length)
{ assert(address==0xfe100000); assert(length==4096); return fail_map ? NULL : registers; }
static void writel(uint32_t value, void *address) { writes++; *(uint32_t *)address=value; }
static uint32_t readl(void *address) { return *(uint32_t *)address; }
static void mdelay(int millis) { assert(millis==1); }
"""
            suffix = r"""
int main(void) {
 const char *cases[]={"brcm,bcm2835-pm","brcm,bcm2711-pm","unrelated"};
 for (int i=0;i<3;i++) {
  wanted=cases[i]; writes=0; registers[0x1c/4]=0x30;
  rpi4_reset();
  assert(writes==(i<2 ? 2 : 0));
  if (i<2) { assert(registers[0x24/4]==0x5a00000a); assert(registers[0x1c/4]==0x5a000020); }
 }
 wanted="brcm,bcm2711-pm"; fail_address=1; writes=0; rpi4_reset(); assert(writes==0);
 fail_address=0; fail_map=1; rpi4_reset(); assert(writes==0);
 return 0;
}
"""
            code = root / 'reset-functions.c'
            code.write_text(prefix + body + suffix)
            compiled = subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', str(code), '-o', str(root/'reset-test')],
                                      text=True, capture_output=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            self.assertEqual(subprocess.run([str(root/'reset-test')]).returncode, 0)
            append = ROOT / 'platform/yocto/meta-zenova/recipes-extended/xen/xen_4.21.bbappend'
            self.assertIn(patch.name, append.read_text())
