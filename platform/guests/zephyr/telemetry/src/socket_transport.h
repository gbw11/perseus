/* SPDX-License-Identifier: Apache-2.0 */
#ifndef XT_SOCKET_TRANSPORT_H
#define XT_SOCKET_TRANSPORT_H
#include <zephyr/net/socket.h>
struct xt_socket {
	int fd;
	int64_t deadline_ms;
};
#endif
