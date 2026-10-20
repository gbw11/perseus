/* SPDX-License-Identifier: Apache-2.0 */
/* Thread-context XenStore exercise; writes stay under one owned test node. */
#include <xenova/xenstore_zephyr.h>
#include <xenova/xenbus_vif.h>
#include <zephyr/sys/printk.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>

#define REQUEST_MS 3000
#define WATCH_MS 60000
static struct xs_zephyr transport;
static struct xvif vif;

static void result(const char *phase, int rc)
{
	printk("[g1] %s rc=%d store_error=%s\n", phase, rc,
	       transport.link.client.last_error);
}

static int child(char *out, size_t size, const char *root, const char *key)
{
	int n = snprintf(out, size, "%s/%s", root, key);
	return n < 0 || n >= (int)size ? -ENAMETOOLONG : 0;
}

static int domain_id(const char *text, unsigned int *id)
{
	if (!text || !*text) { return -EPROTO; }
	unsigned int value = 0;
	for (; *text; text++) {
		if (*text < '0' || *text > '9' || value > (0x7fefU-(*text-'0'))/10) {
			return -EPROTO;
		}
		value = value*10 + (*text-'0');
	}
	*id = value;
	return 0;
}

static int write_permitted(struct xs_client *c, const char *path, unsigned int self)
{
	char perms[256]; size_t length = 0;
	int rc = xs_get_perms(c, path, perms, sizeof(perms), &length, REQUEST_MS);
	if (rc) { return rc; }
	for (size_t i = 0; i < length;) {
		char *entry = perms + i;
		unsigned int id;
		if (!strchr("nrwb", entry[0]) || domain_id(entry+1, &id)) {
			return -EPROTO;
		}
		/* First entry is the owner; later entries grant explicit permissions. */
		if (id == self && (i == 0 || entry[0] == 'w' || entry[0] == 'b')) { return 0; }
		i += strlen(entry)+1;
	}
	return -EACCES;
}

static int claim_test_node(struct xs_client *c, uint32_t tx, int64_t end, void *context)
{
	const char *root = context;
	char entries[64]; size_t length;
	int rc = xs_directory(c, tx, root, entries, sizeof(entries), &length, xs_remaining(c, end));
	if (rc != -ENOENT) { return rc ? rc : -EEXIST; }
	return xs_write(c, tx, root, "", 0, xs_remaining(c, end));
}

static int transaction_body(struct xs_client *c, uint32_t tx, int64_t end, void *context)
{
	const char *key = context;
	char value[64];
	int rc = xs_write(c, tx, key, "committed", 9, xs_remaining(c, end));
	if (!rc) { rc = xs_read_text(c, tx, key, value, sizeof(value), xs_remaining(c, end)); }
	if (!rc && strcmp(value, "committed")) { rc = -EPROTO; }
	return rc;
}

static int backend_observation(struct xs_client *c, uint32_t watch, int64_t end)
{
	struct xs_watch_event event;
	int rc = xs_watch_next(c, watch, &event, 0);
	if (rc == -ETIMEDOUT) { return 0; }
	if (rc) { return rc; }
	rc = xvif_refresh(c, &vif, xs_remaining(c, end));
	if (!rc) {
		printk("[g1] backend-watch initial=%d notifications=%u frontend=%d backend=%d\n",
		       event.initial, event.notifications, vif.frontend_state, vif.backend_state);
	}
	return rc;
}

static int exercise(struct xs_client *c, const char *root, uint32_t backend_watch)
{
	char key[XS_PATH_LIMIT], control[XS_PATH_LIMIT], value[128], entries[128];
	int rc = child(key, sizeof(key), root, "value");
	if (!rc) { rc = child(control, sizeof(control), root, "control"); }
	if (rc) { return rc; }
	rc = xs_write(c, 0, key, "baseline", 8, REQUEST_MS);
	if (!rc) { rc = xs_read_text(c, 0, key, value, sizeof(value), REQUEST_MS); }
	if (!rc && strcmp(value, "baseline")) { rc = -EPROTO; }
	result("read-write", rc);
	if (rc) { return rc; }
	size_t length;
	rc = xs_directory(c, 0, root, entries, sizeof(entries), &length, REQUEST_MS);
	if (!rc && (!length || !memchr(entries, 0, length))) { rc = -EPROTO; }
	result("directory", rc);
	if (rc) { return rc; }

	unsigned int attempts;
	rc = xs_transaction(c, 3, 10000, transaction_body, key, &attempts);
	printk("[g1] transaction-commit attempts=%u rc=%d\n", attempts, rc);
	if (!rc) { rc = xs_read_text(c, 0, key, value, sizeof(value), REQUEST_MS); }
	if (!rc && strcmp(value, "committed")) { rc = -EPROTO; }
	if (rc) { return rc; }
	uint32_t tx;
	rc = xs_transaction_start(c, &tx, REQUEST_MS);
	if (!rc) { rc = xs_write(c, tx, key, "aborted", 7, REQUEST_MS); }
	if (rc) { return rc; }
	rc = xs_transaction_end(c, tx, false, REQUEST_MS);
	if (!rc) { rc = xs_read_text(c, 0, key, value, sizeof(value), REQUEST_MS); }
	if (!rc && strcmp(value, "committed")) { rc = -EPROTO; }
	result("transaction-abort", rc);
	if (rc) { return rc; }

	rc = xs_write(c, 0, control, "guest-initial", 13, REQUEST_MS);
	if (rc) { return rc; }
	uint32_t watch = 0;
	rc = xs_watch(c, control, &watch, REQUEST_MS);
	if (rc) { return rc; }
	struct xs_watch_event event;
	rc = xs_watch_next(c, watch, &event, REQUEST_MS);
	if (!rc) { rc = xs_read_text(c, 0, control, value, sizeof(value), REQUEST_MS); }
	if (!rc) {
		printk("[g1] watch-initial notifications=%u value=%s\n", event.notifications, value);
		printk("[g1] watch-ready path=%s baseline=guest-initial budget_ms=%d\n", control, WATCH_MS);
	}
	bool changed = !rc && strcmp(value, "guest-initial");
	int64_t end = xs_deadline(c, WATCH_MS);
	while (!rc && !changed) {
		if (backend_watch) { rc = backend_observation(c, backend_watch, end); }
		if (rc) { break; }
		int left = xs_remaining(c, end);
		if (!left) { rc = -ETIMEDOUT; break; }
		rc = xs_watch_next(c, watch, &event, left < 1000 ? left : 1000);
		if (rc == -ETIMEDOUT && xs_remaining(c, end)) { rc = 0; continue; }
		if (rc) { break; }
		rc = xs_read_text(c, 0, control, value, sizeof(value), xs_remaining(c, end));
		if (rc) { break; }
		printk("[g1] watch-reread initial=%d notifications=%u value=%s\n",
		       event.initial, event.notifications, value);
		if (!strcmp(value, "guest-initial")) { continue; }
		if (event.notifications) { break; }
	}
	result(rc == -ETIMEDOUT ? "watch-timeout" : "watch-result", rc);
	int cleanup = xs_unwatch(c, watch, REQUEST_MS);
	result("unwatch", cleanup);
	return rc ? rc : cleanup;
}

int main(void)
{
	printk("[g1] Xenova XenStore / XenBus observation\n");
	int rc = xs_zephyr_open(&transport);
	result("store-map-bind", rc);
	if (rc) { return rc; }
	struct xs_client *c = &transport.link.client;
	printk("[g1] store-connected port=%u\n", transport.link.port);

	int vif_rc = xvif_probe(c, &vif, 10000);
	if (vif_rc == -EAGAIN) {
		result("vif-backend-pending", vif_rc);
		vif_rc = xvif_discover(c, &vif, 10000);
	}
	result(vif_rc == -ENODEV ? "vif-absent" : "vif-discovery", vif_rc);
	uint32_t backend_watch = 0;
	if (!vif_rc) {
		printk("[g1] vif frontend=%s backend=%s backend_id=%u mac=%02x:%02x:%02x:%02x:%02x:%02x frontend_state=%d backend_state=%d\n",
		       vif.frontend, vif.backend, vif.backend_domid,
		       vif.mac[0], vif.mac[1], vif.mac[2], vif.mac[3], vif.mac[4], vif.mac[5],
		       vif.frontend_state, vif.backend_state);
		vif_rc = xvif_watch_backend(c, &vif, &backend_watch, REQUEST_MS);
		if (!vif_rc) { vif_rc = backend_observation(c, backend_watch, xs_deadline(c, REQUEST_MS)); }
		result("backend-watch-registration", vif_rc);
	}

	char domid_text[16], domain[64], root[XS_PATH_LIMIT];
	unsigned int domid = 0;
	bool owned = false;
	rc = xs_read_text(c, 0, "domid", domid_text, sizeof(domid_text), REQUEST_MS);
	if (!rc && (domain_id(domid_text, &domid) ||
		    !domid || domid >= 0x7ff0)) { rc = -EPROTO; }
	if (!rc) {
		snprintf(domain, sizeof(domain), "/local/domain/%u/data", domid);
		rc = write_permitted(c, domain, domid);
		result(rc == -ENOENT ? "test-parent-missing" :
		       rc == -EACCES ? "test-parent-denied" : "test-parent-permission", rc);
	}
	if (!rc) {
		snprintf(root, sizeof(root), "%s/xenova-g1-%llx", domain,
			 (unsigned long long)k_cycle_get_64());
		printk("[g1] test-node-candidate path=%s\n", root);
		unsigned int attempts;
		rc = xs_transaction(c, 3, 10000, claim_test_node, root, &attempts);
		owned = !rc;
	}
	if (!rc) { rc = write_permitted(c, root, domid); }
	result("test-node-permission", rc);
	if (!rc) { rc = exercise(c, root, vif_rc ? 0 : backend_watch); }
	if (owned) {
		int cleanup = xs_remove(c, root, REQUEST_MS);
		result("test-node-cleanup", cleanup);
		if (!rc) { rc = cleanup; }
	}
	int cleanup = xs_zephyr_close(&transport, 10000);
	result("store-cleanup", cleanup);
	if (!rc) { rc = vif_rc ? vif_rc : cleanup; }
	result("exercise-result", rc);
	k_sleep(K_FOREVER);
	return rc;
}
