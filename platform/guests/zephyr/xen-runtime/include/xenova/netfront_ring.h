/* SPDX-License-Identifier: Apache-2.0 */
/* Fixed Xen netif ABI subset, checked against references/{netif,ring}.h.
 * Only single-page frames and RX-copy are supported. No offload/extra slots. */
#ifndef XENOVA_NETFRONT_RING_H
#define XENOVA_NETFRONT_RING_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#define XNF_PAGE 4096
#define XNF_SLOTS 8
#define XNF_RING_SIZE 256
#define XNF_FRAME_MAX 1514
struct xnf_tx_request { uint32_t gref; uint16_t offset, flags, id, size; };
struct xnf_tx_response { uint16_t id; int16_t status; };
struct xnf_rx_request { uint16_t id, pad; uint32_t gref; };
struct xnf_rx_response { uint16_t id, offset, flags; int16_t status; };
struct xnf_header { volatile uint32_t req_prod, req_event, rsp_prod, rsp_event; uint8_t pad[48]; };
union xnf_tx_entry { struct xnf_tx_request req; struct xnf_tx_response rsp; };
union xnf_rx_entry { struct xnf_rx_request req; struct xnf_rx_response rsp; };
struct xnf_tx_ring { struct xnf_header h; union xnf_tx_entry ring[XNF_RING_SIZE]; };
struct xnf_rx_ring { struct xnf_header h; union xnf_rx_entry ring[XNF_RING_SIZE]; };
struct xnf_stats { uint32_t tx, rx, tx_drop, rx_drop, pool_busy, protocol_errors; };
struct xnf {
 struct xnf_tx_ring *tx;
 struct xnf_rx_ring *rx;
 uint8_t (*tx_pages)[XNF_PAGE], (*rx_pages)[XNF_PAGE];
 uint32_t tx_grants[XNF_SLOTS], rx_grants[XNF_SLOTS];
 uint32_t tx_prod, tx_cons, rx_prod, rx_cons;
 uint16_t tx_id[XNF_SLOTS], rx_id[XNF_SLOTS];
 bool tx_busy[XNF_SLOTS], rx_posted[XNF_SLOTS], failed;
 struct xnf_stats stats;
 void (*notify)(void *);
 int (*deliver)(void *, const uint8_t *, size_t);
 void *context;
};
void xnf_init(struct xnf *);
int xnf_send(struct xnf *, const void *, size_t);
int xnf_poll(struct xnf *);
/* Under the ring owner's lock, after a successful bounded poll. */
bool xnf_pending(const struct xnf *);
bool xnf_drained(const struct xnf *);
#endif
