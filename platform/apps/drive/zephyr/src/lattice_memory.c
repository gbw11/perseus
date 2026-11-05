/* SPDX-License-Identifier: Apache-2.0 */
/* No payload copying/scanning, no transport, no readiness or progress effects. */
#include "lattice_memory.h"
#include <xenova/diagnostics.h>
#include <errno.h>
#if defined(CONFIG_XENOVA_DRIVE_WORKERS) && defined(CONFIG_XENOVA_DRIVE_RUNTIME)
#include "workers.h"
#include <xenova/drive_io.h>
#define MANUAL_MEMORY 1
#endif
static void memory_observe(struct xdr_app_region out[XDR_APP_REGIONS],struct xdr_app_buffer detail[XDR_MANUAL_REGIONS],void *context){
 struct drive_engine *e=context;
 for(unsigned i=0;i<XDR_AUTO_REGIONS;i++){
  struct dw_memory m={0};enum de_result rc=de_memory_read(e,i,&m);
  out[i]=(struct xdr_app_region){.rc=rc==DE_BUSY?-EBUSY:rc?-EIO:0,
   .reserved=m.reserved,.capacity=m.capacity,.used=m.used,.peak=m.peak,
   .exhausted=m.exhausted,.contention=m.contention};
 }
#if defined(MANUAL_MEMORY)
 int rc=xd_io_memory_read(&out[4],&detail[0]);
 if(rc)out[4].rc=rc;
 rc=drive_workers_memory_read(&out[5],&detail[1]);
 if(rc)for(unsigned i=5;i<XDR_APP_REGIONS;i++)out[i].rc=rc;
#else
 (void)detail;
#endif
}
int drive_lattice_memory_register(struct drive_engine *engine){
 if(!engine)return -EINVAL;
 return xdiag_app_memory_register(memory_observe,engine,
#if defined(MANUAL_MEMORY)
 XDR_APP_REGIONS
#else
 XDR_AUTO_REGIONS
#endif
 );
}
