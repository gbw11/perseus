/* SPDX-License-Identifier: Apache-2.0 */
/**
 * XenStore ring client. One synchronous request at a time; watch notifications
 * are coalesced and callers reread values. All timeouts are monotonic budgets.
 */
#ifndef XENOVA_XENSTORE_H
#define XENOVA_XENSTORE_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <xenova/abi/xs_wire.h>

#define XS_PATH_LIMIT 256
#define XS_VALUE_LIMIT 2048
#define XS_WATCH_LIMIT 4
#define XS_PENDING_LIMIT 4
#define XS_TIMEOUT_LIMIT 120000
#define XS_RETRY_LIMIT 4

struct xs_io {
	int64_t (*now)(void *context);
	/* Latched notification: do not clear it between checking the ring and wait. */
	int (*wait)(void *context, int64_t deadline);
	int (*notify)(void *context);
	void *context;
};

struct xs_watch_slot {
	uint32_t id;
	unsigned int notifications;
	bool initial;
	enum { XS_W_FREE, XS_W_REGISTERING, XS_W_ACTIVE, XS_W_UNCERTAIN,
	       XS_W_CLOSING } state;
	char path[XS_PATH_LIMIT];
	char token[16];
};

struct xs_client {
	struct xenstore_domain_interface *ring;
	struct xs_io io;
	uint32_t next_request, next_watch, active_tx, abandoned_start;
	uint32_t late_responses, sending_request;
	uint32_t pending[XS_PENDING_LIMIT];
	bool busy, poisoned, closed, orphan_tx;
	int fatal_error;
	char last_error[32];
	struct xsd_sockmsg rx_header;
	size_t header_bytes, body_bytes;
	char rx_body[XENSTORE_PAYLOAD_MAX + 1];
	struct xs_watch_slot watches[XS_WATCH_LIMIT];
};

struct xs_watch_event {
	bool initial;
	unsigned int notifications;
};

int xs_init(struct xs_client *client, struct xenstore_domain_interface *ring,
	    const struct xs_io *io);
int64_t xs_deadline(struct xs_client *client, int timeout_ms);
int xs_remaining(struct xs_client *client, int64_t deadline);
int xs_read(struct xs_client *, uint32_t tx, const char *path,
	    void *value, size_t capacity, size_t *length, int timeout_ms);
int xs_read_text(struct xs_client *, uint32_t tx, const char *path,
		 char *value, size_t capacity, int timeout_ms);
/* directory/get_perms return NUL-separated strings and an explicit byte count. */
int xs_directory(struct xs_client *, uint32_t tx, const char *path,
		 char *entries, size_t capacity, size_t *length, int timeout_ms);
int xs_get_perms(struct xs_client *, const char *path,
		 char *entries, size_t capacity, size_t *length, int timeout_ms);
int xs_write(struct xs_client *, uint32_t tx, const char *path,
	     const void *value, size_t length, int timeout_ms);
int xs_mkdir(struct xs_client *, const char *path, int timeout_ms);
int xs_remove(struct xs_client *, const char *path, int timeout_ms);
/* A new start may drain an abandoned start and abort its orphan within this budget. */
int xs_transaction_start(struct xs_client *, uint32_t *tx, int timeout_ms);
int xs_transaction_end(struct xs_client *, uint32_t tx, bool commit, int timeout_ms);
typedef int (*xs_transaction_fn)(struct xs_client *, uint32_t tx,
				int64_t deadline, void *context);
/* Retry only a definitive commit EAGAIN. The callback must be replayable. */
int xs_transaction(struct xs_client *, unsigned int attempts, int timeout_ms,
		   xs_transaction_fn, void *context, unsigned int *used);
int xs_watch(struct xs_client *, const char *path, uint32_t *watch, int timeout_ms);
int xs_unwatch(struct xs_client *, uint32_t watch, int timeout_ms);
int xs_watch_next(struct xs_client *, uint32_t watch,
		  struct xs_watch_event *event, int timeout_ms);
/* Bounded best effort; a negative return means server cleanup is unconfirmed. */
int xs_close(struct xs_client *, int timeout_ms);
#endif
