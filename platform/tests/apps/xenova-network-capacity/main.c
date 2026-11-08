/* Local SDK test only: real connection registry/DHCP init, no MQTT or packets. */
#include <errno.h>
#include <zephyr/device.h>
#include <zephyr/kernel.h>
#include <zephyr/net/dummy.h>
#include <zephyr/net/net_if.h>
#include <zephyr/net/net_ip.h>
#include <zephyr/ztest.h>
#include "connection.h"

static unsigned char mac[6] = {0x00, 0x00, 0x5e, 0x00, 0x53, 0x01};
static int profile_device_init(const struct device *dev)
{
    ARG_UNUSED(dev);
    return 0;
}
static void iface_init(struct net_if *iface)
{
    net_if_set_link_addr(iface, mac, sizeof(mac), NET_LINK_ETHERNET);
}
static int dummy_send(const struct device *dev, struct net_pkt *pkt)
{
    ARG_UNUSED(dev);
    ARG_UNUSED(pkt);
    return -ENOTSUP;
}
static struct dummy_api api = {.iface_api.init = iface_init, .send = dummy_send};
NET_DEVICE_INIT(conn_profile, "conn_profile", profile_device_init, NULL, NULL, NULL,
    CONFIG_KERNEL_INIT_PRIORITY_DEFAULT, &api, DUMMY_L2, NET_L2_GET_CTX_TYPE(DUMMY_L2), 127);

struct counts { unsigned total, tcp, udp, dhcp; };
static void count_connection(struct net_conn *conn, void *user_data)
{
    struct counts *counts = user_data;
    counts->total++;
    counts->tcp += conn->proto == NET_IPPROTO_TCP;
    counts->udp += conn->proto == NET_IPPROTO_UDP;
    counts->dhcp += conn->proto == NET_IPPROTO_UDP &&
        net_ntohs(net_sin(&conn->local_addr)->sin_port) == 68;
}
static struct counts counts(void)
{
    struct counts value = {0};
    net_conn_foreach(count_connection, &value);
    return value;
}
static enum net_verdict callback(struct net_conn *conn, struct net_pkt *pkt,
    union net_ip_header *ip, union net_proto_header *proto, void *user_data)
{
    ARG_UNUSED(conn); ARG_UNUSED(pkt); ARG_UNUSED(ip); ARG_UNUSED(proto); ARG_UNUSED(user_data);
    return NET_DROP;
}
static int register_tcp(unsigned port, struct net_conn_handle **handle)
{
    return net_conn_register(NET_IPPROTO_TCP, NET_SOCK_STREAM, NET_AF_INET,
        NULL, NULL, 18885, port, NULL, callback, NULL, handle);
}

ZTEST(conn_profile, test_dhcp_reserved_tcp_slots_reuse)
{
    struct counts initial = counts();
    zassert_equal(initial.total, 1, "DHCP init must reserve exactly one handler");
    zassert_equal(initial.udp, 1);
    zassert_equal(initial.dhcp, 1);
    zassert_equal(initial.tcp, 0);
    unsigned expected = CONFIG_NET_MAX_CONN - 1;
    zassert_true(expected == 3 || expected == 4 || expected == 6);
    unsigned attempts = expected > 4 ? expected : 4;
    for (unsigned round = 0; round < 20; round++) {
        struct net_conn_handle *handles[6] = {0};
        unsigned accepted = 0;
        for (unsigned i = 0; i < attempts; i++) {
            int rc = register_tcp(12000 + i, &handles[i]);
            if (i < expected) {
                zassert_equal(rc, 0, "valid TCP registration must progress");
                accepted++;
            } else {
                zassert_equal(rc, -ENOENT, "pool exhaustion must be exact ENOENT");
                zassert_is_null(handles[i]);
            }
        }
        struct counts full = counts();
        zassert_equal(accepted, expected);
        zassert_equal(full.total, CONFIG_NET_MAX_CONN);
        zassert_equal(full.tcp, expected);
        zassert_equal(full.dhcp, 1);
        struct net_conn_handle *overflow = NULL;
        zassert_equal(register_tcp(13000, &overflow), -ENOENT);
        zassert_is_null(overflow);
        zassert_equal(net_conn_unregister(handles[0]), 0);
        handles[0] = NULL;
        zassert_equal(counts().total, CONFIG_NET_MAX_CONN - 1);
        zassert_equal(register_tcp(14000, &handles[0]), 0,
            "released slot must accept the next valid TCP registration");
        zassert_equal(counts().total, CONFIG_NET_MAX_CONN);
        for (unsigned i = 0; i < accepted; i++) {
            zassert_equal(net_conn_unregister(handles[i]), 0);
        }
        zassert_equal(counts().total, 1);
        zassert_equal(counts().dhcp, 1);
    }
    printk("CONN_PROFILE pool=%u dhcp=1 tcp_capacity=%u rounds=20 overflow=-%d refill=0 final=1\n",
        CONFIG_NET_MAX_CONN, expected, ENOENT);
}
ZTEST_SUITE(conn_profile, NULL, NULL, NULL, NULL, NULL);
