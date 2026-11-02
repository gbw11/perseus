/* App-owned immutable candidate application. Gateway independently requires a
 * matching source copy accepted while authorized. This helper grants no output.
 * Call only in the app's preparation owner, before exposing the copy to workers;
 * retain it unchanged through cancel/join, as with drive_state_preparation. */
#ifndef XENOVA_DRIVE_STATE_TRANSFER_H
#define XENOVA_DRIVE_STATE_TRANSFER_H
#include "preparation.h"

struct drive_maneuver_copy {
 struct xm_state_snapshot value;
 struct drive_state_preparation prepared;
 char copy_message_id[XM_UUID_LEN+1];
};

static inline int drive_apply_maneuver(struct drive_maneuver_copy *out,
 const struct xm_envelope *envelope,struct xm_span payload,
 const struct xm_vehicle_control *control,const char *receiver_vm,
 const struct drive_state_context *target,const struct xm_state_context *current,
 const char *const *segments,size_t count,uint64_t now,uint64_t deadline)
{
 if(!out||!envelope||!control||!receiver_vm||!target||!current||!segments||count>64)return -EINVAL;
 if(out->prepared.phase!=DRIVE_STATE_EMPTY)return -EBUSY;
 struct xm_state_snapshot value;
 if(xm_state_snapshot_parse(payload,&value))return -EINVAL;
 if(!xm_span_eq(envelope->type,"app.state.snapshot")||!strcmp(control->active_vm_id,receiver_vm)||
    !xm_span_eq(envelope->vm_id,control->active_vm_id)||
    !xm_span_eq(envelope->app_id,control->active_app)||
    value.source_generation!=control->generation)return -EXDEV;
 if(value.context.frame!=current->frame||value.context.configuration!=current->configuration||
    value.context.maneuver!=current->maneuver||strcmp(value.context.odom_epoch,current->odom_epoch)||
    target->frame!=current->frame||target->configuration!=current->configuration||target->maneuver!=current->maneuver)return -EXDEV;
 bool found=false;
 for(size_t i=0;i<count;i++)if(segments[i]&&!strcmp(segments[i],value.merge_segment))found=true;
 if(!found)return -ENOENT;
 struct drive_state_source source={.generation=value.source_generation};
 if(xm_span_copy(source.vm,sizeof(source.vm),envelope->vm_id)<0||
    xm_span_copy(source.instance,sizeof(source.instance),envelope->instance_id)<0||
    xm_span_copy(out->copy_message_id,sizeof(out->copy_message_id),envelope->message_id)<0)return -EINVAL;
 memcpy(source.execution,value.execution,sizeof(source.execution));
 if(!drive_state_uuid(out->copy_message_id))return -EINVAL;
 int rc=drive_state_begin(&out->prepared,target,now,deadline);
 if(rc)return rc;
 rc=drive_state_publish(&out->prepared,target,now,DRIVE_STATE_HANDOFF,value.state_revision,&source,true);
 if(rc)return rc;
 out->value=value;
 return 0;
}
#endif
