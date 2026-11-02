/* Real codec/preparation path; the merge segment table is an app-input fixture. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "xenova/mqtt/xm.h"
#include "adapter.h"
#define SPAN(s) ((struct xm_span){s,strlen(s)})
static const char *A="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
static const char *B="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
static const char *COPY="cccccccc-cccc-4ccc-8ccc-cccccccccccc";

int main(int argc,char **argv)
{
 char wire[4097];struct xm_state_snapshot value={0},parsed={0};
 if(argc==2&&!strcmp(argv[1],"--roundtrip")){
  size_t n=fread(wire,1,sizeof(wire),stdin);
  if(n>4096||xm_state_snapshot_parse((struct xm_span){wire,n},&value))return 2;
  int length=xm_state_snapshot_json(wire,sizeof(wire),&value);
  if(length<0)return 3;
  puts(wire);return 0;
 }
 strcpy(value.execution,A);strcpy(value.context.odom_epoch,COPY);
 value.intent_revision=1;value.lifecycle_generation=2;value.source_generation=42;value.state_revision=UINT64_MAX;
 value.context.frame=1;value.context.configuration=2;value.context.maneuver=3;
 strcpy(value.kind,"AVOID");strcpy(value.side,"LEFT");strcpy(value.phase,"PASS");strcpy(value.merge_segment,"lane:next");
 int n=xm_state_snapshot_json(wire,sizeof(wire),&value);assert(n>0);
 assert(!xm_state_snapshot_parse((struct xm_span){wire,(size_t)n},&parsed));
 assert(parsed.state_revision==UINT64_MAX&&!strcmp(parsed.merge_segment,"lane:next"));
 assert(xm_state_snapshot_json(wire,20,&value)==-ENOSPC);
 n=xm_state_snapshot_json(wire,sizeof(wire),&value);
 struct xm_envelope env={0};env.type=SPAN("app.state.snapshot");env.vm_id=SPAN(A);
 env.app_id=SPAN("autonomous");env.instance_id=SPAN(COPY);env.message_id=SPAN(COPY);
 struct xm_vehicle_control control={.generation=42};strcpy(control.active_vm_id,A);strcpy(control.active_app,"autonomous");
 struct drive_state_context target={.frame=1,.configuration=2,.maneuver=3};
 strcpy(target.request,COPY);strcpy(target.execution,B);strcpy(target.instance,COPY);
 const char *segments[]={"lane:next"};struct drive_maneuver_copy copy={0};
 struct xm_state_context current=value.context;current.road_revision=100;current.route_revision=101;
 assert(!drive_apply_maneuver(&copy,&env,(struct xm_span){wire,(size_t)n},&control,B,&target,&current,segments,1,10,100));
 assert(drive_state_usable(&copy.prepared,&target));
 struct drive_adapter adapter={.requires_state_sync=true};struct drive_maneuver_copy wrapped={0};
 assert(!drive_adapter_apply_maneuver(&adapter,&wrapped,&env,(struct xm_span){wire,(size_t)n},&control,B,&target,&current,segments,1,10,100));
 assert(adapter.state_ready&&adapter.progress==0&&adapter.prepared_state==&wrapped.prepared);
 assert(copy.prepared.origin==DRIVE_STATE_HANDOFF&&copy.prepared.state_revision==UINT64_MAX);
 assert(drive_apply_maneuver(&copy,&env,(struct xm_span){wire,(size_t)n},&control,B,&target,&current,segments,1,11,100)==-EBUSY);
 struct drive_maneuver_copy changed={0};current.frame=2;
 assert(drive_apply_maneuver(&changed,&env,(struct xm_span){wire,(size_t)n},&control,B,&target,&current,segments,1,10,100)==-EXDEV);
 drive_state_cancel(&copy.prepared);assert(!drive_state_usable(&copy.prepared,&target));
 struct xm_state_application applied={.state_revision=UINT64_MAX,.intent_revision=1,.lifecycle_generation=2,.context=value.context,.applied=true};
 strcpy(applied.execution,B);strcpy(applied.copy_message_id,COPY);
 n=xm_state_application_json(wire,sizeof(wire),&applied);assert(n>0);
 struct xm_state_application ack;assert(!xm_state_application_parse((struct xm_span){wire,(size_t)n},&ack));
 assert(ack.applied&&!ack.rebuilt&&ack.state_revision==UINT64_MAX);
 printf("state codec/application PASS; snapshot=%zu application=%zu immutable_copy=%zu\n",sizeof(value),sizeof(ack),sizeof(copy));
 return 0;
}
