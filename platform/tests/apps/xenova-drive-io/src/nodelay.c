/* Real v1 connect/retry and Zephyr TCP options; only the MQTT peer is replaced. */
#include "xm_zclient.h"
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>

static struct xm_zclient client;
static unsigned connections, options, closes, writes;
static int connect_error;
static bool option_failure;

static int socket_fd(void)
{
#if defined(CONFIG_XENOVA_MQTT_BOUNDED_IO)
	return client.socket_io.fd;
#else
	return client.mqtt.transport.tcp.sock;
#endif
}

static void set_socket_fd(int fd)
{
#if defined(CONFIG_XENOVA_MQTT_BOUNDED_IO)
	client.socket_io.fd = fd;
#else
	client.mqtt.transport.tcp.sock = fd;
#endif
}

static int nodelay(void)
{
	int value = -1;
	net_socklen_t length = sizeof(value);
	assert(!zsock_getsockopt(socket_fd(), NET_IPPROTO_TCP, ZSOCK_TCP_NODELAY,
				 &value, &length));
	assert(length == sizeof(value));
	return value;
}

int __wrap_mqtt_connect(struct mqtt_client *mqtt)
{
	assert(mqtt == &client.mqtt);
	connections++;
	if (connect_error) { return connect_error; }
	set_socket_fd(zsock_socket(NET_AF_INET, NET_SOCK_STREAM, NET_IPPROTO_TCP));
	assert(socket_fd() >= 0 && nodelay() == 0);
	return 0;
}

int __real_z_impl_zsock_setsockopt(int, int, int, const void *, net_socklen_t);
int __wrap_z_impl_zsock_setsockopt(int fd, int level, int name, const void *value,
			   net_socklen_t length)
{
	assert(fd == socket_fd() && level == NET_IPPROTO_TCP && name == ZSOCK_TCP_NODELAY);
	assert(length == sizeof(int) && *(const int *)value == 1);
	assert(!client.socket_open && !client.connected);
	options++;
	if (option_failure) { errno = ENOPROTOOPT; return -1; }
	return __real_z_impl_zsock_setsockopt(fd, level, name, value, length);
}

int __wrap_mqtt_abort(struct mqtt_client *mqtt)
{
	assert(mqtt == &client.mqtt);
	if (socket_fd() >= 0) {
		assert(!zsock_close(socket_fd()));
		set_socket_fd(-1);
		closes++;
	}
	return 0;
}

int __wrap_mqtt_live(struct mqtt_client *mqtt)
{
	assert(mqtt == &client.mqtt);
	return -EAGAIN;
}

int __wrap_mqtt_publish(struct mqtt_client *mqtt, const struct mqtt_publish_param *message)
{
	assert(mqtt == &client.mqtt && message->message.payload.len > 0 && nodelay() == 1);
	writes++;
	return 0;
}

int main(void)
{
	struct xm_zconfig config = {.host="192.0.2.1", .port=1883,
		.boot_id="11111111-1111-4111-8111-111111111111",
		.instance_id="22222222-2222-4222-8222-222222222222"};
	assert(!xm_zclient_init(&client, "33333333-3333-4333-8333-333333333333",
				NULL, "manual", &config));
	set_socket_fd(-1);
	for (unsigned attempt = 1; attempt <= 2; attempt++) {
		client.retry_at_ms = 0; /* Skip the existing backoff only in this test. */
		assert(!xm_zclient_poll(&client, 0));
		assert(connections == attempt && options == attempt && client.socket_open);
		assert(nodelay() == 1);
		client.connected = true; /* The peer's successful CONNACK boundary. */
		assert(!xm_zclient_publish(&client, "xenova/v1/apps/manual/events", "drive.command",
			"{\"generation\":1,\"v_mps\":1,\"steer_rad\":0}"));
		xm_zclient_close(&client);
		assert(socket_fd() == -1 && closes == attempt);
	}
	assert(writes == 2);
	client.retry_at_ms = 0;
	option_failure = true;
	assert(xm_zclient_poll(&client, 0) == -ENOTCONN);
	assert(connections == 3 && options == 3 && closes == 3 && writes == 2);
	assert(!client.socket_open && !client.connected && socket_fd() == -1);
	assert(client.last_io_rc == -ENOPROTOOPT && client.retry_at_ms > k_uptime_get());
	assert(xm_zclient_poll(&client, 0) == -ENOTCONN && connections == 3);
	option_failure = false;
	client.retry_at_ms = 0;
	assert(!xm_zclient_poll(&client, 0) && nodelay() == 1);
	xm_zclient_close(&client);
	assert(connections == 4 && options == 4 && closes == 4);
	client.retry_at_ms = 0;
	connect_error = -ECONNREFUSED;
	assert(xm_zclient_poll(&client, 0) == -ENOTCONN);
	assert(connections == 5 && options == 4 && closes == 4 && writes == 2);
	assert(client.last_io_rc == -ECONNREFUSED && !client.socket_open);
	puts("PASS real TCP_NODELAY=1 on initial/reconnected v1 sockets; option failure closes and retries");
	exit(0);
}
