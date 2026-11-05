/* SPDX-License-Identifier: Apache-2.0 */
/* Wire framing, response correlation and XenStore resource ownership. */
#include <errno.h>
#include <xenova/xenstore.h>
#include <limits.h>
#include <stdio.h>
#include <string.h>

#if __BYTE_ORDER__ != __ORDER_LITTLE_ENDIAN__
#error "The frozen ARM64 XenStore wire endpoint is little endian"
#endif
_Static_assert(sizeof(struct xsd_sockmsg) == 16, "XenStore header ABI");
_Static_assert(offsetof(struct xenstore_domain_interface, req_cons) == 2048,
	       "XenStore shared page ABI");
_Static_assert(sizeof(struct xenstore_domain_interface) <= 4096, "Store page size");

static int fatal(struct xs_client *c, int rc)
{
	c->poisoned = true;
	c->fatal_error = rc;
	return rc;
}

static int path_length(const char *path)
{
	if (!path) { return -EINVAL; }
	size_t n = strnlen(path, XS_PATH_LIMIT);
	if (!n || n == XS_PATH_LIMIT) { return -EINVAL; }
	const char *p = path;
	if (*p == '/') { p++; }
	while (*p) {
		const char *end = strchr(p, '/');
		size_t len = end ? (size_t)(end - p) : strlen(p);
		if (!len || (len == 1 && p[0] == '.') ||
		    (len == 2 && p[0] == '.' && p[1] == '.')) { return -EINVAL; }
		for (size_t i = 0; i < len; i++) {
			unsigned char ch = (unsigned char)p[i];
			if (ch < 0x21 || ch > 0x7e) { return -EINVAL; }
		}
		if (!end) { break; }
		p = end + 1;
		if (!*p) { return -EINVAL; }
	}
	return (int)n + 1;
}

int64_t xs_deadline(struct xs_client *c, int ms)
{
	if (!c || !c->io.now || ms < 0 || ms > XS_TIMEOUT_LIMIT) { return -1; }
	int64_t now = c->io.now(c->io.context);
	if (now < 0 || now > INT64_MAX - ms) { return -1; }
	return now + ms;
}

int xs_remaining(struct xs_client *c, int64_t end)
{
	int64_t now = c->io.now(c->io.context);
	return now >= end ? 0 : (int)(end - now);
}

int xs_init(struct xs_client *c, struct xenstore_domain_interface *ring,
	    const struct xs_io *io)
{
	if (!c || !ring || !io || !io->now || !io->wait || !io->notify ||
	    (uintptr_t)ring % _Alignof(struct xenstore_domain_interface)) { return -EINVAL; }
	memset(c, 0, sizeof(*c));
	c->ring = ring;
	c->io = *io;
	return 0;
}

static int enter(struct xs_client *c)
{
	if (!c || !c->ring || c->closed) { return -ENOTCONN; }
	bool expected = false;
	if (!__atomic_compare_exchange_n(&c->busy, &expected, true, false,
					__ATOMIC_ACQUIRE, __ATOMIC_RELAXED)) { return -EBUSY; }
	if (c->poisoned) {
		__atomic_store_n(&c->busy, false, __ATOMIC_RELEASE);
		return c->fatal_error;
	}
	return 0;
}

static int leave(struct xs_client *c, int rc)
{
	__atomic_store_n(&c->busy, false, __ATOMIC_RELEASE);
	return rc;
}

static int connection(struct xs_client *c)
{
	struct xenstore_domain_interface *r = c->ring;
	uint32_t feature = __atomic_load_n(&r->server_features, __ATOMIC_ACQUIRE);
	if (__atomic_load_n(&r->connection, __ATOMIC_ACQUIRE) != XENSTORE_CONNECTED) {
		return fatal(c, -ECONNRESET);
	}
	if ((feature & XENSTORE_SERVER_FEATURE_ERROR) &&
	    __atomic_load_n(&r->error, __ATOMIC_ACQUIRE)) { return fatal(c, -EIO); }
	return 0;
}

static int publish_progress(struct xs_client *c, uint32_t *index, uint32_t value)
{
	__atomic_store_n(index, value, __ATOMIC_RELEASE);
	__atomic_thread_fence(__ATOMIC_SEQ_CST);
	int rc = c->io.notify(c->io.context);
	return rc ? fatal(c, rc) : 0;
}

/* Acquire the peer's index before bytes; release our index after bytes.
 * Notifications are sent after publishing either producer or consumer progress.
 */
static int ring_transfer(struct xs_client *c, bool write, void *data, size_t count)
{
	struct xenstore_domain_interface *r = c->ring;
	int rc = connection(c);
	if (rc) { return rc; }
	uint32_t prod = __atomic_load_n(write ? &r->req_prod : &r->rsp_prod,
					__ATOMIC_ACQUIRE);
	uint32_t cons = __atomic_load_n(write ? &r->req_cons : &r->rsp_cons,
					__ATOMIC_ACQUIRE);
	uint32_t used = prod - cons;
	if (used > XENSTORE_RING_SIZE) { return fatal(c, -EPROTO); }
	size_t available = write ? XENSTORE_RING_SIZE - used : used;
	if (count > available) { count = available; }
	char *bytes = data, *ring = write ? r->req : r->rsp;
	uint32_t index = write ? prod : cons;
	for (size_t i = 0; i < count; i++) {
		if (write) { ring[MASK_XENSTORE_IDX(index + i)] = bytes[i]; }
		else { bytes[i] = ring[MASK_XENSTORE_IDX(index + i)]; }
	}
	if (count) {
		rc = publish_progress(c, write ? &r->req_prod : &r->rsp_cons, index + (uint32_t)count);
		if (rc) { return rc; }
	}
	return (int)count;
}

static int wait_until(struct xs_client *c, int64_t end)
{
	__atomic_thread_fence(__ATOMIC_SEQ_CST);
	if (!xs_remaining(c, end)) { return 0; }
	int rc = c->io.wait(c->io.context, end);
	/* The caller always rechecks the ring, including on the last wakeup. */
	return rc == -ETIMEDOUT ? 0 : rc;
}

/* A partial header/body is retained across waits and across request timeouts. */
static int receive(struct xs_client *c)
{
	int n;
	if (c->header_bytes < sizeof(c->rx_header)) {
		n = ring_transfer(c, false, (char *)&c->rx_header + c->header_bytes,
				  sizeof(c->rx_header) - c->header_bytes);
		if (n < 0) { return n; }
		c->header_bytes += (size_t)n;
		if (c->header_bytes < sizeof(c->rx_header)) { return 0; }
		if (c->rx_header.len > XENSTORE_PAYLOAD_MAX) { return fatal(c, -EMSGSIZE); }
	}
	if (c->body_bytes < c->rx_header.len) {
		n = ring_transfer(c, false, c->rx_body + c->body_bytes,
				  c->rx_header.len - c->body_bytes);
		if (n < 0) { return n; }
		c->body_bytes += (size_t)n;
		if (c->body_bytes < c->rx_header.len) { return 0; }
	}
	c->rx_body[c->rx_header.len] = '\0';
	return 1;
}

static struct xs_watch_slot *watch_slot(struct xs_client *c, uint32_t id)
{
	for (unsigned int i = 0; i < XS_WATCH_LIMIT; i++) {
		if (c->watches[i].state != XS_W_FREE && c->watches[i].id == id) {
			return &c->watches[i];
		}
	}
	return NULL;
}

static int watch_message(struct xs_client *c)
{
	size_t len = c->rx_header.len;
	const char *nul = memchr(c->rx_body, 0, len);
	if (!nul || nul == c->rx_body || nul == c->rx_body + len - 1) {
		return fatal(c, -EPROTO);
	}
	const char *token = nul + 1;
	size_t rest = len - (size_t)(token - c->rx_body);
	if (strnlen(token, rest) != rest - 1 || path_length(c->rx_body) < 0) {
		return fatal(c, -EPROTO);
	}
	for (unsigned int i = 0; i < XS_WATCH_LIMIT; i++) {
		struct xs_watch_slot *w = &c->watches[i];
		if (w->state != XS_W_FREE && !strcmp(w->token, token)) {
			size_t n = strlen(w->path);
			if (strncmp(c->rx_body, w->path, n) ||
			    (n != 1 && c->rx_body[n] && c->rx_body[n] != '/')) { return fatal(c, -EPROTO); }
			if (w->state == XS_W_ACTIVE || w->state == XS_W_REGISTERING) {
				if (w->notifications != UINT_MAX) { w->notifications++; }
			}
			break;
		}
	}
	return 0;
}

static int error_reply(struct xs_client *c)
{
	size_t n = c->rx_header.len;
	if (!n || n >= sizeof(c->last_error) || c->rx_body[n-1] ||
	    strnlen(c->rx_body, n) != n-1) { return fatal(c, -EPROTO); }
	memcpy(c->last_error, c->rx_body, n);
	for (size_t i = 0; i < sizeof(xsd_errors)/sizeof(xsd_errors[0]); i++) {
		if (!strcmp(c->last_error, xsd_errors[i].errstring)) {
			return -xsd_errors[i].errnum;
		}
	}
	return -EIO;
}

static int parse_number(const char *text, uint32_t *value)
{
	if (!text || !*text) { return -EPROTO; }
	uint32_t n = 0;
	for (; *text; text++) {
		if (*text < '0' || *text > '9' || n > (UINT32_MAX - (*text-'0')) / 10) {
			return -EPROTO;
		}
		n = n * 10 + (*text - '0');
	}
	if (!n) { return -EPROTO; }
	*value = n;
	return 0;
}

static bool pending_requests(struct xs_client *c)
{
	for (unsigned int i = 0; i < XS_PENDING_LIMIT; i++) {
		if (c->pending[i]) { return true; }
	}
	return false;
}

/* ready is true only for this request, never for watches or delayed replies. */
static int pump(struct xs_client *c, uint32_t request, uint32_t type,
		uint32_t tx, void *output, size_t capacity, size_t *length, bool *ready)
{
	int rc = receive(c);
	if (rc <= 0) { return rc; }
	struct xsd_sockmsg h = c->rx_header;
	if (h.type != XS_WATCH_EVENT && h.req_id == c->sending_request && h.req_id) {
		return fatal(c, -EPROTO);
	}
	if (h.type != XS_WATCH_EVENT) {
		for (unsigned int i = 0; i < XS_PENDING_LIMIT; i++) {
			if (c->pending[i] == h.req_id) { c->pending[i] = 0; }
		}
	}
	if (h.type == XS_WATCH_EVENT) {
		rc = watch_message(c);
	} else if (!h.req_id || h.req_id > c->next_request) {
		rc = fatal(c, -EPROTO);
	} else if (h.req_id != request) {
		c->late_responses++;
		if (h.req_id == c->abandoned_start) {
			if (h.tx_id || c->active_tx) { rc = fatal(c, -EPROTO); }
			else if (h.type == XS_TRANSACTION_START) {
				uint32_t orphan;
				size_t n = h.len;
				if (n && c->rx_body[n-1] == 0) { n--; }
				rc = memchr(c->rx_body, 0, n) ? -EPROTO : parse_number(c->rx_body, &orphan);
				if (rc) { fatal(c, rc); }
				if (!rc) { c->active_tx = orphan; c->orphan_tx = true; }
			} else if (h.type != XS_ERROR) { rc = fatal(c, -EPROTO); }
			c->abandoned_start = 0;
		}
	} else {
		*ready = true;
		if (h.tx_id != tx) { rc = fatal(c, -EPROTO); }
		else if (h.type == XS_ERROR) { rc = error_reply(c); }
		else if (h.type != type) { rc = fatal(c, -EPROTO); }
		else if (h.len > capacity) { rc = -ENOBUFS; }
		else {
			if (h.len) { memcpy(output, c->rx_body, h.len); }
			if (length) { *length = h.len; }
			rc = 0;
		}
	}
	c->header_bytes = c->body_bytes = 0;
	return rc ? rc : 1;
}

/* A frame that fits in the request ring must never expose a partial header or
 * body. The session owner is the only producer, and the peer only frees space. */
static int try_request_frame(struct xs_client *c, const struct xsd_sockmsg *header,
			     const void *payload, int64_t end)
{
	struct xenstore_domain_interface *r = c->ring;
	int rc = connection(c);
	if (rc) { return rc; }
	uint32_t prod = __atomic_load_n(&r->req_prod, __ATOMIC_ACQUIRE);
	uint32_t used = prod - __atomic_load_n(&r->req_cons, __ATOMIC_ACQUIRE);
	if (used > XENSTORE_RING_SIZE) { return fatal(c, -EPROTO); }
	const size_t total = sizeof(*header) + header->len;
	if (total > XENSTORE_RING_SIZE - used) { return 0; }
	const char *bytes = (const char *)header;
	for (size_t i = 0; i < sizeof(*header); i++) {
		r->req[MASK_XENSTORE_IDX(prod + i)] = bytes[i];
	}
	bytes = payload;
	for (size_t i = 0; i < header->len; i++) {
		r->req[MASK_XENSTORE_IDX(prod + sizeof(*header) + i)] = bytes[i];
	}
	if (!xs_remaining(c, end)) { return -ETIMEDOUT; }
	c->sending_request = 0;
	rc = publish_progress(c, &r->req_prod, prod + (uint32_t)total);
	return rc ? rc : (int)total;
}

static int send_request_frame(struct xs_client *c, const struct xsd_sockmsg *header,
			      const void *payload, int64_t end)
{
	for (;;) {
		/* Correlate any buffered late response or invalid early reply before
		 * the request becomes visible to the server. */
		bool ignored = false;
		int progress = pump(c, 0, 0, 0, NULL, 0, NULL, &ignored);
		if (progress < 0) { return progress; }
		int rc = try_request_frame(c, header, payload, end);
		if (rc) { return rc < 0 ? rc : 0; }
		if (!xs_remaining(c, end)) { return -ETIMEDOUT; }
		if (progress) { continue; }
		rc = wait_until(c, end);
		if (rc < 0) { return rc; }
	}
}

static int request_held(struct xs_client *c, uint32_t type, uint32_t tx,
		   const void *payload, size_t length, void *output,
		   size_t capacity, size_t *output_length, int ms)
{
	if ((length && !payload) || (capacity && !output) ||
	    length > XENSTORE_PAYLOAD_MAX || capacity > XENSTORE_PAYLOAD_MAX) { return -EINVAL; }
	if (!c) { return -EINVAL; }
	int64_t end = xs_deadline(c, ms);
	if (end < 0) { return -EINVAL; }
	int rc;
	if (tx && (tx != c->active_tx || (c->orphan_tx && type != XS_TRANSACTION_END))) {
		return -EINVAL;
	}
	if (c->next_request == UINT32_MAX) { return -EOVERFLOW; }
	/* An enclosing deadline can reach zero between Store calls. */
	if (!ms) { return -ETIMEDOUT; }
	unsigned int slot;
	for (;;) {
		for (slot = 0; slot < XS_PENDING_LIMIT; slot++) {
			if (!c->pending[slot]) { break; }
		}
		if (slot < XS_PENDING_LIMIT) { break; }
		bool ignored = false;
		rc = pump(c, 0, 0, 0, NULL, 0, NULL, &ignored);
		if (rc < 0) { return rc; }
		if (!rc || !xs_remaining(c, end)) { return -EBUSY; }
	}
	struct xsd_sockmsg h = {type, ++c->next_request, tx, (uint32_t)length};
	c->pending[slot] = h.req_id;
	c->sending_request = h.req_id;
	size_t sent = 0;
	const size_t total = sizeof(h) + length;
	c->last_error[0] = 0;
	if (total <= XENSTORE_RING_SIZE) {
		rc = send_request_frame(c, &h, payload, end);
		if (rc) {
			if (c->sending_request) { c->pending[slot] = 0; c->sending_request = 0; }
			return rc;
		}
		sent = total;
	}
	while (sent < total) {
		void *data = sent < sizeof(h) ? (char *)&h + sent : (char *)payload + sent-sizeof(h);
		size_t count = sent < sizeof(h) ? sizeof(h)-sent : total-sent;
		rc = ring_transfer(c, true, data, count);
		if (rc < 0) { return rc; }
		int written = rc;
		sent += (size_t)rc;
		if (sent == total) { c->sending_request = 0; break; }
		bool ignored = false;
		rc = pump(c, 0, 0, 0, NULL, 0, NULL, &ignored);
		if (rc < 0) { return rc; }
		if (sent == total) { c->sending_request = 0; break; }
		if (!xs_remaining(c, end)) {
			if (sent) { fatal(c, -ETIMEDOUT); }
			else { c->pending[slot] = 0; c->sending_request = 0; }
			return -ETIMEDOUT;
		}
		if (written > 0 || rc > 0) { continue; }
		if (!rc) {
			rc = wait_until(c, end);
			if (rc < 0) {
				if (sent) { fatal(c, rc); }
				else { c->pending[slot] = 0; c->sending_request = 0; }
				return rc;
			}
		}
	}
	for (;;) {
		bool ready = false;
		rc = pump(c, h.req_id, type, tx, output, capacity, output_length, &ready);
		if (ready || rc < 0) { return rc < 0 ? rc : 0; }
		if (!xs_remaining(c, end)) {
			if (type == XS_TRANSACTION_START) { c->abandoned_start = h.req_id; }
			return -ETIMEDOUT;
		}
		if (rc > 0) { continue; }
		rc = wait_until(c, end);
		if (rc < 0) { return fatal(c, rc); }
	}
}

static int request(struct xs_client *c, uint32_t type, uint32_t tx,
		   const void *payload, size_t length, void *output,
		   size_t capacity, size_t *output_length, int ms)
{
	int rc = enter(c);
	if (rc) { return rc; }
	rc = request_held(c, type, tx, payload, length, output, capacity, output_length, ms);
	return leave(c, rc);
}

static int path_request(struct xs_client *c, uint32_t type, uint32_t tx,
			const char *path, void *out, size_t size, size_t *len, int ms)
{
	int n = path_length(path);
	if (n < 0) { return n; }
	return request(c, type, tx, path, (size_t)n, out, size, len, ms);
}

int xs_read(struct xs_client *c, uint32_t tx, const char *path,
	    void *out, size_t size, size_t *len, int ms)
{
	return path_request(c, XS_READ, tx, path, out, size, len, ms);
}

int xs_read_text(struct xs_client *c, uint32_t tx, const char *path,
		 char *out, size_t size, int ms)
{
	if (!out || size < 2 || size > XENSTORE_PAYLOAD_MAX) { return -EINVAL; }
	size_t len;
	int rc = xs_read(c, tx, path, out, size-1, &len, ms);
	if (rc) { return rc; }
	if (len && out[len-1] == 0) { len--; }
	if (memchr(out, 0, len)) { return -EPROTO; }
	out[len] = 0;
	return 0;
}

static int string_list(struct xs_client *c, uint32_t type, uint32_t tx,
		       const char *path, char *out, size_t size, size_t *len, int ms)
{
	if (!len) { return -EINVAL; }
	int rc = path_request(c, type, tx, path, out, size, len, ms);
	if (rc) { return rc; }
	if (*len && out[*len-1]) { return -EPROTO; }
	for (size_t i = 0; i < *len;) {
		size_t n = strlen(out+i);
		if (!n) { return -EPROTO; }
		i += n+1;
	}
	return 0;
}

int xs_directory(struct xs_client *c, uint32_t tx, const char *path,
		 char *out, size_t size, size_t *len, int ms)
{
	return string_list(c, XS_DIRECTORY, tx, path, out, size, len, ms);
}

int xs_get_perms(struct xs_client *c, const char *path,
		 char *out, size_t size, size_t *len, int ms)
{
	return string_list(c, XS_GET_PERMS, 0, path, out, size, len, ms);
}

int xs_write(struct xs_client *c, uint32_t tx, const char *path,
	     const void *value, size_t len, int ms)
{
	int n = path_length(path);
	if (n < 0 || len > XS_VALUE_LIMIT || (len && !value)) { return -EINVAL; }
	char payload[XS_PATH_LIMIT + XS_VALUE_LIMIT];
	memcpy(payload, path, (size_t)n);
	if (len) { memcpy(payload+n, value, len); }
	char reply[4];
	return request(c, XS_WRITE, tx, payload, (size_t)n+len, reply, sizeof(reply), NULL, ms);
}

int xs_mkdir(struct xs_client *c, const char *path, int ms)
{
	char reply[4];
	return path_request(c, XS_MKDIR, 0, path, reply, sizeof(reply), NULL, ms);
}

int xs_remove(struct xs_client *c, const char *path, int ms)
{
	char reply[4];
	return path_request(c, XS_RM, 0, path, reply, sizeof(reply), NULL, ms);
}

static int transaction_end_held(struct xs_client *c, uint32_t tx, bool commit, int ms)
{
	if (tx != c->active_tx) { return -EINVAL; }
	/* Replaying a commit can hide the original uncertain outcome. A later
	 * abort only retires the same ID, including ENOENT after a late commit. */
	if (commit && c->orphan_tx) { return -EALREADY; }
	char reply[4];
	int rc = request_held(c, XS_TRANSACTION_END, tx, commit ? "T" : "F", 2,
			 reply, sizeof(reply), NULL, ms);
	if (!rc || rc == -EAGAIN || rc == -ENOENT) {
		c->active_tx = 0;
		c->orphan_tx = false;
	} else {
		c->orphan_tx = true;
	}
	return rc;
}

int xs_transaction_start(struct xs_client *c, uint32_t *tx, int ms)
{
	if (!c || !tx) { return -EINVAL; }
	int lock = enter(c);
	if (lock) { return lock; }
	int64_t end = xs_deadline(c, ms);
	if (end < 0) { return leave(c, -EINVAL); }
	while (c->abandoned_start) {
		if (!xs_remaining(c, end)) { return leave(c, -EBUSY); }
		bool ignored = false;
		int rc = pump(c, 0, 0, 0, NULL, 0, NULL, &ignored);
		if (rc <= 0) { return leave(c, rc ? rc : -EBUSY); }
	}
	if (c->orphan_tx) {
		int rc = transaction_end_held(c, c->active_tx, false, xs_remaining(c, end));
		if (rc && rc != -ENOENT && rc != -EAGAIN) { return leave(c, rc); }
	}
	if (c->active_tx) { return leave(c, -EBUSY); }
	char reply[16]; size_t len = 0;
	int rc = request_held(c, XS_TRANSACTION_START, 0, "", 1, reply, sizeof(reply)-1, &len,
			      xs_remaining(c, end));
	if (rc) { return leave(c, rc); }
	reply[len] = 0;
	if (len && reply[len-1] == 0) { reply[--len] = 0; }
	if (memchr(reply, 0, len)) { return leave(c, fatal(c, -EPROTO)); }
	rc = parse_number(reply, tx);
	if (!rc) { c->active_tx = *tx; }
	else { fatal(c, rc); }
	return leave(c, rc);
}

int xs_transaction_end(struct xs_client *c, uint32_t tx, bool commit, int ms)
{
	if (!c || !tx || ms < 0) { return -EINVAL; }
	int lock = enter(c);
	if (lock) { return lock; }
	return leave(c, transaction_end_held(c, tx, commit, ms));
}

int xs_transaction(struct xs_client *c, unsigned int limit, int ms,
		   xs_transaction_fn fn, void *ctx, unsigned int *used)
{
	if (!c || !fn || !used || !limit || limit > XS_RETRY_LIMIT) { return -EINVAL; }
	int64_t end = xs_deadline(c, ms);
	if (end < 0) { return -EINVAL; }
	*used = 0;
	while (*used < limit && xs_remaining(c, end)) {
		uint32_t tx;
		(*used)++;
		int rc = xs_transaction_start(c, &tx, xs_remaining(c, end));
		if (rc) { return rc; }
		rc = fn(c, tx, end, ctx);
		if (rc) {
			int cleanup = xs_transaction_end(c, tx, false, xs_remaining(c, end));
			return cleanup ? cleanup : rc;
		}
		rc = xs_transaction_end(c, tx, true, xs_remaining(c, end));
		if (rc != -EAGAIN) { return rc; }
	}
	return xs_remaining(c, end) ? -EAGAIN : -ETIMEDOUT;
}

int xs_watch(struct xs_client *c, const char *path, uint32_t *id, int ms)
{
	if (!c || !id || path_length(path) < 0 || path[0] != '/') { return -EINVAL; }
	int lock = enter(c);
	if (lock) { return lock; }
	if (!ms) { return leave(c, -ETIMEDOUT); }
	struct xs_watch_slot *w = NULL;
	for (unsigned int i = 0; i < XS_WATCH_LIMIT; i++) {
		if (c->watches[i].state == XS_W_FREE) { w = &c->watches[i]; break; }
	}
	if (!w) { return leave(c, -ENOSPC); }
	if (c->next_watch == UINT32_MAX) { return leave(c, -EOVERFLOW); }
	memset(w, 0, sizeof(*w));
	w->id = ++c->next_watch;
	snprintf(w->token, sizeof(w->token), "xw-%08x", w->id);
	strcpy(w->path, path);
	w->state = XS_W_REGISTERING;
	*id = w->id;
	char payload[XS_PATH_LIMIT+16], reply[4];
	size_t n = strlen(path)+1, t = strlen(w->token)+1;
	memcpy(payload, path, n); memcpy(payload+n, w->token, t);
	int rc = request_held(c, XS_WATCH, 0, payload, n+t, reply, sizeof(reply), NULL, ms);
	if (rc) {
		w->state = rc == -ETIMEDOUT || c->poisoned ? XS_W_UNCERTAIN : XS_W_FREE;
		return leave(c, rc);
	}
	w->state = XS_W_ACTIVE;
	w->initial = true;
	return leave(c, 0);
}

int xs_unwatch(struct xs_client *c, uint32_t id, int ms)
{
	if (!c) { return -EINVAL; }
	int lock = enter(c);
	if (lock) { return lock; }
	struct xs_watch_slot *w = watch_slot(c, id);
	if (!w) { return leave(c, -ENOENT); }
	if (!ms) { return leave(c, -ETIMEDOUT); }
	w->state = XS_W_CLOSING;
	w->initial = false; w->notifications = 0;
	char payload[XS_PATH_LIMIT+16], reply[4];
	size_t n = strlen(w->path)+1, t = strlen(w->token)+1;
	memcpy(payload, w->path, n); memcpy(payload+n, w->token, t);
	int rc = request_held(c, XS_UNWATCH, 0, payload, n+t, reply, sizeof(reply), NULL, ms);
	w->state = !rc || rc == -ENOENT ? XS_W_FREE : XS_W_UNCERTAIN;
	return leave(c, rc == -ENOENT ? 0 : rc);
}

int xs_watch_next(struct xs_client *c, uint32_t id, struct xs_watch_event *event, int ms)
{
	if (!c || !event) { return -EINVAL; }
	int64_t end = xs_deadline(c, ms);
	if (end < 0) { return -EINVAL; }
	int rc = enter(c);
	if (rc) { return rc; }
	for (;;) {
		struct xs_watch_slot *w = watch_slot(c, id);
		if (!w || w->state != XS_W_ACTIVE) { return leave(c, -ENOENT); }
		if (w->initial || w->notifications) {
			event->initial = w->initial;
			event->notifications = w->notifications;
			w->initial = false; w->notifications = 0;
			return leave(c, 0);
		}
		bool ignored = false;
		rc = pump(c, 0, 0, 0, NULL, 0, NULL, &ignored);
		if (rc < 0) { return leave(c, rc); }
		if (w->notifications) { continue; }
		if (!xs_remaining(c, end)) { return leave(c, -ETIMEDOUT); }
		if (rc > 0) { continue; }
		rc = wait_until(c, end);
		if (rc < 0) { return leave(c, fatal(c, rc)); }
	}
}

int xs_close(struct xs_client *c, int ms)
{
	if (!c || c->closed) { return 0; }
	if (__atomic_load_n(&c->busy, __ATOMIC_ACQUIRE)) { return -EBUSY; }
	int64_t end = xs_deadline(c, ms);
	if (end < 0) { return -EINVAL; }
	int first = 0;
	while (pending_requests(c) && !c->poisoned && xs_remaining(c, end)) {
		bool ignored = false;
		int rc = pump(c, 0, 0, 0, NULL, 0, NULL, &ignored);
		if (rc < 0) { first = rc; break; }
		if (pending_requests(c) && !rc) {
			rc = wait_until(c, end);
			if (rc < 0) { first = rc; break; }
		}
	}
	if (pending_requests(c) && !first) { first = -ETIMEDOUT; }
	if (c->active_tx) {
		int rc = xs_transaction_end(c, c->active_tx, false, xs_remaining(c, end));
		if (rc && rc != -ENOENT && !first) { first = rc; }
	}
	for (unsigned int i = 0; i < XS_WATCH_LIMIT; i++) {
		if (c->watches[i].state != XS_W_FREE) {
			int rc = xs_unwatch(c, c->watches[i].id, xs_remaining(c, end));
			if (rc && !first) { first = rc; }
		}
	}
	if (c->poisoned && !first) { first = c->fatal_error; }
	c->closed = true;
	c->ring = NULL;
	return first;
}
