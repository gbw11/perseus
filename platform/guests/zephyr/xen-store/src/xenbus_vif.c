/* SPDX-License-Identifier: Apache-2.0 */
/* Observe XenBus state without writing frontend state or netfront resources. */
#include <xenova/xenbus_vif.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>

static int number(const char *text, uint32_t max, uint32_t *out)
{
	if (!text || !*text) { return -EPROTO; }
	uint32_t n = 0;
	for (; *text; text++) {
		if (*text < '0' || *text > '9' || n > (max - (*text-'0'))/10) {
			return -EPROTO;
		}
		n = n*10 + (*text-'0');
		if (n > max) { return -EPROTO; }
	}
	*out = n;
	return 0;
}

static int read_child(struct xs_client *c, const char *root, const char *key,
		      char *out, size_t size, int64_t end)
{
	char path[XS_PATH_LIMIT];
	int n = snprintf(path, sizeof(path), "%s/%s", root, key);
	if (n < 0 || n >= (int)sizeof(path)) { return -ENAMETOOLONG; }
	return xs_read_text(c, 0, path, out, size, xs_remaining(c, end));
}

static int state(struct xs_client *c, const char *root, enum xenbus_state *out, int64_t end)
{
	char text[16]; uint32_t n;
	int rc = read_child(c, root, "state", text, sizeof(text), end);
	if (rc) { return rc; }
	rc = number(text, XenbusStateReconfigured, &n);
	if (!rc) { *out = (enum xenbus_state)n; }
	return rc;
}

static int mac_address(const char *text, uint8_t mac[6])
{
	if (strlen(text) != 17) { return -EPROTO; }
	for (int i = 0; i < 6; i++) {
		unsigned int n = 0;
		for (int j = 0; j < 2; j++) {
			char ch = text[i*3+j];
			int v = ch >= '0' && ch <= '9' ? ch-'0' :
				ch >= 'a' && ch <= 'f' ? ch-'a'+10 :
				ch >= 'A' && ch <= 'F' ? ch-'A'+10 : -1;
			if (v < 0) { return -EPROTO; }
			n = n*16 + (unsigned int)v;
		}
		mac[i] = (uint8_t)n;
		if (i < 5 && text[i*3+2] != ':') { return -EPROTO; }
	}
	if ((mac[0]&1) || !memcmp(mac, (uint8_t[6]){0}, 6)) { return -EPROTO; }
	return 0;
}

static int discover_once(struct xs_client *c, struct xvif *v, int64_t end)
{
	char text[XS_PATH_LIMIT], entries[64]; size_t len;
	int rc = xs_read_text(c, 0, "domid", text, sizeof(text), xs_remaining(c, end));
	if (rc) { return rc; }
	rc = number(text, 0x7fef, &v->domid);
	if (rc || !v->domid) { return -EPROTO; }
	snprintf(v->domain, sizeof(v->domain), "/local/domain/%u", v->domid);
	snprintf(v->frontend, sizeof(v->frontend), "/local/domain/%u/device/vif", v->domid);
	rc = xs_directory(c, 0, v->frontend, entries, sizeof(entries), &len, xs_remaining(c, end));
	if (rc == -ENOENT || (!rc && !len)) { return -ENODEV; }
	if (rc) { return rc; }
	if (strlen(entries)+1 != len) { return -E2BIG; }
	rc = number(entries, 65535, &v->device);
	if (rc) { return rc; }
	snprintf(v->frontend, sizeof(v->frontend), "/local/domain/%u/device/vif/%u", v->domid, v->device);
	rc = read_child(c, v->frontend, "backend", v->backend, sizeof(v->backend), end);
	if (rc == -ENOENT) { return -EAGAIN; }
	if (rc) { return rc; }
	rc = read_child(c, v->frontend, "backend-id", text, sizeof(text), end);
	if (rc == -ENOENT) { return -EAGAIN; }
	if (rc) { return rc; }
	rc = number(text, 0x7fef, &v->backend_domid);
	if (rc) { return rc; }
	char expected[XS_PATH_LIMIT];
	snprintf(expected, sizeof(expected), "/local/domain/%u/backend/vif/%u/%u",
		 v->backend_domid, v->domid, v->device);
	if (strcmp(expected, v->backend)) { return -EPROTO; }
	rc = read_child(c, v->frontend, "mac", text, sizeof(text), end);
	if (rc == -ENOENT) { return -EAGAIN; }
	if (rc) { return rc; }
	rc = mac_address(text, v->mac);
	if (rc) { return rc; }
	rc = state(c, v->frontend, &v->frontend_state, end);
	if (rc == -ENOENT) { return -EAGAIN; }
	if (rc) { return rc; }
	rc = state(c, v->backend, &v->backend_state, end);
	return rc == -ENOENT ? -EAGAIN : rc;
}

int xvif_probe(struct xs_client *c, struct xvif *v, int ms)
{
	if (!c || !v) { return -EINVAL; }
	int64_t end = xs_deadline(c, ms);
	if (end < 0) { return -EINVAL; }
	memset(v, 0, sizeof(*v));
	return discover_once(c, v, end);
}

int xvif_discover(struct xs_client *c, struct xvif *v, int ms)
{
	if (!c || !v) { return -EINVAL; }
	int64_t end = xs_deadline(c, ms);
	if (end < 0) { return -EINVAL; }
	memset(v, 0, sizeof(*v));
	for (;;) {
		int rc = discover_once(c, v, end);
		if (rc != -EAGAIN || !ms) { return rc; }
		int remaining = xs_remaining(c, end);
		if (!remaining) { return -ETIMEDOUT; }
		int64_t next = c->io.now(c->io.context) + (remaining < 10 ? remaining : 10);
		rc = c->io.wait(c->io.context, next);
		if (rc && rc != -ETIMEDOUT) { return rc; }
	}
}

int xvif_watch_backend(struct xs_client *c, const struct xvif *v, uint32_t *id, int ms)
{
	if (!v) { return -EINVAL; }
	char path[XS_PATH_LIMIT];
	int n = snprintf(path, sizeof(path), "%s/state", v->backend);
	if (n < 0 || n >= (int)sizeof(path)) { return -ENAMETOOLONG; }
	return xs_watch(c, path, id, ms);
}

int xvif_refresh(struct xs_client *c, struct xvif *v, int ms)
{
	if (!c || !v) { return -EINVAL; }
	int64_t end = xs_deadline(c, ms);
	if (end < 0) { return -EINVAL; }
	char backend[XS_PATH_LIMIT];
	int rc = read_child(c, v->frontend, "backend", backend, sizeof(backend), end);
	if (rc) { return rc; }
	if (strcmp(backend, v->backend)) { return -EXDEV; }
	rc = state(c, v->frontend, &v->frontend_state, end);
	return rc ? rc : state(c, v->backend, &v->backend_state, end);
}

int xvif_wait_backend(struct xs_client *c, struct xvif *v, uint32_t watch,
		      enum xenbus_state wanted, int ms)
{
	if (!c || !v || wanted > XenbusStateReconfigured || wanted < XenbusStateUnknown) {
		return -EINVAL;
	}
	int64_t end = xs_deadline(c, ms);
	if (end < 0) { return -EINVAL; }
	for (;;) {
		int rc = xvif_refresh(c, v, xs_remaining(c, end));
		if (rc) { return rc; }
		if (v->backend_state == wanted) { return 0; }
		if (!xs_remaining(c, end)) { return -ETIMEDOUT; }
		struct xs_watch_event event;
		rc = xs_watch_next(c, watch, &event, xs_remaining(c, end));
		if (rc) { return rc; }
	}
}
