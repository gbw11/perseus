/* SPDX-License-Identifier: Apache-2.0 */
#ifndef XENOVA_MQTT_SOCKET_H
#define XENOVA_MQTT_SOCKET_H
#include <zephyr/net/socket.h>
struct xt_socket {
	int fd;
	int64_t deadline_ms;
	/* An ambiguous close is quarantined, never retried on a recycled fd. */
	int close_error;
};
int xt_socket_close(struct xt_socket *);
#endif
