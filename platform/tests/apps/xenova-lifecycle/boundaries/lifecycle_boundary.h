/* Native-only external device/transport boundary; kernel threads/locks are real. */
#ifndef XL_BOUNDARY_H
#define XL_BOUNDARY_H
#include <zephyr/kernel.h>
#include <stdatomic.h>
extern atomic_uint xl_polls;
extern atomic_uint xl_tx_done;
extern atomic_int xl_poll_error;
extern atomic_bool xl_wait_enabled;
extern struct k_sem xl_store_wait;
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#define NET_AF_UNSPEC 0
#define NET_IF_NO_AUTO_START 1
#define NET_LINK_ETHERNET 1
struct device;
struct net_if { int unused; };
struct net_pkt { size_t length; uint8_t bytes[1500]; };
enum ethernet_hw_caps { XL_ETH_NONE };
struct ethernet_api {
 struct { void (*init)(struct net_if *); } iface_api;
 enum ethernet_hw_caps (*get_capabilities)(const struct device *);
 int (*send)(const struct device *,struct net_pkt *);
};
extern const struct ethernet_api *xl_api;
#define ETH_NET_DEVICE_INIT(n,label,fn,pm,data,config,priority,ether,mtu) \
 void xl_net_init(void) { static struct net_if i; (void)fn(NULL); xl_api=ether; (ether)->iface_api.init(&i); }
void xl_net_init(void);
void net_if_flag_set(struct net_if *,int);
void ethernet_init(struct net_if *);
void net_if_carrier_off(struct net_if *);
void net_eth_carrier_off(struct net_if *);
void net_eth_carrier_on(struct net_if *);
int net_if_up(struct net_if *);
int net_if_down(struct net_if *);
void net_if_set_link_addr(struct net_if *,const uint8_t *,size_t,int);
void net_dhcpv4_start(struct net_if *);
void net_dhcpv4_stop(struct net_if *);
struct net_pkt *net_pkt_rx_alloc_with_buffer(struct net_if *,size_t,int,int,k_timeout_t);
int net_pkt_write(struct net_pkt *,const void *,size_t);
int net_recv_data(struct net_if *,struct net_pkt *);
void net_pkt_unref(struct net_pkt *);
size_t net_pkt_get_len(struct net_pkt *);
int net_pkt_read(struct net_pkt *,void *,size_t);
#define EVTCHN_2L_NR_CHANNELS 1024
#define EVTCHNOP_unmask 1
#define SCHEDOP_shutdown 2
#define SHUTDOWN_poweroff 0
#define SHUTDOWN_reboot 1
struct evtchn_unmask { unsigned port; };
struct sched_shutdown { unsigned reason; };
int alloc_unbound_event_channel(unsigned);
void mask_event_channel(unsigned);
int bind_event_channel(unsigned,void (*)(void *),void *);
void unbind_event_channel(unsigned);
int evtchn_close(unsigned);
int notify_evtchn(unsigned);
int HYPERVISOR_event_channel_op(unsigned,void *);
int HYPERVISOR_sched_op(unsigned,void *);
extern const char *xl_case;
extern unsigned xl_hypercalls,xl_grants,xl_revokes,xl_opened,xl_closed,xl_features;
extern bool xl_retry,xl_race;
void xl_complete_tx(void);
void xl_corrupt_tx(void);
#if defined(XL_PACKET_LOCK_TEST)
extern atomic_int xl_packet_lock_error;
#endif
extern struct k_sem xl_acquired,xl_release;
#endif
