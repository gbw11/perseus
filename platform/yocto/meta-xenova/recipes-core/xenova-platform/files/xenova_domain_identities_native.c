/* Bounded read-only batch of the same pinned libxenmanage ABI used by
 * xenova_domain_identity_native.c. One process/handle, no VM operations.
 * No success bytes are written until every requested identity is verified. */
#define _POSIX_C_SOURCE 200809L
#include <dlfcn.h>
#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_DOMAINS 256
typedef void *(*open_fn)(void *, unsigned int);
typedef int (*close_fn)(void *);
typedef int (*info_fn)(void *, unsigned int, unsigned int *, unsigned int *, uint64_t *);

static int failure(const char *reason)
{
    fprintf(stderr, "xenova-domain-identities: %s\n", reason);
    return 1;
}

int main(int argc, char **argv)
{
    if (argc == 2 && !strcmp(argv[1], "--help")) {
        puts("Read-only identity batch v1. usage: --domids N,N,... (1..256 unique guests)");
        return 0;
    }
    if (argc != 3 || strcmp(argv[1], "--domids")) return failure("--domids is required");
    unsigned int ids[MAX_DOMAINS], states[MAX_DOMAINS];
    uint64_t uniques[MAX_DOMAINS];
    size_t count = 0;
    const char *next = argv[2];
    for (;;) {
        if (count == MAX_DOMAINS || *next < '1' || *next > '9')
            return failure("canonical guest domids required");
        char *end;
        errno = 0;
        unsigned long id = strtoul(next, &end, 10);
        if (errno || id >= 0x7ff0 || (*end && *end != ',')) return failure("invalid guest domid");
        for (size_t j = 0; j < count; ++j)
            if (ids[j] == id) return failure("duplicate guest domid");
        ids[count++] = (unsigned int)id;
        if (!*end) break;
        next = end + 1;
    }
    void *library = dlopen("libxenmanage.so.1", RTLD_NOW | RTLD_LOCAL);
    if (!library) return failure("libxenmanage.so.1 unavailable");
    open_fn open_handle;
    close_fn close_handle;
    info_fn get_info;
    void *symbol = dlsym(library, "xenmanage_open");
    _Static_assert(sizeof(open_handle) == sizeof(symbol), "unsupported function pointer ABI");
    memcpy(&open_handle, &symbol, sizeof(open_handle));
    symbol = dlsym(library, "xenmanage_close");
    memcpy(&close_handle, &symbol, sizeof(close_handle));
    symbol = dlsym(library, "xenmanage_get_domain_info");
    memcpy(&get_info, &symbol, sizeof(get_info));
    if (!open_handle || !close_handle || !get_info) {
        dlclose(library);
        return failure("required Xen manage ABI unavailable");
    }
    void *handle = open_handle(NULL, 0);
    if (!handle) {
        dlclose(library);
        return failure("Xen manage handle unavailable");
    }
    for (size_t i = 0; i < count; ++i) {
        unsigned int capabilities = 0;
        states[i] = 0;
        uniques[i] = 0;
        if (get_info(handle, ids[i], &states[i], &capabilities, &uniques[i]) ||
            !(states[i] & 1) || !uniques[i]) {
            close_handle(handle);
            dlclose(library);
            return failure("live domain incarnation could not be verified");
        }
    }
    close_handle(handle);
    dlclose(library);
    printf("{\"schema_version\":1,\"items\":[");
    for (size_t i = 0; i < count; ++i)
        printf("%s{\"domid\":%u,\"xen_unique_id\":\"%" PRIu64 "\",\"state_bits\":%u}",
               i ? "," : "", ids[i], uniques[i], states[i]);
    puts("]}");
    return 0;
}
