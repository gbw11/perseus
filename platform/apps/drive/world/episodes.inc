/* Shared structure and original-fact validation. Included by world.c. */
static bool zero32(const uint8_t *p){for(unsigned i=0;i<32;i++)if(p[i])return false;return true;}
static bool core_inactive(const xenova_drive_EpisodeCore *c){return c->state<2&&!c->phase;}
static bool progress_zero(const xenova_drive_EpisodeProgress *p){return !p->used_travel_ticks&&!p->last_progress_pi_us&&!p->vehicle_sequence&&!p->station_lower_m&&!p->remaining_upper_m&&!p->revision;}
static enum dw_result ep_progress(const xenova_drive_EpisodeProgress *p){return isfinite(p->station_lower_m)&&nonnegative(p->remaining_upper_m)?DW_OK:DW_NUMERIC;}
enum dw_result dw_episode_source_validate(const xenova_drive_EpisodeSourceContext *s){
 if(!typed_valid(s,xenova_drive_EpisodeSourceContext_fields,0))return DW_ARRAY;
 return source_uuid_text(s->mapper_boot)&&source_uuid_text(s->mapper_instance)&&source_uuid_text(s->map_epoch)&&source_uuid_text(s->frame_epoch)&&source_uuid_text(s->odom_epoch)&&source_uuid_text(s->clock_epoch)&&positive63(s->reference_revision)&&positive63(s->world_sequence)&&positive63(s->observation_sequence)?DW_OK:DW_SOURCE;
}
enum dw_result dw_episode_core_validate(const xenova_drive_EpisodeCore *c){
 if(!typed_valid(c,xenova_drive_EpisodeCore_fields,0))return DW_ARRAY;
 if(c->state>2||c->phase>5||c->policy>2||c->progress_mode>2||c->blocker_count>8)return DW_ARRAY;
 if(!dw_pose_finite(c->phase_goal)||!nonnegative(c->blocker_position_bound_m)||!nonnegative(c->max_travel_m))return DW_NUMERIC;
 if(core_inactive(c))return !c->revision&&!c->phase_revision&&!c->home_lane_id&&!c->target_lane_id&&!c->policy&&!c->blocker_count&&!c->blocker_position_bound_m&&!c->blocker_world_sequence&&!c->blocker_observation_sequence&&nil_uuid(c->episode_id.bytes)&&nil_uuid(c->birth_snapshot_id.bytes)&&!c->basis_revision&&!c->basis_prefix_count&&zero32(c->basis_prefix_sha256.bytes)&&zero32(c->source_geometry_sha256.bytes)&&!c->origin_vehicle_sequence&&!c->started_pi_us&&!c->deadline_pi_us&&!c->phase_started_pi_us&&!c->phase_deadline_pi_us&&!c->left_baseline_ticks&&!c->right_baseline_ticks&&nil_uuid(c->meter_epoch.bytes)&&!c->meter_baseline_total_ticks&&!c->max_travel_m&&!c->progress_mode&&!c->phase_goal.x&&!c->phase_goal.y&&!c->phase_goal.yaw&&!c->stop_wait&&!c->stop_wait_started_pi_us?DW_OK:DW_ARRAY;
 if(!c->state||!c->phase||(c->state==1)!=(c->phase==5)||!positive63(c->revision)||!positive63(c->phase_revision)||!c->home_lane_id||!c->target_lane_id||c->home_lane_id==c->target_lane_id||!c->policy||!c->progress_mode||!positive63(c->basis_revision)||c->basis_prefix_count<2||c->basis_prefix_count>64||!positive63(c->origin_vehicle_sequence)||!positive63(c->blocker_world_sequence)||!positive63(c->blocker_observation_sequence)||nil_uuid(c->episode_id.bytes)||nil_uuid(c->birth_snapshot_id.bytes)||nil_uuid(c->meter_epoch.bytes)||zero32(c->basis_prefix_sha256.bytes)||zero32(c->source_geometry_sha256.bytes)||!(c->max_travel_m>0)||c->max_travel_m>6||c->blocker_count<3)return DW_SOURCE;
 if(!simple(c->blocker,c->blocker_count,true))return DW_GEOMETRY;
 bool pos=true,neg=true;for(size_t i=0;i<c->blocker_count;i++){float turn=cross(c->blocker[i],c->blocker[(i+1)%c->blocker_count],c->blocker[(i+2)%c->blocker_count]);pos&=turn>1e-6f;neg&=turn< -1e-6f;}
 if(!pos&&!neg)return DW_GEOMETRY;
 if((c->started_pi_us||c->deadline_pi_us||c->phase_started_pi_us||c->phase_deadline_pi_us)&&(!positive63(c->started_pi_us)||!positive63(c->deadline_pi_us)||!positive63(c->phase_started_pi_us)||!positive63(c->phase_deadline_pi_us)||c->started_pi_us>c->phase_started_pi_us||c->phase_started_pi_us>=c->phase_deadline_pi_us||c->phase_deadline_pi_us>c->deadline_pi_us||c->deadline_pi_us-c->started_pi_us>40000000||c->phase_deadline_pi_us-c->phase_started_pi_us>10000000))return DW_TIME;
 return c->stop_wait!=(c->stop_wait_started_pi_us>0)?DW_TIME:DW_OK;
}
enum dw_result dw_episode_snapshot_validate(const xenova_drive_EpisodeSnapshot *s){
 if(!typed_valid(s,xenova_drive_EpisodeSnapshot_fields,0))return DW_ARRAY;
 enum dw_result rc=dw_episode_core_validate(&s->core);if(rc)return rc;rc=ep_progress(&s->progress);if(rc)return rc;
 const xenova_drive_EpisodeAcceptanceRef *r=&s->acceptance;uint8_t hash[32];
 if(!s->core.state||!positive63(r->registry_revision)||nil_uuid(r->registry_epoch.bytes)||!dw_episode_core_hash(&s->core,hash)||memcmp(hash,r->accepted_core_sha256.bytes,32))return DW_SOURCE;
 bool initial=nil_uuid(r->request_nonce.bytes);
 if(initial){if(!zero32(r->request_sha256.bytes)||r->request_sequence||!nil_uuid(r->origin_vm.bytes)||!nil_uuid(r->origin_execution.bytes)||!nil_uuid(r->origin_app_instance.bytes)||r->origin_generation||r->origin_grant_revision)return DW_SOURCE;}
 else if(zero32(r->request_sha256.bytes)||!positive63(r->request_sequence)||!r->origin_generation||!positive63(r->origin_grant_revision)||nil_uuid(r->origin_vm.bytes)||nil_uuid(r->origin_execution.bytes)||nil_uuid(r->origin_app_instance.bytes))return DW_SOURCE;
 if(core_inactive(&s->core))return initial&&progress_zero(&s->progress)?DW_OK:DW_SOURCE;
 if(initial||!positive63(s->core.started_pi_us)||!positive63(s->progress.vehicle_sequence)||!positive63(s->progress.revision)||s->progress.vehicle_sequence<s->core.origin_vehicle_sequence||(s->progress.last_progress_pi_us&&(s->progress.last_progress_pi_us<s->core.started_pi_us||s->progress.last_progress_pi_us>s->core.deadline_pi_us)))return DW_TIME;
 return DW_OK;
}
enum dw_result dw_episode_request_validate(const xenova_drive_EpisodeRequest *r){
 if(!typed_valid(r,xenova_drive_EpisodeRequest_fields,0))return DW_ARRAY;
 if(r->major!=1||r->action<1||r->action>3)return DW_VERSION;
 enum dw_result rc=dw_episode_core_validate(&r->candidate);if(rc)return rc;rc=ep_progress(&r->progress);if(rc)return rc;rc=dw_episode_source_validate(&r->source);if(rc)return rc;
 if(!source_uuid_text(r->vm)||!source_uuid_text(r->execution)||!source_uuid_text(r->app_instance)||!positive63(r->request_sequence)||!r->control_generation||!positive63(r->mission_revision)||!positive63(r->expected_grant_revision)||!positive63(r->completed_guest_us)||!positive63(r->phase_started_guest_us)||!positive63(r->valid_until_pi_us)||!positive63(r->clock_probe_sequence)||!positive63(r->vehicle_sequence)||!positive63(r->plan_revision)||!positive63(r->phase_revision)||!positive63(r->calculation_sequence)||nil_uuid(r->request_nonce.bytes)||nil_uuid(r->mission_id.bytes)||nil_uuid(r->expected_registry_epoch.bytes)||!positive63(r->expected_registry_revision)||zero32(r->clock_reply_sha256.bytes)||zero32(r->plan_sha256.bytes)||nil_uuid(r->phase_state_message_id.bytes)||!r->candidate.state||r->candidate.phase_revision!=r->phase_revision||(r->action==1&&(!positive63(r->began_guest_us)||r->began_guest_us>r->phase_started_guest_us))||(r->action!=1&&r->began_guest_us)||r->phase_started_guest_us>r->completed_guest_us||!scene_pair(r->scene_admission_id.bytes,r->scene_revision)||r->candidate.origin_vehicle_sequence>r->vehicle_sequence||((r->action==3)!=(r->candidate.state==1&&r->candidate.phase==5)))return DW_SOURCE;
 return DW_OK;
}
enum dw_result dw_episode_receipt_validate(const xenova_drive_EpisodeReceipt *r){
 if(!typed_valid(r,xenova_drive_EpisodeReceipt_fields,0))return DW_ARRAY;
 if(r->major!=1||r->result<1||r->result>5||r->reason>31||r->has_snapshot!=(r->result==1))return DW_VERSION;
 if(!positive63(r->request_sequence)||!positive63(r->registry_revision)||nil_uuid(r->request_nonce.bytes)||nil_uuid(r->registry_epoch.bytes)||zero32(r->request_sha256.bytes))return DW_SOURCE;
 if(r->has_snapshot){enum dw_result rc=dw_episode_snapshot_validate(&r->snapshot);if(rc)return rc;const xenova_drive_EpisodeAcceptanceRef *a=&r->snapshot.acceptance;
  if(a->request_sequence!=r->request_sequence||a->registry_revision!=r->registry_revision||memcmp(a->request_nonce.bytes,r->request_nonce.bytes,16)||memcmp(a->registry_epoch.bytes,r->registry_epoch.bytes,16)||memcmp(a->request_sha256.bytes,r->request_sha256.bytes,32))return DW_SOURCE;}
 return DW_OK;
}
enum dw_result dw_episode_status_validate(const xenova_drive_EpisodeStatus *s){
 if(!typed_valid(s,xenova_drive_EpisodeStatus_fields,0))return DW_ARRAY;
 if(s->major!=1||s->state>2||s->commit_state>2||(!s->state&&s->commit_state))return DW_VERSION;
 if(s->state&&(!positive63(s->registry_revision)||!positive63(s->valid_until_pi_us)||nil_uuid(s->registry_epoch.bytes)||zero32(s->accepted_core_sha256.bytes)))return DW_SOURCE;
 if(s->state==2&&(nil_uuid(s->episode_id.bytes)||!positive63(s->episode_revision)))return DW_SOURCE;
 if(s->commit_state==2&&(nil_uuid(s->state_message_id.bytes)||zero32(s->state_sha256.bytes)||!positive63(s->state_revision)||!positive63(s->plan_revision)||!positive63(s->phase_revision)))return DW_SOURCE;
 return s->has_receipt?dw_episode_receipt_validate(&s->receipt):DW_OK;
}
enum dw_result dw_episode_initial_none(xenova_drive_EpisodeSnapshot *out,const xenova_drive_EpisodeStatus *s){
 if(!out||dw_episode_status_validate(s)||s->state!=1||!nil_uuid(s->episode_id.bytes)||s->episode_revision)return DW_SOURCE;
 xenova_drive_EpisodeSnapshot n={0};n.core.state=1;n.core.episode_id.size=n.core.birth_snapshot_id.size=n.core.meter_epoch.size=16;n.core.basis_prefix_sha256.size=n.core.source_geometry_sha256.size=32;
 xenova_drive_EpisodeAcceptanceRef *r=&n.acceptance;r->registry_epoch.size=16;memcpy(r->registry_epoch.bytes,s->registry_epoch.bytes,16);r->registry_revision=s->registry_revision;r->request_nonce.size=r->origin_vm.size=r->origin_execution.size=r->origin_app_instance.size=16;r->request_sha256.size=r->accepted_core_sha256.size=32;
 if(!dw_episode_core_hash(&n.core,r->accepted_core_sha256.bytes)||memcmp(r->accepted_core_sha256.bytes,s->accepted_core_sha256.bytes,32))return DW_SOURCE;
 *out=n;return DW_OK;
}
static enum dw_result ep_stop(const xenova_drive_StopFacts *s){
 if(s->state>3||s->producer_role>2)return DW_VERSION;
 if(!nonnegative(s->left_speed_bound_mps)||!nonnegative(s->right_speed_bound_mps)||!nonnegative(s->yaw_rate_bound_rps))return DW_NUMERIC;
 if(!s->state)return DW_OK;
 if(!source_uuid_text(s->pi_session)||!source_uuid_text(s->device_session)||!positive63(s->command_sequence)||!positive63(s->valid_until_pi_us))return DW_SOURCE;
 bool pi_source=nil_uuid(s->source_vm.bytes)&&nil_uuid(s->source_execution.bytes)&&nil_uuid(s->source_app_instance.bytes)&&!s->calculation_sequence;
 if(!s->producer_role&&!pi_source)return DW_SOURCE;
 if(!pi_source&&(!s->control_generation||!positive63(s->calculation_sequence)||nil_uuid(s->source_vm.bytes)||nil_uuid(s->source_execution.bytes)||nil_uuid(s->source_app_instance.bytes)))return DW_SOURCE;
 if(s->state>=2&&(!positive63(s->applied_report_sequence)||!positive63(s->applied_source_sample)||!positive63(s->applied_pi_lower_us)||!positive63(s->applied_pi_upper_us)||s->applied_pi_lower_us>s->applied_pi_upper_us))return DW_TIME;
 if(s->state==3&&(!positive63(s->stationary_first_sequence)||!positive63(s->stationary_last_sequence)||!positive63(s->stationary_started_pi_us)||!positive63(s->stationary_observed_pi_us)||s->stationary_first_sequence>s->stationary_last_sequence||s->applied_pi_upper_us>s->stationary_started_pi_us||s->stationary_started_pi_us>=s->stationary_observed_pi_us||s->stationary_observed_pi_us>=s->valid_until_pi_us))return DW_TIME;
 return DW_OK;
}
enum dw_result dw_control_facts_validate(const xenova_drive_DriveControlFacts *f,const dw_vehicle *v){
 if(!f||!v||!typed_valid(f,xenova_drive_DriveControlFacts_fields,0))return DW_ARRAY;
 const xenova_drive_PiAbsoluteMeter *m=&f->meter;if(f->major!=1)return DW_VERSION;
 if(!isfinite(m->metres_per_tick)||m->metres_per_tick<0)return DW_NUMERIC;
 if(m->vehicle_sequence!=v->sequence||(m->valid&&(nil_uuid(m->meter_epoch.bytes)||!m->vehicle_sequence||m->metres_per_tick<=0||v->quality!=3||!v->has_pi_session)))return DW_SOURCE;
 if((m->valid||(f->has_stop&&f->stop.state))&&!positive63(f->valid_until_pi_us))return DW_TIME;
 if(f->has_stop){enum dw_result rc=ep_stop(&f->stop);if(rc)return rc;const xenova_drive_StopFacts *s=&f->stop;
  if(s->state&&(!v->has_pi_session||strcmp(s->pi_session,v->pi_session)||strcmp(s->device_session,v->device_session)||s->stationary_last_sequence>v->sequence||s->stationary_observed_pi_us>v->pi_sample_us))return DW_SOURCE;}
 return DW_OK;
}
