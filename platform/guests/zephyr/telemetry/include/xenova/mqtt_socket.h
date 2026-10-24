/* SPDX-License-Identifier: Apache-2.0 */
#ifndef XENOVA_MQTT_SOCKET_H
#define XENOVA_MQTT_SOCKET_H
#include <zephyr/net/socket.h>
struct xt_socket {
	int fd;
	int64_t deadline_ms;
};
#endif
