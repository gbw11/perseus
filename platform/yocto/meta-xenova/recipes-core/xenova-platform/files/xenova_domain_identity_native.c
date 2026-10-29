/* Read-only CLI equivalent of xenova_domain_identity.py. No VM operations.
 * ABI: Xen 1c72306b1f0b4dc533954f678361044f8083dccc xenmanage.h, as in the
 * existing ctypes implementation. Resolve the same versioned shared library;
 * keep the subprocess boundary so the caller can enforce its deadline.
 */
#define _POSIX_C_SOURCE 200809L
#include <dlfcn.h>
#include <errno.h>
#include <inttypes.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef void *(*open_fn)(void *, unsigned int);
typedef int (*close_fn)(void *);
typedef int (*info_fn)(void *, unsigned int, unsigned int *, unsigned int *, uint64_t *);

static int usage(const char *reason)
{
    fprintf(stderr, "usage: xenova-domain-identity --domid N [--allow-dom0]\n");
    if (reason) fprintf(stderr, "xenova-domain-identity: %s\n", reason);
    return 2;
}

static int failure(const char *kind, const char *message)
{
    fprintf(stderr, "[xenova-domain-identity] %s: %s\n", kind, message);
    return 1;
}

int main(int argc, char **argv)
{
    const char *text = NULL;
    bool allow_dom0 = false;
    for (int i = 1; i < argc; ++i) {
        if (!strcmp(argv[i], "--allow-dom0")) allow_dom0 = true;
        else if (!strcmp(argv[i], "--domid")) {
            if (++i == argc) return usage("--domid requires an integer");
            text = argv[i];
        } else if (!strncmp(argv[i], "--domid=", 8)) text = argv[i] + 8;
        else if (!strcmp(argv[i], "--help") || !strcmp(argv[i], "-h")) {
            puts("Read a live Xen domain incarnation; performs no VM changes.\n"
                 "usage: xenova-domain-identity --domid N [--allow-dom0]");
            return 0;
        } else return usage("unrecognized argument");
    }
    if (!text || !*text) return usage("--domid is required");
    char *end;
    errno = 0;
    long domid = strtol(text, &end, 10);
    if (errno || *end) return usage("--domid requires an integer");
    if (domid < 0 || domid >= 0x7ff0 || (!domid && !allow_dom0))
        return failure("ValueError", "valid guest domid required");

    void *library = dlopen("libxenmanage.so.1", RTLD_NOW | RTLD_LOCAL);
    if (!library) return failure("OSError", "libxenmanage.so.1 could not be loaded");
    open_fn open_handle;
    close_fn close_handle;
    info_fn get_info;
    /* POSIX dlsym returns function addresses through void*. memcpy avoids
     * non-ISO casts or type-punning while retaining the exact pinned ABI. */
    void *symbol = dlsym(library, "xenmanage_open");
    _Static_assert(sizeof(open_handle) == sizeof(symbol), "unsupported function pointer ABI");
    memcpy(&open_handle, &symbol, sizeof(open_handle));
    symbol = dlsym(library, "xenmanage_close");
    memcpy(&close_handle, &symbol, sizeof(close_handle));
    symbol = dlsym(library, "xenmanage_get_domain_info");
    memcpy(&get_info, &symbol, sizeof(get_info));
    if (!open_handle || !close_handle || !get_info) {
        dlclose(library);
        return failure("OSError", "required Xen manage ABI is unavailable");
    }
    void *handle = open_handle(NULL, 0);
    if (!handle) {
        int saved = errno;
        dlclose(library);
        return failure("OSError", strerror(saved));
    }
    unsigned int state = 0, capabilities = 0;
    uint64_t unique = 0;
    int status = get_info(handle, (unsigned int)domid, &state, &capabilities, &unique);
    int saved = errno;
    close_handle(handle);
    dlclose(library);
    if (status) return failure("OSError", strerror(saved));
    if (!(state & 1) || !unique)
        return failure("ValueError", "live domain incarnation could not be verified");
    printf("{\"domid\":%ld,\"xen_unique_id\":\"%" PRIu64 "\",\"state_bits\":%u}\n",
           domid, unique, state);
    return 0;
}
