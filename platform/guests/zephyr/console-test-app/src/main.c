/* SPDX-License-Identifier: Apache-2.0 */
/* QEMU console acceptance app: no driving, networking or failover commands. */
#include <string.h>
#include <zephyr/device.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>

#define LINE_BYTES 128

static void prompt(void)
{
	printk("mo04> ");
}

static void command(const char *line)
{
	if (strcmp(line, "ping") == 0) {
		printk("pong\n");
	} else if (strncmp(line, "ping ", 5) == 0) {
		printk("pong %s\n", line + 5);
	} else if (strncmp(line, "echo ", 5) == 0) {
		printk("echo: %s\n", line + 5);
	} else if (strcmp(line, "info") == 0) {
		printk("MO04_CONSOLE_TEST 1.0.0; Xen PV; test firmware only\n");
	} else if (strcmp(line, "help") == 0) {
		printk("ping [token] | echo <text> | info | help\n");
	} else if (line[0] != '\0') {
		printk("ERR unknown-command\n");
	}
	prompt();
}

int main(void)
{
	const struct device *console = DEVICE_DT_GET(DT_CHOSEN(zephyr_console));
	char line[LINE_BYTES];
	size_t length = 0;
	bool overflow = false;
	bool after_cr = false;

	if (!device_is_ready(console)) {
		printk("MO04_CONSOLE_TEST ERROR console-not-ready\n");
		return 1;
	}
	printk("MO04_CONSOLE_TEST 1.0.0; ping/echo only; no vehicle control\n");
	prompt();
	for (;;) {
		unsigned char c;

		if (uart_poll_in(console, &c) != 0) {
			k_msleep(10);
			continue;
		}
		if (c == '\n' && after_cr) {
			after_cr = false;
			continue;
		}
		after_cr = c == '\r';
		if (c == '\r' || c == '\n') {
			printk("\n");
			line[length] = '\0';
			if (overflow) {
				printk("ERR line-too-long\n");
				prompt();
			} else {
				command(line);
			}
			length = 0;
			overflow = false;
		} else if (c == 3) {
			length = 0;
			overflow = false;
			printk("^C\n");
			prompt();
		} else if (!overflow && (c == '\b' || c == 127)) {
			if (length > 0) {
				length--;
			}
		} else if (!overflow && c >= 32 && c != 127) {
			if (length < sizeof(line) - 1) {
				line[length++] = (char)c;
			} else {
				/* Discard the entire overlong line, never execute its suffix. */
				overflow = true;
			}
		}
	}
}
