/* SPDX-License-Identifier: Apache-2.0 */
/* Evidence axes stay separate: missing diagnostics cannot erase a confirmed
 * work failure, and high occupancy alone never authorizes a restart. */
#include <xenova/health.h>
#include <errno.h>
#include <string.h>

int xdh_assess(const struct xdh_work *work,uint64_t now,uint64_t uptime,
 const struct xdr_snapshot *r,int resource_rc,struct xdh_health *out)
{
 if(!work||!out||(unsigned)work->state>XDH_STOPPING)return -EINVAL;
 memset(out,0,sizeof(*out));out->work_rc=work->rc;
 bool fresh=!resource_rc&&r&&r->sample&&uptime>=r->raw.at_ms&&uptime-r->raw.at_ms<=1000;
 out->cpu_rc=resource_rc?resource_rc:!fresh?-ENODATA:r->cpu.rc;
 if(fresh){
  out->resource_sample=r->sample;
  out->cpu_start_ms=r->cpu.start_ms;out->cpu_end_ms=r->cpu.end_ms;
  if(!out->cpu_rc&&(r->cpu.percent_milli>100000||r->cpu.end_ms<r->cpu.start_ms||
      r->cpu.end_ms!=r->raw.at_ms))out->cpu_rc=-ERANGE;
  if(r->threads_rc||r->seen>r->rows)out->warnings|=XDH_THREADS_INCOMPLETE;
 }
 out->cpu_valid=!out->cpu_rc;
 if(!out->cpu_valid)out->warnings|=XDH_CPU_UNAVAILABLE;
 else {
  out->cpu_percent_milli=r->cpu.percent_milli;
  /* No arbitrary 90% threshold: this window observed no accounted idle. */
  if(r->cpu.percent_milli==100000)out->warnings|=XDH_CPU_NO_IDLE;
 }
 if(!fresh||r->heap_rc)out->warnings|=XDH_MEMORY_UNAVAILABLE;
 else if(!r->heap_free_bytes)out->warnings|=XDH_MEMORY_NO_FREE;
 if(fresh)for(size_t i=0;i<r->regions&&i<XDR_REGIONS;i++){
  if(r->memory[i].rc)out->warnings|=XDH_MEMORY_UNAVAILABLE;
  else if(!r->memory[i].free)out->warnings|=XDH_MEMORY_NO_FREE;
 }
 if(work->state==XDH_NOT_STARTED||work->state==XDH_IDLE||work->state==XDH_STOPPING)return 0;
 if(work->rc)out->failure=(work->rc==-ENOMEM||work->rc==-ENOBUFS)?XDH_RESOURCE_FAILED:XDH_WORK_FAILED;
 else if(work->clock_rc||!work->deadline_us||now<work->begin_us||
         (work->state==XDH_COMPLETED&&(work->end_us<work->begin_us||now<work->end_us)))out->failure=XDH_UNVERIFIED;
 else if((work->state==XDH_COMPLETED?work->end_us:now)>work->deadline_us)out->failure=XDH_DEADLINE;
 out->restrict_function=work->required&&out->failure!=XDH_NONE;
 return 0;
}
