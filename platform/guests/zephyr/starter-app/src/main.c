/* SPDX-License-Identifier: Apache-2.0 */
/* 기본 앱은 PV 콘솔 안내를 한 번 출력하고 사용자 앱 교체를 기다린다. */
#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>

int main(void)
{
	printk("Zenova / Zephyr / Xen\n"
	       "Starter App: edit, build and replace this firmware with your application.\n"
	       "PV console only; board compatibility and application readiness require testing.\n");
	k_sleep(K_FOREVER);
	return 0;
}
