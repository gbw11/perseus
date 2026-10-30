/* Pure product classifier. Synthetic resource windows are policy boundary
 * fixtures, not measurements of Xen/guest CPU or memory performance. */
#include <xenova/health.h>
#include <assert.h>
#include <errno.h>
#include <stdio.h>

int main(void)
{
 struct xdr_snapshot r={.sample=1,.raw={.at_ms=100},.cpu={.start_ms=0,.end_ms=100,.percent_milli=95000},
  .heap_free_bytes=1024,.rows=2,.seen=2};
 struct xdh_work w={.state=XDH_COMPLETED,.required=true,.begin_us=100,.end_us=20000,.deadline_us=20000};
 struct xdh_health h;
 assert(!xdh_assess(&w,20000,100,&r,0,&h));assert(!h.restrict_function&&!h.warnings);
 r.cpu.percent_milli=100000;r.heap_free_bytes=0;
 assert(!xdh_assess(&w,20000,100,&r,0,&h));
 assert(!h.restrict_function&&(h.warnings&XDH_CPU_NO_IDLE)&&(h.warnings&XDH_MEMORY_NO_FREE));
 w.end_us=20001;assert(!xdh_assess(&w,20001,100,&r,0,&h));assert(h.restrict_function&&h.failure==XDH_DEADLINE);
 r.cpu.percent_milli=0;assert(!xdh_assess(&w,20001,100,&r,0,&h));assert(h.restrict_function); /* zero CPU cannot cancel a confirmed miss */
 w.end_us=1000;w.rc=-ENOMEM;
 assert(!xdh_assess(&w,1000,100,&r,0,&h));assert(h.restrict_function&&h.failure==XDH_RESOURCE_FAILED);
 w.required=false;assert(!xdh_assess(&w,1000,100,&r,0,&h));assert(!h.restrict_function); /* diagnostic-only allocation */
 w.required=true;w.rc=0;
 assert(!xdh_assess(&w,1000,1200,&r,0,&h));assert(!h.cpu_valid&&!h.restrict_function);
 assert(!xdh_assess(&w,1000,100,&r,-EBUSY,&h));assert(!h.cpu_valid&&!h.restrict_function);
 r.threads_rc=-ENOSPC;r.seen=65;r.rows=32;
 assert(!xdh_assess(&w,1000,100,&r,0,&h));assert(h.warnings&XDH_THREADS_INCOMPLETE);
 w.clock_rc=-EIO;assert(!xdh_assess(&w,1000,100,&r,0,&h));assert(h.restrict_function&&h.failure==XDH_UNVERIFIED);
 w.clock_rc=0;w.state=XDH_IDLE;
 assert(!xdh_assess(&w,999999,100,&r,0,&h));assert(!h.restrict_function); /* event worker with no task */
 w.state=XDH_NOT_STARTED;assert(!xdh_assess(&w,999999,100,&r,0,&h));assert(!h.restrict_function);
 w.state=XDH_STOPPING;assert(!xdh_assess(&w,999999,100,&r,0,&h));assert(!h.restrict_function);
 w.state=XDH_RUNNING;assert(!xdh_assess(&w,20001,100,&r,0,&h));assert(h.restrict_function);
 puts("health evidence: independent resource warnings, task failure/deadline and idle exclusions PASS");
}
