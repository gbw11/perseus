/* SPDX-License-Identifier: Apache-2.0 */
/* Standard Zephyr MQTT protocol with deadline-bounded, nonblocking TCP I/O.
 * Native TCP transport's blocking connect/send loops do not bound quiesce. */
#include <zephyr/net/mqtt.h>
#include <zephyr/net/socket.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/fdtable.h>
#include <errno.h>
#include "socket_transport.h"

static int ready(struct xt_socket *s, short events)
{
	int64_t left=s->deadline_ms-k_uptime_get();
	if (left<=0) { return -ETIMEDOUT; }
	struct zsock_pollfd poll={.fd=s->fd,.events=events};
	int rc=zsock_poll(&poll,1,(int)left);
	if (rc<0) { return -errno; }
	if (!rc) { return -ETIMEDOUT; }
	if (poll.revents & (ZSOCK_POLLERR|ZSOCK_POLLHUP|ZSOCK_POLLNVAL)) { return -ECONNRESET; }
	return poll.revents & events ? 0 : -EIO;
}
int xt_socket_close(struct xt_socket *s)
{
	if (s->fd < 0) { return s->close_error; }
	int fd=s->fd;s->fd=-1;
	int rc=zsock_close(fd);
	if (rc<0 && errno!=EBADF) { s->close_error=-errno; }
	return s->close_error;
}

int mqtt_client_custom_transport_connect(struct mqtt_client *m)
{
	struct xt_socket *s=m->transport.custom_transport_data;
	if (s->close_error) { return s->close_error; }
	s->fd=zsock_socket(NET_AF_INET,NET_SOCK_STREAM,NET_IPPROTO_TCP);
	if (s->fd<0) { return -errno; }
	if (zsock_fcntl(s->fd,ZVFS_F_SETFL,ZVFS_O_NONBLOCK)<0) { return -errno; }
	int rc=zsock_connect(s->fd,m->broker,sizeof(struct net_sockaddr_in));
	if (rc<0 && errno!=EINPROGRESS && errno!=EALREADY) { return -errno; }
	if (rc<0) {
		rc=ready(s,ZSOCK_POLLOUT);if (rc) { return rc; }
		int error=0;net_socklen_t length=sizeof(error);
		if (zsock_getsockopt(s->fd,ZSOCK_SOL_SOCKET,ZSOCK_SO_ERROR,&error,&length)<0) { return -errno; }
		if (error) { return -error; }
	}
	return 0;
}

int mqtt_client_custom_transport_write(struct mqtt_client *m,const uint8_t *data,uint32_t length)
{
	struct xt_socket *s=m->transport.custom_transport_data;
	uint32_t offset=0;
	while (offset<length) {
		if (k_uptime_get()>=s->deadline_ms) { return -ETIMEDOUT; }
		int rc=zsock_send(s->fd,data+offset,length-offset,ZSOCK_MSG_DONTWAIT);
		if (rc>0) { offset+=(uint32_t)rc;continue; }
		if (!rc) { return -ECONNRESET; }
		if (errno==EINTR) { continue; }
		if (errno!=EAGAIN && errno!=EWOULDBLOCK) { return -errno; }
		rc=ready(s,ZSOCK_POLLOUT);if (rc) { return rc; }
	}
	return 0;
}

int mqtt_client_custom_transport_write_msg(struct mqtt_client *m,const struct net_msghdr *message)
{
	/* One overall deadline across every iovec, including partial sends. */
	for (size_t i=0;i<message->msg_iovlen;++i) {
		int rc=mqtt_client_custom_transport_write(m,message->msg_iov[i].iov_base,message->msg_iov[i].iov_len);
		if (rc) { return rc; }
	}
	return 0;
}

int mqtt_client_custom_transport_read(struct mqtt_client *m,uint8_t *data,uint32_t length,bool block)
{
	struct xt_socket *s=m->transport.custom_transport_data;
	/* v2 ACK decoding stays incremental. v1 payload reads may wait, but share
	 * the enclosing poll/publish deadline instead of an unbounded recv. */
	for (;;) {
		int rc=zsock_recv(s->fd,data,length,ZSOCK_MSG_DONTWAIT);
		if (rc>=0) { return rc; }
		if (!block || (errno!=EAGAIN && errno!=EWOULDBLOCK && errno!=EINTR)) { return -errno; }
		rc=ready(s,ZSOCK_POLLIN);if (rc) { return rc; }
	}
}

int mqtt_client_custom_transport_disconnect(struct mqtt_client *m)
{
	struct xt_socket *s=m->transport.custom_transport_data;
	return xt_socket_close(s);
}
