/* SPDX-License-Identifier: Apache-2.0 */
/* One Jetson map socket, one Pi state socket, and small vehicle input on the
 * existing Pi control owner. Blocking one connection never waits in a tracker. */
#include "lattice_input.h"
#include <xenova/sha256.h>
#include "lattice_app.h"
#include "pb_encode.h"
#include <zephyr/sys/atomic.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <limits.h>

static struct drive_engine *engine;
#if defined(CONFIG_XENOVA_DRIVE_INPUT_DIAGNOSTICS)
#include <zephyr/logging/log.h>
LOG_MODULE_REGISTER(drive_input_diag, LOG_LEVEL_INF);
/* State-owner-only microsecond measurements. known is a presence mask, so a
 * missing clock read is not observed zero. no_reply counts closed probes only.
 * IDs are the original outgoing probe, never untrusted reply strings. Intent
 * is not in ClockProbe: the outer query guard covers its current context only.
 * Slot ownership protects a one-shot shell
 * copy without waiting or borrowing a product/clock mutex. A row is immutable
 * while published/claimed; no seqlock read of racing non-atomic C fields. */
enum {CT_EB=1U,CT_EE=2U,CT_TB=4U,CT_TE=8U,CT_RX=16U,CT_PI=32U,
 CT_PB=64U,CT_PE=128U,CT_PREV=256U,CT_SETUP=512U,CT_POLL=1024U,
 CT_GAP=2048U,CT_OTHER=4096U,CT_ENC_RC=8192U,CT_TX_RC=16384U,CT_REPLY_RC=32768U,CT_WIDTH=65536U,CT_ACTIVE=131072U};
_Static_assert(ATOMIC_INT_LOCK_FREE==2,"diagnostic publication must not use a blocking atomic fallback");
enum {CT_PROBES,CT_NO_REPLY,CT_UNMATCHED,CT_ENCODE_FAILED,CT_TX_FAILED,CT_RECORDS,CT_SKIPPED,CT_CLOCK_ERRORS,CT_COUNTERS};
struct ct_row {
 uint64_t record,sequence,g0,live_sequence,live_g0,reply_sequence,echo_g0;
 uint64_t eb,ee,tb,te,g3,p1,p2,poll_begin,poll_end,previous_poll_end;
 uint64_t setup_max,poll_max,gap_max,other_total,other_max;
 int64_t width;
 unsigned known,setup_calls,poll_calls,other_calls,phase,match,event,other_kind;
 int encode_rc,tx_rc,reply_rc,clock_rc,setup_rc,poll_rc,diag_rc;
 char vm[37],execution[37],app_instance[37];
};
struct ct_slot {atomic_uint state;struct ct_row row;};
struct ct_tx {uint64_t eb,ee,tb,te;unsigned known,kind;int encode_rc;};
static struct ct_slot ct_slots[8];
static struct ct_row ct_active,ct_pending;
static bool ct_open,ct_reply_seen,ct_wire_failed,ct_pending_ready,ct_polling;
static unsigned ct_first,ct_tail,ct_peak_used;
static uint64_t ct_peak[2],ct_poll_begin,ct_previous_end;
static bool ct_poll_begin_valid,ct_previous_end_valid;
static atomic_uint ct_counts[CT_COUNTERS];
static atomic_int ct_first_clock_rc;
static unsigned ct_bump(atomic_uint *value){unsigned n=atomic_load(value);if(n<UINT_MAX)atomic_store(value,++n);return n;}
static void ct_error(int rc){
 ct_bump(&ct_counts[CT_CLOCK_ERRORS]);
 if(!atomic_load(&ct_first_clock_rc))atomic_store(&ct_first_clock_rc,rc);
 if(ct_open&&!ct_active.diag_rc)ct_active.diag_rc=rc;
}
static bool ct_stamp(uint64_t *value){
 uint64_t now=0;int rc=xdiag_clock_us(&now);if(rc){ct_error(rc);return false;}*value=now;return true;
}
static bool ct_span(uint64_t begin,uint64_t end,uint64_t *span){
 if(end<begin){ct_error(-ERANGE);return false;}*span=end-begin;return true;
}
static void ct_increment(unsigned *value){if(*value<UINT_MAX)++*value;}
static bool ct_store(unsigned index,const struct ct_row *row){
 unsigned expected=atomic_load_explicit(&ct_slots[index].state,memory_order_acquire);
 if((expected!=0&&expected!=2)||!atomic_compare_exchange_strong_explicit(&ct_slots[index].state,&expected,1,memory_order_acq_rel,memory_order_acquire)){
  ct_bump(&ct_counts[CT_SKIPPED]);return false;
 }
 ct_slots[index].row=*row;
 atomic_store_explicit(&ct_slots[index].state,2,memory_order_release);return true;
}
#if defined(CONFIG_SHELL)
static int ct_read(unsigned index,struct ct_row *row){
 unsigned expected=2;
 if(!atomic_compare_exchange_strong_explicit(&ct_slots[index].state,&expected,3,memory_order_acq_rel,memory_order_acquire))return expected==0?-ENODATA:-EBUSY;
 *row=ct_slots[index].row;
 atomic_store_explicit(&ct_slots[index].state,2,memory_order_release);return 0;
}
#endif
static void ct_publish(struct ct_row *row){
 if(atomic_load(&ct_counts[CT_RECORDS])==UINT_MAX)return;
 row->record=ct_bump(&ct_counts[CT_RECORDS]);
 if(ct_first<2)(void)ct_store(ct_first++,row);
 (void)ct_store(2+ct_tail,row);ct_tail=(ct_tail+1)%4;
 if((row->known&CT_WIDTH)&&row->width>=0){
  unsigned index=ct_peak_used<2?ct_peak_used:(ct_peak[0]<=ct_peak[1]?0:1);
  if(ct_peak_used<2||(uint64_t)row->width>ct_peak[index]){
   if(ct_store(6+index,row)){ct_peak[index]=(uint64_t)row->width;if(ct_peak_used<2)ct_peak_used++;}
  }
 }
}
static void ct_binary_begin(const xenova_drive_DriveLink *value,struct ct_tx *tx){
 memset(tx,0,sizeof(*tx));
 tx->kind=value->which_payload==xenova_drive_DriveLink_probe_tag?1:
  value->which_payload==xenova_drive_DriveLink_applied_tag?2:3;
 if(tx->kind!=1)return;
 if(ct_open&&!ct_reply_seen&&!ct_wire_failed)ct_bump(&ct_counts[CT_NO_REPLY]);
 memset(&ct_active,0,sizeof(ct_active));ct_open=true;ct_reply_seen=ct_wire_failed=false;
 const xenova_drive_ClockProbe *probe=&value->payload.probe;
 ct_active.sequence=probe->sequence;ct_active.g0=probe->guest_send_us;
 ct_active.known=CT_ACTIVE;
 memcpy(ct_active.vm,probe->vm,sizeof(ct_active.vm));
 memcpy(ct_active.execution,probe->execution,sizeof(ct_active.execution));
 memcpy(ct_active.app_instance,probe->app_instance,sizeof(ct_active.app_instance));
 ct_bump(&ct_counts[CT_PROBES]);
}
static void ct_binary_end(const struct ct_tx *tx,int rc){
 if(!ct_open)return;
 if(tx->kind==1){
  ct_active.eb=tx->eb;ct_active.ee=tx->ee;ct_active.tb=tx->tb;ct_active.te=tx->te;
  ct_active.known|=tx->known;ct_active.encode_rc=tx->encode_rc;ct_active.tx_rc=rc;
  if(rc){ct_wire_failed=true;ct_bump(&ct_counts[tx->known&CT_TX_RC?CT_TX_FAILED:CT_ENCODE_FAILED]);ct_active.event=tx->known&CT_TX_RC?3:2;ct_publish(&ct_active);}
 }else{
  ct_increment(&ct_active.other_calls);uint64_t elapsed;
  unsigned finish=tx->known&CT_TX_RC?CT_TE:CT_EE;
  if((tx->known&(CT_EB|finish))==(CT_EB|finish)&&ct_span(tx->eb,finish==CT_TE?tx->te:tx->ee,&elapsed)){
   ct_active.known|=CT_OTHER;
   if(UINT64_MAX-ct_active.other_total<elapsed)ct_active.other_total=UINT64_MAX;else ct_active.other_total+=elapsed;
   if(elapsed>=ct_active.other_max){ct_active.other_max=elapsed;ct_active.other_kind=tx->kind;}
  }
 }
}
static void ct_reply(unsigned phase,int rc,uint64_t sequence,uint64_t echo,uint64_t live_sequence,uint64_t live_g0,
 uint64_t p1,uint64_t p2,uint64_t g3,int clock_rc){
 bool matches=ct_open&&sequence==ct_active.sequence&&echo==ct_active.g0;
 if(matches){ct_pending=ct_active;ct_reply_seen=true;}else{
  memset(&ct_pending,0,sizeof(ct_pending));ct_bump(&ct_counts[CT_UNMATCHED]);
  if(ct_open){ct_pending.sequence=ct_active.sequence;ct_pending.g0=ct_active.g0;ct_pending.known|=CT_ACTIVE;
   memcpy(ct_pending.vm,ct_active.vm,sizeof(ct_pending.vm));memcpy(ct_pending.execution,ct_active.execution,sizeof(ct_pending.execution));
   memcpy(ct_pending.app_instance,ct_active.app_instance,sizeof(ct_pending.app_instance));}
 }
 ct_pending.event=matches?1:4;ct_pending.phase=phase;ct_pending.reply_rc=rc;ct_pending.clock_rc=clock_rc;
 ct_pending.reply_sequence=sequence;ct_pending.echo_g0=echo;ct_pending.live_sequence=live_sequence;ct_pending.live_g0=live_g0;
 ct_pending.p1=p1;ct_pending.p2=p2;ct_pending.g3=g3;ct_pending.known|=CT_PI|CT_REPLY_RC;
 if(!clock_rc)ct_pending.known|=CT_RX;
 ct_pending.match=matches?1:0;
 if(matches&&!clock_rc&&phase>=2&&phase<=5&&g3>=echo&&p2>=p1&&g3-echo<=INT64_MAX&&p2-p1<=INT64_MAX){
  ct_pending.match|=2;ct_pending.width=(int64_t)(g3-echo)-(int64_t)(p2-p1);ct_pending.known|=CT_WIDTH;
 }
 if(ct_polling&&ct_poll_begin_valid){ct_pending.poll_begin=ct_poll_begin;ct_pending.known|=CT_PB;}
 if(ct_previous_end_valid){ct_pending.previous_poll_end=ct_previous_end;ct_pending.known|=CT_PREV;}
 ct_pending_ready=true;
}
static int ct_poll(struct xm_zclient *client){
 ct_polling=true;ct_poll_begin_valid=ct_stamp(&ct_poll_begin);
 if(ct_open&&ct_poll_begin_valid&&ct_previous_end_valid){uint64_t elapsed;
  if(ct_span(ct_previous_end,ct_poll_begin,&elapsed)){ct_active.known|=CT_GAP;if(elapsed>ct_active.gap_max)ct_active.gap_max=elapsed;}
 }
 int rc=xm_zclient_poll(client,0);uint64_t end=0;bool valid=ct_stamp(&end);ct_polling=false;
 if(ct_open){ct_increment(&ct_active.poll_calls);ct_active.poll_rc=rc;
  uint64_t elapsed;if(valid&&ct_poll_begin_valid&&ct_span(ct_poll_begin,end,&elapsed)){
   ct_active.known|=CT_POLL;if(elapsed>ct_active.poll_max)ct_active.poll_max=elapsed;
  }
 }
 if(ct_pending_ready){
  if(valid){ct_pending.poll_end=end;ct_pending.known|=CT_PE;}
  if(ct_pending.match&1){ct_pending.poll_max=ct_active.poll_max;ct_pending.poll_calls=ct_active.poll_calls;
   ct_pending.poll_rc=rc;ct_pending.known|=ct_active.known&(CT_POLL|CT_GAP);ct_pending.diag_rc=ct_active.diag_rc;}
  ct_publish(&ct_pending);ct_pending_ready=false;
 }
 ct_previous_end=end;ct_previous_end_valid=valid;return rc;
}
#if defined(CONFIG_SHELL)
#include <zephyr/shell/shell.h>
static int ct_shell(const struct shell *shell,size_t argc,char **argv){
 ARG_UNUSED(argv);if(argc!=1)return -EINVAL;
 shell_print(shell,"XD_CLOCK_TRANSPORT_META {\"schema\":1,\"slots\":8,\"row_bytes\":%u,\"probes\":%u,\"no_reply\":%u,\"unmatched\":%u,\"encode_failed\":%u,\"publish_failed\":%u,\"records\":%u,\"skipped\":%u,\"extra_clock_errors\":%u,\"first_extra_clock_rc\":%d,\"scope\":\"per-row original observation; outer query context is current only\"}",
  (unsigned)sizeof(struct ct_row),atomic_load(&ct_counts[CT_PROBES]),atomic_load(&ct_counts[CT_NO_REPLY]),atomic_load(&ct_counts[CT_UNMATCHED]),atomic_load(&ct_counts[CT_ENCODE_FAILED]),atomic_load(&ct_counts[CT_TX_FAILED]),atomic_load(&ct_counts[CT_RECORDS]),atomic_load(&ct_counts[CT_SKIPPED]),atomic_load(&ct_counts[CT_CLOCK_ERRORS]),atomic_load(&ct_first_clock_rc));
 for(unsigned index=0;index<8;index++){
  struct ct_row r;int rc=ct_read(index,&r);
  if(rc){shell_print(shell,"XD_CLOCK_TRANSPORT_ROW {\"slot\":%u,\"status\":\"%s\",\"row\":null}",index,rc==-ENODATA?"empty":"busy");continue;}
  char width[32];if(r.known&CT_WIDTH)snprintf(width,sizeof(width),"%lld",(long long)r.width);else strcpy(width,"null");
  shell_print(shell,"XD_CLOCK_TRANSPORT_ROW {\"slot\":%u,\"status\":\"valid\",\"record\":%llu,\"seq\":%llu,\"g0\":%llu,\"live_seq\":%llu,\"live_g0\":%llu,\"reply_seq\":%llu,\"echo\":%llu,\"event\":%u,\"phase\":%u,\"match\":%u,\"p\":%u,\"vm\":\"%.36s\",\"execution\":\"%.36s\",\"app_instance\":\"%.36s\"}",index,(unsigned long long)r.record,(unsigned long long)r.sequence,(unsigned long long)r.g0,(unsigned long long)r.live_sequence,(unsigned long long)r.live_g0,(unsigned long long)r.reply_sequence,(unsigned long long)r.echo_g0,r.event,r.phase,r.match,r.known,r.vm,r.execution,r.app_instance);
  shell_print(shell,"XD_CLOCK_TRANSPORT_TIME {\"slot\":%u,\"record\":%llu,\"eb\":%llu,\"ee\":%llu,\"tb\":%llu,\"te\":%llu,\"g3\":%llu,\"p1\":%llu,\"p2\":%llu,\"poll_begin\":%llu,\"poll_end\":%llu,\"previous_poll_end\":%llu,\"width\":%s}",index,(unsigned long long)r.record,(unsigned long long)r.eb,(unsigned long long)r.ee,(unsigned long long)r.tb,(unsigned long long)r.te,(unsigned long long)r.g3,(unsigned long long)r.p1,(unsigned long long)r.p2,(unsigned long long)r.poll_begin,(unsigned long long)r.poll_end,(unsigned long long)r.previous_poll_end,width);
  shell_print(shell,"XD_CLOCK_TRANSPORT_WORK {\"slot\":%u,\"record\":%llu,\"setup_max\":%llu,\"poll_max\":%llu,\"gap_max\":%llu,\"other_total\":%llu,\"other_max\":%llu,\"setup_calls\":%u,\"poll_calls\":%u,\"other_calls\":%u,\"other_kind\":%u,\"encode_rc\":%d,\"tx_rc\":%d,\"reply_rc\":%d,\"clock_rc\":%d,\"setup_rc\":%d,\"poll_rc\":%d,\"diag_rc\":%d}",index,(unsigned long long)r.record,(unsigned long long)r.setup_max,(unsigned long long)r.poll_max,(unsigned long long)r.gap_max,(unsigned long long)r.other_total,(unsigned long long)r.other_max,r.setup_calls,r.poll_calls,r.other_calls,r.other_kind,r.encode_rc,r.tx_rc,r.reply_rc,r.clock_rc,r.setup_rc,r.poll_rc,r.diag_rc);
 }
 return 0;
}
SHELL_CMD_REGISTER(xenova_clock,NULL,"Read bounded DriveLink clock transport metadata",ct_shell);
#endif
#define LINK_POLL(client) ct_poll(client)
static unsigned clock_diag_count,map_diag_count;
static unsigned vehicle_diag_count,receive_diag_count,receive_phase_count[5];
static uint64_t vehicle_phase_count[18];
static void vehicle_diagnostic(unsigned phase,int rc,int returned,const dw_vehicle *sample,bool mapped,uint64_t now,uint32_t source_error){
 uint64_t *count=&vehicle_phase_count[phase];if(*count<UINT64_MAX)++*count;
 /* Preserve room for later distinct causes instead of spending the quota on
  * the first burst of normal clock acquisition. Counters still saturate. */
 if(*count!=1||vehicle_diag_count>=16)return;
 unsigned index=++vehicle_diag_count;
 LOG_INF("XD_VEHICLE_DIAG n=%u cap=16 phase=%u rc=%d ret=%d sv=%u nv=%u seq=%llu pi=%llu err=%u now=%llu",
  index,phase,rc,returned,sample?1U:0U,mapped?1U:0U,
  (unsigned long long)(sample?sample->sequence:0),(unsigned long long)(sample?sample->pi_sample_us:0),
  sample?source_error:0,(unsigned long long)(mapped?now:0));
}
/* Enqueue and link owners share one atomic first-stage/boot quota.
 * The high byte saturates at8; low bits claim distinct phases exactly once. */
static atomic_uint applied_diag_state;
static void applied_diagnostic(unsigned phase,int rc,const xenova_drive_AppliedState *value){
 if(!phase||phase>8)return;
 unsigned old=atomic_load(&applied_diag_state),bit=1U<<(phase-1),index;
 do{
  if((old>>8)>=8||(old&bit))return;
  index=(old>>8)+1;
 }while(!atomic_compare_exchange_weak(&applied_diag_state,&old,((index<<8)|(old&255U)|bit)));
 LOG_INF("XD_APPLIED_DIAG n=%u cap=8 phase=%u rc=%d p=%u seq=%llu path=%llu applied=%u",
  index,phase,rc,value?1U:0U,(unsigned long long)(value?value->application_sequence:0),
  (unsigned long long)(value?value->path_revision:0),value&&value->applied?1U:0U);
}
static void receive_diagnostic(unsigned phase,int rc,unsigned before,unsigned after,uint64_t now){
 if(receive_diag_count>=8||receive_phase_count[phase]>=2)return;
 unsigned index=++receive_diag_count;++receive_phase_count[phase];
 LOG_INF("XD_RECEIVE_DIAG n=%u cap=8 phase=%u rc=%d world=%d before=%u after=%u published=%llu now=%llu",
  index,phase,rc,atomic_load(&engine->last_world_result),before,after,
  (unsigned long long)engine->maps.published,(unsigned long long)now);
}
static void clock_diagnostic(unsigned phase,int rc,uint64_t sequence,uint64_t echo,uint64_t live_sequence,uint64_t t0,
 uint64_t p1,uint64_t p2,uint64_t t4,int clock_rc){
 ct_reply(phase,rc,sequence,echo,live_sequence,t0,p1,p2,t4,clock_rc);
 if(clock_diag_count>=16)return;
 unsigned index=++clock_diag_count;
 uint64_t decision=0;int decision_rc=xdiag_clock_us(&decision);
 LOG_INF("XD_CLOCK_DIAG n=%u cap=16 seq=%llu g0=%llu p1=%llu p2=%llu g3=%llu dec=%llu crc=%d drc=%d rc=%d phase=%u",
  index,(unsigned long long)sequence,(unsigned long long)t0,
  (unsigned long long)p1,(unsigned long long)p2,(unsigned long long)t4,
  (unsigned long long)decision,clock_rc,decision_rc,rc,phase);
}
static void map_diagnostic(unsigned phase,int rc,int returned,size_t bytes){
 if(map_diag_count>=8)return;
 unsigned index=++map_diag_count;
 LOG_INF("XD_MAP_DIAG n=%u cap=8 bytes=%u rc=%d ret=%d phase=%u",
  index,(unsigned)bytes,rc,returned,phase);
}
#define CLOCK_DIAG(phase,rc,body,t4,clock_rc) \
 clock_diagnostic(phase,rc,(body)->probe.sequence,(body)->probe.guest_send_us,auto_probe_sequence,probe_sent, \
  (body)->pi_receive_us,(body)->pi_send_us,t4,clock_rc)
#define MAP_DIAG(phase,rc,returned,bytes) map_diagnostic(phase,rc,returned,bytes)
#define VEHICLE_DIAG(phase,rc,returned,sample,mapped,now,source_error) vehicle_diagnostic(phase,rc,returned,sample,mapped,now,source_error)
#define APPLIED_DIAG(phase,rc,value) applied_diagnostic(phase,rc,value)
#else
#define CLOCK_DIAG(...) ((void)0)
#define MAP_DIAG(...) ((void)0)
#define VEHICLE_DIAG(...) ((void)0)
#define APPLIED_DIAG(...) ((void)0)
#define LINK_POLL(client) xm_zclient_poll(client,0)
#endif

#define VEHICLE_TOPIC "xenova/v1/vehicle/state.pb"
#define WORLD_TOPIC "xenova/v1/world/snapshot.pb"
extern const struct lp_config lp_selected_config;
extern const struct lp_assets lp_selected_assets;
static struct xm_zclient map_client,link_client;
/* Each synchronous polling owner alone changes its callback's adoption gate. */
static bool map_input_allowed,link_input_allowed;
static struct xt_bootstrap map_boot,link_boot;
static uint8_t map_wire[DW_WIRE_MAX],link_wire[DW_WIRE_MAX],send_wire[DW_WIRE_MAX],vehicle_wire[2048];
static xenova_drive_DriveLink incoming,outgoing;
struct state_slot {atomic_t state;dw_state copy;};
static struct state_slot copies[2];
static char in_topic[XM_TOPIC_MAX],out_topic[XM_TOPIC_MAX];
static struct k_thread map_thread,link_thread;
K_THREAD_STACK_DEFINE(map_stack,8192);
K_THREAD_STACK_DEFINE(link_stack,8192);
K_MUTEX_DEFINE(input_context_lock);
K_MUTEX_DEFINE(applied_lock);
struct guest_clock_slot {
 struct dl_clock pending,confirmed;
 xenova_drive_GuestClockReceipt receipt,sent_receipt;
 xenova_drive_DirectionGateWitness gate;dw_vehicle vehicle;
 uint64_t sequence,sent,gate_received_guest;
 bool pending_valid,confirmed_valid,sent_has_receipt,gate_available;
};
static struct guest_clock_slot guest_clocks[2];
static struct dw_trust latest_trust;
#define current_clock (guest_clocks[0].confirmed)
#define clock_available (guest_clocks[0].confirmed_valid)
#define auto_probe_sequence (guest_clocks[0].sequence)
#define probe_sent (guest_clocks[0].sent)
static char gateway[37];
static bool context_available;
static atomic_t stop,reason;
static bool map_started,link_started,map_joined,link_joined;
static xenova_drive_AppliedState applied;
static bool application_pending;
static uint64_t next_probe,next_snapshot;
static atomic_ullong received_vehicle;
static uint64_t accepted_maps,rejected_maps,rejected_links,rejected_vehicles;
static xenova_drive_MissionGrant current_mission;
static bool mission_available,budget_pending,budget_send_pending,budget_reply_available;
static xenova_drive_MissionBudgetRequest budget_request;
static xenova_drive_MissionBudgetReply budget_reply;
static uint8_t mission_wire[1024],grant_candidate[1024],budget_wire[512],budget_candidate[512],budget_sha256[32];
static size_t mission_wire_size,budget_wire_size;
static uint32_t control_generation;
static char active_vm[37];
static xenova_drive_EpisodeStatus current_episode;
static xenova_drive_DriveControlFacts current_facts;
static dw_vehicle facts_vehicle;
static bool episode_available,facts_available,episode_pending,episode_send_pending,episode_reply_available,episode_probe_started;
static xenova_drive_EpisodeRequest episode_request;
static xenova_drive_EpisodeReceipt episode_reply;
static uint8_t episode_wire[2048],episode_candidate[2048],episode_sha256[32];
static size_t episode_wire_size;
static uint8_t episode_mission_id[16];static uint64_t episode_mission_revision;
static atomic_t snapshot_requested,episode_probe_requested;
static void episode_pending_clear(void){
 episode_pending=episode_send_pending=episode_reply_available=episode_probe_started=false;
 episode_wire_size=0;atomic_set(&episode_probe_requested,0);atomic_set(&snapshot_requested,0);
}
#if defined(CONFIG_XENOVA_DRIVE_NATIVE_TEST)
static uint64_t clock_trace[16][4];static unsigned clock_trace_count;
#endif

static uint64_t clock_us(void){uint64_t now=0;if(xdiag_clock_us(&now))atomic_set(&reason,DW_TIME);return now;}
static bool stopped(void){return atomic_get(&stop)||xd_stopping();}
static int hex_digit(char c){return c>='0'&&c<='9'?c-'0':c>='a'&&c<='f'?c-'a'+10:-1;}
static bool bytes_hex(const uint8_t *bytes,size_t size,const char *text,bool uuid){
 if(strlen(text)!=(uuid?36:2*size))return false;
 size_t n=0;int upper=-1;
 for(size_t i=0;text[i];i++){
  if(uuid&&(i==8||i==13||i==18||i==23)){if(text[i]!='-')return false;continue;}
  int digit=hex_digit(text[i]);if(digit<0)return false;
  if(upper<0)upper=digit;else{if(n>=size||bytes[n++]!=(uint8_t)(upper*16+digit))return false;upper=-1;}
 }
 return n==size&&upper<0;
}
static int cache_grant(const xenova_drive_MissionGrant *grant,const struct xt_bootstrap *boot){
 if(dw_mission_grant_validate(grant)||strcmp(grant->vehicle,lp_selected_config.vehicle)||
    strcmp(grant->configuration,lp_selected_config.id)||strcmp(grant->calibration,lp_selected_config.calibration)||
    !bytes_hex(grant->primitive.bytes,32,lp_selected_assets.sha256,false)||
    !bytes_hex(grant->pi_host_boot.bytes,16,boot->identity.host_boot_id,true)||
    !bytes_hex(grant->gateway_instance.bytes,16,gateway,true))return -EPROTO;
 if(grant->control_generation!=control_generation||
    !bytes_hex(grant->owner_vm.bytes,16,active_vm,true))return -ESTALE;
 pb_ostream_t stream=pb_ostream_from_buffer(grant_candidate,sizeof(grant_candidate));
 if(!pb_encode(&stream,xenova_drive_MissionGrant_fields,grant))return -EMSGSIZE;
 if(mission_wire_size){
  bool same_mission=current_mission.mission_revision==grant->mission_revision&&
   !memcmp(current_mission.mission_id.bytes,grant->mission_id.bytes,16);
  if(same_mission&&grant->grant_revision<current_mission.grant_revision)return -ESTALE;
  bool same_key=same_mission&&current_mission.grant_revision==grant->grant_revision;
  bool same_owner=current_mission.control_generation==grant->control_generation&&
   !memcmp(current_mission.owner_vm.bytes,grant->owner_vm.bytes,16)&&
   !memcmp(current_mission.owner_execution.bytes,grant->owner_execution.bytes,16)&&
   !memcmp(current_mission.app_instance.bytes,grant->app_instance.bytes,16)&&
   !memcmp(current_mission.pi_host_boot.bytes,grant->pi_host_boot.bytes,16)&&
   !memcmp(current_mission.gateway_instance.bytes,grant->gateway_instance.bytes,16);
  /* A qualified warm owner may carry the original immutable mission budget.
   * Its owner binding changes; conflicting bytes in the same binding do not. */
  if(same_key&&same_owner){
   if(stream.bytes_written!=mission_wire_size||memcmp(grant_candidate,mission_wire,mission_wire_size))return -EPROTO;
   mission_available=true;return 0;
  }
  if(grant->issued_pi_us<current_mission.issued_pi_us)return -ESTALE;
 }
 if(mission_wire_size){
  episode_pending_clear();
  if(current_mission.mission_revision!=grant->mission_revision||memcmp(current_mission.mission_id.bytes,grant->mission_id.bytes,16)||
     current_mission.cancel_fence_revision!=grant->cancel_fence_revision)episode_available=false;
 }
 current_mission=*grant;memcpy(mission_wire,grant_candidate,stream.bytes_written);mission_wire_size=stream.bytes_written;
 mission_available=true;return 0;
}
static int cache_scene(const xenova_drive_SceneGrant *grant,const struct xt_bootstrap *boot){
 if(!engine)return -EAGAIN;
 if(dw_scene_grant_validate(grant))return -EPROTO;
 if(!context_available||!clock_available||strcmp(grant->pi_host_boot,boot->identity.host_boot_id)||
    strcmp(grant->gateway_instance,gateway)||strcmp(grant->vehicle,lp_selected_config.vehicle)||
    strcmp(grant->configuration,lp_selected_config.id)||strcmp(grant->calibration,lp_selected_config.calibration)||
    strcmp(grant->primitive,lp_selected_assets.sha256))return -EXDEV;
 uint64_t guest=clock_us();
 if(!guest||guest>INT64_MAX||guest>current_clock.expires_guest||
    (current_clock.upper>0&&(uint64_t)current_clock.upper>INT64_MAX-guest)||
    (int64_t)guest+current_clock.upper<0)return -EAGAIN;
 uint64_t now=(uint64_t)((int64_t)guest+current_clock.upper);
 enum dw_result source=dw_pool_refresh(&engine->maps,&latest_trust);
 if(source)return source==DW_BUSY?-EBUSY:-EPROTO;
 enum dw_result accepted=dw_pool_scene(&engine->maps,grant,now);
 return accepted==DW_OK||accepted==DW_STALE?0:accepted==DW_BUSY?-EBUSY:-EPROTO;
}
void dl_context(const struct xd_inputs *inputs){
 if(k_mutex_lock(&input_context_lock,K_NO_WAIT))return;
 uint32_t previous_generation=control_generation;
 bool valid=inputs->connected&&!inputs->control.error&&!strcmp(inputs->control.sender,"dom0-gateway")&&inputs->control.instance[0];
 if(!valid||strcmp(gateway,inputs->control.instance)){
  for(unsigned i=0;i<2;i++){guest_clocks[i].confirmed_valid=false;guest_clocks[i].pending_valid=false;guest_clocks[i].gate_available=false;guest_clocks[i].sent=0;}
  mission_available=false;mission_wire_size=0;gateway[0]=0;episode_available=facts_available=false;
  episode_pending_clear();
  if(engine)dw_scene_fence(&engine->maps);
 }
 context_available=valid;
 if(valid)strcpy(gateway,inputs->control.instance);
 struct xm_vehicle_control control;control_generation=0;active_vm[0]=0;
 if(valid&&!xm_vehicle_control_parse((struct xm_span){inputs->control.payload,inputs->control.length},&control)){
  control_generation=control.generation;strcpy(active_vm,control.active_vm_id);
 }
 if(previous_generation!=control_generation){
  episode_available=facts_available=false;episode_pending_clear();
 }
 k_mutex_unlock(&input_context_lock);
}
static bool same_receipt(const xenova_drive_GuestClockReceipt *a,const xenova_drive_GuestClockReceipt *b){
 return a->probe_sequence==b->probe_sequence&&a->guest_receive_us==b->guest_receive_us&&
  a->reply_sha256.size==32&&b->reply_sha256.size==32&&!memcmp(a->reply_sha256.bytes,b->reply_sha256.bytes,32);
}
static int clock_get_role(unsigned role,struct dl_clock *out,uint64_t *now_pi){
 if(!out||!now_pi||role>1)return -EINVAL;
 struct xt_bootstrap boot;if(xd_context_get(&boot))return -EAGAIN;
 uint64_t guest=clock_us();if(!guest||guest>INT64_MAX)return -EIO;
 if(k_mutex_lock(&input_context_lock,K_NO_WAIT))return -EBUSY;
 const struct guest_clock_slot *slot=&guest_clocks[role];
 const char *app=role?boot.manual_instance_id:boot.auto_instance_id;
 int rc=context_available&&slot->confirmed_valid&&guest<slot->confirmed.expires_guest&&
  !strcmp(slot->confirmed.vm,boot.identity.vm_id)&&!strcmp(slot->confirmed.execution,boot.identity.execution_id)&&
  !strcmp(slot->confirmed.app_instance,app)&&!strcmp(slot->confirmed.host_boot,boot.identity.host_boot_id)&&
  !strcmp(slot->confirmed.gateway,gateway)?0:-EAGAIN;
 if(!rc){*out=slot->confirmed;if(!role)out->trust=latest_trust;}
 k_mutex_unlock(&input_context_lock);
 if(!rc){
  if((out->upper>0&&(uint64_t)out->upper>INT64_MAX-guest)||(int64_t)guest+out->upper<0)return -ERANGE;
  *now_pi=(uint64_t)((int64_t)guest+out->upper);
 }
 return rc;
}
int dl_clock_get(struct dl_clock *out,uint64_t *now_pi){return clock_get_role(0,out,now_pi);}
int dl_manual_clock_get(struct dl_clock *out,uint64_t *now_pi){int rc=clock_get_role(1,out,now_pi);return !rc&&!out->acknowledged?-EAGAIN:rc;}
int dl_direction_get(bool manual,xenova_drive_DirectionGateWitness *out,uint64_t now){
 if(!out)return -EINVAL;
 unsigned role=manual?1:0;struct dl_clock clock;uint64_t current;
 int rc=clock_get_role(role,&clock,&current);if(rc)return rc;
 if(!clock.acknowledged)return -EAGAIN;
 if(now<current)now=current;
 if(k_mutex_lock(&input_context_lock,K_NO_WAIT))return -EBUSY;
 struct guest_clock_slot *slot=&guest_clocks[role];
 rc=slot->gate_available&&slot->gate.control_generation==control_generation&&slot->gate.producer_role==role+1&&
  slot->gate.valid_until_pi_us>now?0:-EAGAIN;
 if(!rc)*out=slot->gate;
 k_mutex_unlock(&input_context_lock);if(rc)return rc;
 dw_vehicle vehicle;if(!engine||dw_vehicle_latest(&engine->odometry,&vehicle))return -EAGAIN;
 if(!vehicle.has_pi_session||!vehicle.has_gateway_instance||strcmp(vehicle.gateway_instance,clock.gateway)||
    strcmp(out->pi_session,vehicle.pi_session)||strcmp(out->device_session,vehicle.device_session)||
    vehicle.sequence<out->vehicle_sequence||now<vehicle.pi_sample_us||
    now-vehicle.pi_sample_us+vehicle.clock_error_us>lp_selected_config.pose_age_us)return -EXDEV;
 return 0;
}
int dl_mission_get(xenova_drive_MissionGrant *out,uint64_t now){
 if(!out)return -EINVAL;
 if(k_mutex_lock(&input_context_lock,K_NO_WAIT))return -EBUSY;
 uint64_t guest=clock_us();
 int rc=!guest||guest>current_clock.expires_guest||!context_available||!clock_available||!mission_available||
  current_mission.control_generation!=control_generation||current_mission.issued_pi_us>now||
  !bytes_hex(current_mission.owner_vm.bytes,16,active_vm,true)||
  (current_mission.kind==2&&now>=current_mission.mission_deadline_pi_us)?-EAGAIN:0;
 if(!rc)*out=current_mission;
 k_mutex_unlock(&input_context_lock);return rc;
}
int dl_mission_for_local(xenova_drive_MissionGrant *out,uint64_t now){
 int rc=dl_mission_get(out,now);if(rc)return rc;
 struct xt_bootstrap boot;if(xd_context_get(&boot))return -EAGAIN;
 if(!out->admitted||(out->kind!=1&&out->kind!=2))return -EACCES;
 return bytes_hex(out->owner_vm.bytes,16,boot.identity.vm_id,true)&&
  bytes_hex(out->owner_execution.bytes,16,boot.identity.execution_id,true)&&
  bytes_hex(out->app_instance.bytes,16,boot.auto_instance_id,true)?0:-EXDEV;
}
int dl_budget_submit(const xenova_drive_MissionBudgetRequest *request,const uint8_t fingerprint[32]){
 if(!request||!fingerprint||dw_budget_request_validate(request))return -EINVAL;
 struct dl_clock mapping;uint64_t now;if(dl_clock_get(&mapping,&now))return -EAGAIN;
 struct xt_bootstrap boot;if(xd_context_get(&boot))return -EAGAIN;
 if(k_mutex_lock(&input_context_lock,K_NO_WAIT))return -EBUSY;
 pb_ostream_t stream=pb_ostream_from_buffer(budget_candidate,sizeof(budget_candidate));
 if(!pb_encode(&stream,xenova_drive_MissionBudgetRequest_fields,request)){k_mutex_unlock(&input_context_lock);return -EMSGSIZE;}
 int rc=0;
 if(budget_pending){
  if(stream.bytes_written!=budget_wire_size||memcmp(budget_candidate,budget_wire,budget_wire_size)||memcmp(fingerprint,budget_sha256,32))rc=-EBUSY;
  else if(!budget_reply_available)budget_send_pending=true;
 }else{
  uint64_t guest=clock_us(),lower=0;bool mapped=true;
  if(mapping.lower<0){uint64_t magnitude=(uint64_t)(-(mapping.lower+1))+1;if(magnitude>request->completed_guest_us)mapped=false;else lower=request->completed_guest_us-magnitude;}
  else if((uint64_t)mapping.lower>UINT64_MAX-request->completed_guest_us)mapped=false;
  else lower=request->completed_guest_us+(uint64_t)mapping.lower;
  if(!mission_available||!current_mission.admitted||current_mission.kind!=2||
   !bytes_hex(current_mission.owner_vm.bytes,16,boot.identity.vm_id,true)||
   !bytes_hex(current_mission.owner_execution.bytes,16,boot.identity.execution_id,true)||
   !bytes_hex(current_mission.app_instance.bytes,16,boot.auto_instance_id,true)||
   request->control_generation!=control_generation||request->control_generation!=current_mission.control_generation||
   request->mission_revision!=current_mission.mission_revision||request->expected_grant_revision!=current_mission.grant_revision||
   memcmp(request->mission_id.bytes,current_mission.mission_id.bytes,16)||
   memcmp(request->scene_admission_id.bytes,current_mission.scene_admission_id.bytes,16)||
   request->scene_revision!=current_mission.scene_revision||request->completed_guest_us>guest||!mapped||
   request->valid_until_pi_us<=now||request->valid_until_pi_us<lower||request->valid_until_pi_us-lower>250000)rc=-ESTALE;
  else{budget_request=*request;memcpy(budget_wire,budget_candidate,stream.bytes_written);budget_wire_size=stream.bytes_written;
   memcpy(budget_sha256,fingerprint,32);budget_pending=budget_send_pending=true;budget_reply_available=false;}
 }
 k_mutex_unlock(&input_context_lock);return rc;
}
int dl_budget_take(xenova_drive_MissionBudgetRequest *request,xenova_drive_MissionBudgetReply *reply){
 if(!request||!reply)return -EINVAL;
 if(k_mutex_lock(&input_context_lock,K_NO_WAIT))return -EBUSY;
 int rc=budget_pending&&budget_reply_available?0:-EAGAIN;
 if(!rc){*request=budget_request;*reply=budget_reply;budget_pending=budget_send_pending=budget_reply_available=false;}
 k_mutex_unlock(&input_context_lock);return rc;
}
void dl_snapshot_request(void){atomic_set(&snapshot_requested,1);}
int dl_episode_status(xenova_drive_EpisodeStatus *out,uint64_t now){
 if(!out)return -EINVAL;
 struct dl_clock mapping;uint64_t current;int rc=dl_clock_get(&mapping,&current);if(rc)return rc;
 if(!mapping.acknowledged)return -EAGAIN;
 if(now<current)now=current;
 if(k_mutex_lock(&input_context_lock,K_NO_WAIT))return -EBUSY;
 rc=!episode_available?-ENODATA:!mission_available||current_mission.mission_revision!=episode_mission_revision||
    memcmp(current_mission.mission_id.bytes,episode_mission_id,16)?-ESTALE:current_episode.valid_until_pi_us<=now?-ESTALE:0;
 if(!rc)*out=current_episode;
 k_mutex_unlock(&input_context_lock);return rc;
}
int dl_control_facts(xenova_drive_DriveControlFacts *out,uint64_t now){
 if(!out)return -EINVAL;
 struct dl_clock mapping;uint64_t current;int rc=dl_clock_get(&mapping,&current);if(rc)return rc;
 if(!mapping.acknowledged)return -EAGAIN;
 if(now<current)now=current;
 dw_vehicle original;
 if(k_mutex_lock(&input_context_lock,K_NO_WAIT))return -EBUSY;
 rc=!facts_available?-ENODATA:current_facts.valid_until_pi_us<=now?-ESTALE:0;
 if(!rc){*out=current_facts;original=facts_vehicle;}
 k_mutex_unlock(&input_context_lock);if(rc)return rc;
 dw_vehicle latest;if(!engine||dw_vehicle_latest(&engine->odometry,&latest))return -EAGAIN;
 if(!original.has_pi_session||!latest.has_pi_session||!latest.has_gateway_instance||
    strcmp(latest.gateway_instance,mapping.gateway)||strcmp(original.pi_session,latest.pi_session)||
    strcmp(original.device_session,latest.device_session)||strcmp(original.odom_epoch,latest.odom_epoch)||
    strcmp(original.calibration,latest.calibration)||strcmp(original.host_boot,latest.host_boot)||
    latest.sequence<original.sequence||now<original.pi_sample_us||
    now-original.pi_sample_us+original.clock_error_us>lp_selected_config.pose_age_us)return -ESTALE;
 return 0;
}
int dl_episode_submit(const xenova_drive_EpisodeRequest *request,const uint8_t fingerprint[32]){
 if(!request||!fingerprint||dw_episode_request_validate(request))return -EINVAL;
 uint8_t actual[32];if(!dw_episode_request_hash(request,actual)||memcmp(actual,fingerprint,32))return -EINVAL;
 struct dl_clock mapping;uint64_t now;if(dl_clock_get(&mapping,&now)||!mapping.acknowledged)return -EAGAIN;
 struct xt_bootstrap boot;if(xd_context_get(&boot))return -EAGAIN;
 if(k_mutex_lock(&input_context_lock,K_NO_WAIT))return -EBUSY;
 pb_ostream_t encoded=pb_ostream_from_buffer(episode_candidate,sizeof(episode_candidate));
 if(!pb_encode(&encoded,xenova_drive_EpisodeRequest_fields,request)){k_mutex_unlock(&input_context_lock);return -EMSGSIZE;}
 int rc=0;
 if(episode_pending){
  if(encoded.bytes_written!=episode_wire_size||memcmp(episode_candidate,episode_wire,episode_wire_size)||memcmp(fingerprint,episode_sha256,32))rc=-EBUSY;
  else if(!episode_reply_available&&request->valid_until_pi_us>now)episode_send_pending=true;
 }else{
  uint64_t guest=clock_us();
  if(!mission_available||!current_mission.admitted||request->control_generation!=control_generation||
     request->control_generation!=current_mission.control_generation||
     strcmp(request->vm,boot.identity.vm_id)||strcmp(request->execution,boot.identity.execution_id)||strcmp(request->app_instance,boot.auto_instance_id)||
     !bytes_hex(current_mission.owner_vm.bytes,16,boot.identity.vm_id,true)||
     !bytes_hex(current_mission.owner_execution.bytes,16,boot.identity.execution_id,true)||
     !bytes_hex(current_mission.app_instance.bytes,16,boot.auto_instance_id,true)||
     request->mission_revision!=current_mission.mission_revision||request->expected_grant_revision!=current_mission.grant_revision||
     request->cancel_fence_revision!=current_mission.cancel_fence_revision||memcmp(request->mission_id.bytes,current_mission.mission_id.bytes,16)||
     request->scene_revision!=current_mission.scene_revision||memcmp(request->scene_admission_id.bytes,current_mission.scene_admission_id.bytes,16)||
     !current_clock.acknowledged||request->clock_probe_sequence!=current_clock.probe_sequence||
     memcmp(request->clock_reply_sha256.bytes,current_clock.reply_sha256,32)||
     request->completed_guest_us>guest||request->valid_until_pi_us<=now)rc=-ESTALE;
  else{episode_request=*request;memcpy(episode_wire,episode_candidate,encoded.bytes_written);episode_wire_size=encoded.bytes_written;
   memcpy(episode_sha256,fingerprint,32);episode_pending=episode_send_pending=true;episode_reply_available=false;episode_probe_started=false;}
 }
 k_mutex_unlock(&input_context_lock);return rc;
}
int dl_episode_take(xenova_drive_EpisodeRequest *request,xenova_drive_EpisodeReceipt *reply){
 if(!request||!reply)return -EINVAL;
 if(k_mutex_lock(&input_context_lock,K_NO_WAIT))return -EBUSY;
 int rc=episode_pending&&episode_reply_available?0:-EAGAIN;
 if(!rc){*request=episode_request;*reply=episode_reply;episode_pending_clear();}
 k_mutex_unlock(&input_context_lock);return rc;
}
static int vehicle_received(void *unused,const uint8_t *bytes,size_t count,int64_t received){
 ARG_UNUSED(unused);ARG_UNUSED(received);
 dw_vehicle sample;enum dw_result decoded=dw_vehicle_decode(bytes,count,&sample);
#if defined(CONFIG_XENOVA_DRIVE_INPUT_DIAGNOSTICS)
 uint32_t source_error=decoded?0:sample.clock_error_us;
#endif
 struct dl_clock mapping;uint64_t now;
 struct xt_bootstrap boot;
#define VEHICLE_REJECT(phase,code,returned,value,mapped,at) do { \
 rejected_vehicles++;VEHICLE_DIAG(phase,code,returned,value,mapped,at,source_error);return returned; } while(0)
 /* The original short-circuit order and public errno remain unchanged. A
  * failed decode never lends uninitialized sample fields to diagnostics. */
 if(decoded)VEHICLE_REJECT(1,decoded,-EPROTO,NULL,false,0);
 int gate=dl_clock_get(&mapping,&now);
 if(gate)VEHICLE_REJECT(2,gate,-EPROTO,&sample,false,0);
 gate=xd_context_get(&boot);
 if(gate)VEHICLE_REJECT(3,gate,-EPROTO,&sample,true,now);
 if(strcmp(sample.host_boot,boot.identity.host_boot_id))VEHICLE_REJECT(4,-EPROTO,-EPROTO,&sample,true,now);
 if(!sample.has_gateway_instance)VEHICLE_REJECT(5,-EPROTO,-EPROTO,&sample,true,now);
 if(strcmp(sample.gateway_instance,mapping.gateway))VEHICLE_REJECT(6,-EPROTO,-EPROTO,&sample,true,now);
 if(strcmp(sample.vehicle,mapping.trust.vehicle))VEHICLE_REJECT(7,-EPROTO,-EPROTO,&sample,true,now);
 if(strcmp(sample.odom_epoch,mapping.trust.odom_epoch))VEHICLE_REJECT(8,-EPROTO,-EPROTO,&sample,true,now);
 if(strcmp(sample.calibration,mapping.trust.calibration))VEHICLE_REJECT(9,-EPROTO,-EPROTO,&sample,true,now);
 if(sample.pi_sample_us<=received_vehicle)VEHICLE_REJECT(10,-EPROTO,-EPROTO,&sample,true,now);
 if(now<sample.pi_sample_us)VEHICLE_REJECT(11,-EPROTO,-EPROTO,&sample,true,now);
 if(now-sample.pi_sample_us+sample.clock_error_us>lp_selected_config.pose_age_us)
  VEHICLE_REJECT(12,-EPROTO,-EPROTO,&sample,true,now);
 uint64_t width=(uint64_t)mapping.upper-(uint64_t)mapping.lower;
 if(width>UINT32_MAX-sample.clock_error_us)VEHICLE_REJECT(13,-ERANGE,-ERANGE,&sample,true,now);
 sample.clock_error_us+=(uint32_t)width;
 enum dw_result rc=dw_odom_add(&engine->odometry,&sample);
 if(rc==DW_EPOCH){
  /* A new epoch is already authorized by the current clock/trust reply.
   * Atomic reset invalidates old map anchors; it does not reset AUTO or MANUAL. */
  rc=dw_odom_reset(&engine->odometry);
  if(rc)VEHICLE_REJECT(15,rc,-EAGAIN,&sample,true,now);
  rc=dw_odom_add(&engine->odometry,&sample);
  if(rc)VEHICLE_REJECT(16,rc,-EAGAIN,&sample,true,now);
 }
 if(rc)VEHICLE_REJECT(14,rc,-EAGAIN,&sample,true,now);
 VEHICLE_DIAG(17,0,0,&sample,true,now,source_error);
 received_vehicle=sample.pi_sample_us;return 0;
#undef VEHICLE_REJECT
}
static int map_received(void *unused,const uint8_t *bytes,size_t count,int64_t received){
 ARG_UNUSED(unused);ARG_UNUSED(received);struct dl_clock mapping;uint64_t now;
 if(unused&&!*(const bool *)unused){rejected_maps++;return -EAGAIN;}
 int gate=dl_clock_get(&mapping,&now);
 if(gate){rejected_maps++;MAP_DIAG(1,gate,-EAGAIN,count);return -EAGAIN;}
 /* 같은 source의 정상 clock lease 갱신은 기존 reader/writer를 fence하지 않는다. */
 enum dw_result source=dw_pool_refresh(&engine->maps,&mapping.trust);
 if(source){rejected_maps++;MAP_DIAG(2,source,-EBUSY,count);return -EBUSY;}
 enum de_result rc=de_world(engine,bytes,count,now);
 if(rc){rejected_maps++;atomic_set(&reason,rc);MAP_DIAG(3,rc,-EPROTO,count);return -EPROTO;}
 accepted_maps++;MAP_DIAG(4,0,0,count);return 0;
}
static bool same_identity(const struct xt_bootstrap *a,const struct xt_bootstrap *b){
 return !memcmp(&a->identity,&b->identity,sizeof(a->identity));
}
static int link_received(void *unused,const uint8_t *bytes,size_t count,int64_t received){
 /* First accessible receive boundary, before protobuf/context processing.
  * The transport's received argument is uptime milliseconds, not this us clock.
  * Record failure without changing decode/context rejection precedence. */
 uint64_t received_us=0;int clock_rc=xdiag_clock_us(&received_us);
 ARG_UNUSED(unused);ARG_UNUSED(received);
 if(dw_link_decode(bytes,count,&incoming)){rejected_links++;return -EPROTO;}
 if(unused&&!*(const bool *)unused){rejected_links++;return -EAGAIN;}
 struct xt_bootstrap boot;if(xd_context_get(&boot)){
  rejected_links++;
  if(incoming.which_payload==xenova_drive_DriveLink_clock_tag)
   CLOCK_DIAG(7,-EAGAIN,&incoming.payload.clock,received_us,clock_rc);
  return -EAGAIN;
 }
 if(incoming.which_payload==xenova_drive_DriveLink_clock_tag){
  const xenova_drive_ClockReply *reply=&incoming.payload.clock;const xenova_drive_ClockProbe *probe=&reply->probe;
  unsigned role=!strcmp(probe->app_instance,boot.auto_instance_id)?0:!strcmp(probe->app_instance,boot.manual_instance_id)?1:2;
  if(role>1||clock_rc||!received_us||received_us>INT64_MAX){rejected_links++;CLOCK_DIAG(clock_rc?6:1,-EPROTO,reply,received_us,clock_rc);return -EPROTO;}
  struct guest_clock_slot *slot=&guest_clocks[role];uint64_t sent=slot->sent,now=received_us;
  if(!sent||probe->sequence!=slot->sequence||probe->guest_send_us!=sent||now<sent||
   strcmp(probe->vm,boot.identity.vm_id)||strcmp(probe->execution,boot.identity.execution_id)||
   strcmp(reply->host_boot,boot.identity.host_boot_id)||reply->pi_receive_us>reply->pi_send_us||
   reply->pi_send_us>INT64_MAX||strcmp(reply->vehicle.vehicle,lp_selected_config.vehicle)||
   strcmp(reply->vehicle.calibration,lp_selected_config.calibration)||
   (reply->has_trust&&(strcmp(reply->trust.configuration,lp_selected_config.id)||strcmp(reply->trust.primitive,lp_selected_assets.sha256)))){
   rejected_links++;CLOCK_DIAG(1,-EPROTO,reply,received_us,clock_rc);return -EPROTO;
  }
  int64_t lower=(int64_t)reply->pi_send_us-(int64_t)now,upper=(int64_t)reply->pi_receive_us-(int64_t)sent;
#if defined(CONFIG_XENOVA_DRIVE_NATIVE_TEST)
  if(clock_trace_count<16){uint64_t *row=clock_trace[clock_trace_count++];row[0]=sent;row[1]=now;row[2]=reply->pi_receive_us;row[3]=reply->pi_send_us;}
#endif
  if(reply->has_guest_clock_valid_until_pi_us){
   uint64_t e=reply->guest_clock_valid_until_pi_us,span=e-reply->pi_receive_us,rtt=now-sent;
   unsigned ppm=lp_selected_config.guest_drift_ppm,error=lp_selected_config.guest_timestamp_error_us;
   if(e<=reply->pi_receive_us||span>1000000||rtt>1000000||!ppm||ppm>=1000000||!error||error>=1000000)return -EPROTO;
   /* 원 E 전체를 덮는다. Pi acceptance 때 알려진 RTT+lease보다 좁게 잡지 않는다.
    * paired profile의 원 Pi support window를 사용하며 모든 계산은 checked integer다. */
   /* Pi support를 guest 기간으로 감쌀 때의 같은 보수 drift 식이다.
    * Pi owner의 ceil(ppm*duration/(1e6-ppm))보다 좁은 경계를 만들지 않는다. */
   uint64_t duration=2*span+rtt,denominator=1000000-ppm;
   if(duration>(UINT64_MAX-denominator+1)/ppm)return -ERANGE;
   uint64_t pad=2*(uint64_t)error+(duration*ppm+denominator-1)/denominator;
   if(pad>INT64_MAX||lower<INT64_MIN+(int64_t)pad||upper>INT64_MAX-(int64_t)pad)return -ERANGE;
   lower-=(int64_t)pad;upper+=(int64_t)pad;
  }
  if(upper<lower||(uint64_t)upper-(uint64_t)lower>lp_selected_config.clock_error_us){
   rejected_links++;CLOCK_DIAG(2,-ETIME,reply,received_us,clock_rc);return -ETIME;
  }
  if((upper>0&&(uint64_t)upper>INT64_MAX-now)||(int64_t)now+upper<0)return -ERANGE;
  uint64_t pi_now=(uint64_t)((int64_t)now+upper),expiry=0;
  /* Pi가 원 reply 생성 전에 정한 E만 보존한다. ACK 수신은 기한 갱신이 아니다. */
  bool numeric=reply->has_guest_clock_valid_until_pi_us&&reply->guest_clock_valid_until_pi_us>pi_now;
  if(numeric){uint64_t e=reply->guest_clock_valid_until_pi_us;
   if(e>INT64_MAX||(upper<0&&(uint64_t)(-(upper+1))+1>INT64_MAX-e)||(upper>0&&e<(uint64_t)upper))return -ERANGE;
   expiry=(uint64_t)((int64_t)e-upper);
  }
  xenova_drive_GuestClockReceipt receipt={0};receipt.probe_sequence=probe->sequence;receipt.guest_receive_us=now;
  receipt.reply_sha256.size=32;if(!xdr_sha256(bytes,count,receipt.reply_sha256.bytes))return -EINVAL;
  if(k_mutex_lock(&input_context_lock,K_NO_WAIT)){CLOCK_DIAG(3,-EBUSY,reply,received_us,clock_rc);return -EBUSY;}
  if(!context_available||strcmp(gateway,reply->gateway_instance)){
   k_mutex_unlock(&input_context_lock);CLOCK_DIAG(4,-EXDEV,reply,received_us,clock_rc);return -EXDEV;
  }
  /* Echo만으로 승인하지 않는다. 실제 보낸 receipt와 명시 Pi ACK를 모두 대조한다. */
  if(slot->pending_valid&&slot->sent_has_receipt&&probe->has_receipt&&reply->has_accepted_receipt&&
     same_receipt(&slot->receipt,&slot->sent_receipt)&&same_receipt(&slot->sent_receipt,&probe->receipt)&&
     same_receipt(&slot->sent_receipt,&reply->accepted_receipt)&&now<slot->pending.expires_guest){
   slot->confirmed=slot->pending;slot->confirmed.acknowledged=true;slot->confirmed_valid=true;
  }
  if(!reply->has_guest_clock_valid_until_pi_us&&now<=(uint64_t)INT64_MAX-lp_selected_config.clock_lease_us){
   /* 구형 reply는 기존 legacy profile에만 사용한다. command3 ACK로 승격하지 않는다. */
   struct dl_clock legacy={.lower=lower,.upper=upper,.expires_guest=now+lp_selected_config.clock_lease_us};
   strcpy(legacy.gateway,gateway);strcpy(legacy.vm,probe->vm);strcpy(legacy.execution,probe->execution);
   strcpy(legacy.app_instance,probe->app_instance);strcpy(legacy.host_boot,reply->host_boot);
   slot->confirmed=legacy;slot->confirmed_valid=true;
  }
  slot->pending_valid=numeric;
  if(numeric){struct dl_clock *out=&slot->pending;memset(out,0,sizeof(*out));out->lower=lower;out->upper=upper;
   out->expires_guest=expiry;out->expires_pi=reply->guest_clock_valid_until_pi_us;out->probe_sequence=probe->sequence;
   strcpy(out->gateway,gateway);strcpy(out->vm,probe->vm);strcpy(out->execution,probe->execution);
   strcpy(out->app_instance,probe->app_instance);strcpy(out->host_boot,reply->host_boot);slot->receipt=receipt;
   memcpy(out->reply_sha256,receipt.reply_sha256.bytes,32);
   strcpy(out->trust.vehicle,reply->vehicle.vehicle);strcpy(out->trust.odom_epoch,reply->vehicle.odom_epoch);
   strcpy(out->trust.calibration,reply->vehicle.calibration);
  }
  slot->gate_available=reply->has_direction_gate;
  if(slot->gate_available){slot->gate=reply->direction_gate;slot->gate_received_guest=now;}
  slot->vehicle=reply->vehicle;slot->sent=0;
  if(!role){
   if(reply->has_current_mission){int granted=cache_grant(&reply->current_mission,&boot);if(granted&&granted!=-ESTALE){k_mutex_unlock(&input_context_lock);return granted;}}
   else mission_available=false;
   bool available=reply->has_episode_status&&reply->has_current_mission&&reply->current_mission.control_generation==control_generation;
   facts_available=reply->has_control_facts;
   if(available){
    const xenova_drive_EpisodeStatus *status=&reply->episode_status;
    bool same=episode_available&&episode_mission_revision==reply->current_mission.mission_revision&&
     !memcmp(episode_mission_id,reply->current_mission.mission_id.bytes,16);
    bool epoch=same&&!memcmp(current_episode.registry_epoch.bytes,status->registry_epoch.bytes,16);
    if(!same||!status->state||(epoch&&status->registry_revision>=current_episode.registry_revision)){
     current_episode=*status;episode_available=true;
     memcpy(episode_mission_id,reply->current_mission.mission_id.bytes,16);episode_mission_revision=reply->current_mission.mission_revision;
    }else if(!epoch)episode_available=false;
    /* An old exact result can finish bookkeeping without rolling current status back. */
    const xenova_drive_EpisodeReceipt *answer=&status->receipt;
    if(episode_pending&&status->has_receipt&&answer->request_sequence==episode_request.request_sequence&&
       !memcmp(answer->request_nonce.bytes,episode_request.request_nonce.bytes,16)&&!memcmp(answer->request_sha256.bytes,episode_sha256,32)){
     if(!episode_reply_available){episode_reply=*answer;episode_reply_available=true;episode_send_pending=false;}
    }
   }else episode_available=false;
   if(facts_available){current_facts=reply->control_facts;facts_vehicle=reply->vehicle;}
   struct dw_trust previous_source=latest_trust;bool had_source=latest_trust.vehicle[0]!=0;
   const xenova_drive_WorldTrust *t=&reply->trust;struct dw_trust d={0};
   strcpy(d.vehicle,reply->vehicle.vehicle);strcpy(d.mapper_boot,t->mapper_boot);strcpy(d.mapper_instance,t->mapper_instance);
   strcpy(d.map_epoch,t->map_epoch);strcpy(d.frame_epoch,t->frame_epoch);strcpy(d.odom_epoch,reply->vehicle.odom_epoch);
   strcpy(d.configuration,t->configuration);strcpy(d.calibration,reply->vehicle.calibration);strcpy(d.primitive,t->primitive);
   strcpy(d.clock.epoch,t->clock_epoch);d.clock.lower_offset_us=t->lower_offset_us;d.clock.upper_offset_us=t->upper_offset_us;
   d.clock.expires_pi_us=t->expires_pi_us;d.clock.max_error_us=MIN(t->max_error_us,lp_selected_config.clock_error_us);d.max_live_age_us=lp_selected_config.live_age_us;
   if(engine){enum dw_result refreshed=reply->has_trust?dw_pool_refresh(&engine->maps,&d):DW_SOURCE;
    bool same_pending=refreshed==DW_BUSY&&had_source&&dw_trust_same_source(&previous_source,&d);
    if(refreshed&&!same_pending)dw_scene_fence(&engine->maps);
   }
   latest_trust=d;if(!slot->confirmed.acknowledged)slot->confirmed.trust=d;
  }
  /* fresh Vehicle/Gate와 원 confirmed numeric을 분리한다. */
  bool confirmed=slot->confirmed_valid&&now<slot->confirmed.expires_guest;struct dl_clock mapping=slot->confirmed;
  k_mutex_unlock(&input_context_lock);
  if(confirmed&&engine){dw_vehicle sample=reply->vehicle;uint64_t current=0;
   if((mapping.upper<=0||(uint64_t)mapping.upper<=INT64_MAX-now)&&(int64_t)now+mapping.upper>=0)current=(uint64_t)((int64_t)now+mapping.upper);
   uint64_t width=(uint64_t)mapping.upper-(uint64_t)mapping.lower;
   if(current>=sample.pi_sample_us&&current-sample.pi_sample_us+sample.clock_error_us<=lp_selected_config.pose_age_us&&width<=UINT32_MAX-sample.clock_error_us){
    sample.clock_error_us+=(uint32_t)width;enum dw_result added=dw_odom_add(&engine->odometry,&sample);
    if(added==DW_EPOCH&&!dw_odom_reset(&engine->odometry))added=dw_odom_add(&engine->odometry,&sample);
    if(!added)received_vehicle=sample.pi_sample_us;
   }
  }
  CLOCK_DIAG(5,0,reply,received_us,clock_rc);return 0;
 }
 if(incoming.which_payload==xenova_drive_DriveLink_scene_grant_tag){
  if(k_mutex_lock(&input_context_lock,K_NO_WAIT))return -EBUSY;
  int rc=cache_scene(&incoming.payload.scene_grant,&boot);
  k_mutex_unlock(&input_context_lock);return rc;
 }
 if(incoming.which_payload==xenova_drive_DriveLink_mission_grant_tag){
  if(k_mutex_lock(&input_context_lock,K_NO_WAIT))return -EBUSY;
  int rc=context_available?cache_grant(&incoming.payload.mission_grant,&boot):-EAGAIN;
  k_mutex_unlock(&input_context_lock);return rc==-ESTALE?0:rc;
 }
 if(incoming.which_payload==xenova_drive_DriveLink_budget_reply_tag){
  if(k_mutex_lock(&input_context_lock,K_NO_WAIT))return -EBUSY;
  const xenova_drive_MissionBudgetReply *reply=&incoming.payload.budget_reply;
  int rc=0;
  if(!context_available||!budget_pending||memcmp(reply->request_nonce.bytes,budget_request.request_nonce.bytes,16)||
     memcmp(reply->request_sha256.bytes,budget_sha256,32))rc=-EPROTO;
  else if(!budget_reply_available){
   if(reply->has_grant){int granted=cache_grant(&reply->grant,&boot);if(granted&&granted!=-ESTALE)rc=granted;}
   if(!rc){budget_reply=*reply;budget_reply_available=true;budget_send_pending=false;}
  }
  k_mutex_unlock(&input_context_lock);return rc;
 }
 if(incoming.which_payload==xenova_drive_DriveLink_accepted_tag){
  struct dl_clock mapping;uint64_t now;const xenova_drive_AcceptedState *copy=&incoming.payload.accepted;
  if(dl_clock_get(&mapping,&now)||strcmp(copy->host_boot,boot.identity.host_boot_id)||strcmp(copy->gateway_instance,mapping.gateway))return -EXDEV;
  for(unsigned k=0;k<2;k++)if(atomic_cas(&copies[k].state,0,1)){
   copies[k].copy=copy->state;atomic_set(&copies[k].state,2);return 0;
  }
  return -ENOBUFS;
 }
 rejected_links++;return -EPROTO;
}
int dl_state_take(const dw_state **out){
 for(unsigned k=0;k<2;k++)if(atomic_cas(&copies[k].state,2,3)){*out=&copies[k].copy;return (int)k;}
 return -EAGAIN;
}
void dl_state_release(int slot){if(slot>=0&&slot<2)(void)atomic_cas(&copies[slot].state,3,0);}
int dl_applied(const xenova_drive_AppliedState *value){
 if(stopped()){APPLIED_DIAG(1,-ESHUTDOWN,value);return -ESHUTDOWN;}
 if(k_mutex_lock(&applied_lock,K_NO_WAIT)){APPLIED_DIAG(1,-EBUSY,value);return -EBUSY;}
 applied=*value;application_pending=true;k_mutex_unlock(&applied_lock);
 APPLIED_DIAG(2,0,value);return 0;
}
static int binary_publish(struct xm_zclient *client,const char *topic,const xenova_drive_DriveLink *value){
#if defined(CONFIG_XENOVA_DRIVE_INPUT_DIAGNOSTICS)
 struct ct_tx tx;ct_binary_begin(value,&tx);
#endif
 pb_ostream_t stream=pb_ostream_from_buffer(send_wire,sizeof(send_wire));
#if defined(CONFIG_XENOVA_DRIVE_INPUT_DIAGNOSTICS)
 if(ct_stamp(&tx.eb))tx.known|=CT_EB;
#endif
 if(!pb_encode(&stream,xenova_drive_DriveLink_fields,value)){
#if defined(CONFIG_XENOVA_DRIVE_INPUT_DIAGNOSTICS)
  if(ct_stamp(&tx.ee))tx.known|=CT_EE;
  tx.encode_rc=-EMSGSIZE;tx.known|=CT_ENC_RC;ct_binary_end(&tx,-EMSGSIZE);
#endif
  return -EMSGSIZE;
 }
#if defined(CONFIG_XENOVA_DRIVE_INPUT_DIAGNOSTICS)
 if(ct_stamp(&tx.ee))tx.known|=CT_EE;
 tx.known|=CT_ENC_RC;
#endif
 struct mqtt_publish_param p={0};p.message.topic.topic.utf8=(const uint8_t *)topic;p.message.topic.topic.size=strlen(topic);
 p.message.topic.qos=MQTT_QOS_0_AT_MOST_ONCE;p.message.payload.data=send_wire;p.message.payload.len=stream.bytes_written;
#if defined(CONFIG_XENOVA_MQTT_BOUNDED_IO)
 client->socket_io.deadline_ms=k_uptime_get()+CONFIG_XENOVA_MQTT_IO_MS;
#endif
#if defined(CONFIG_XENOVA_DRIVE_INPUT_DIAGNOSTICS)
 if(ct_stamp(&tx.tb))tx.known|=CT_TB;
 int rc=mqtt_publish(&client->mqtt,&p);
 if(ct_stamp(&tx.te))tx.known|=CT_TE;
 tx.known|=CT_TX_RC;ct_binary_end(&tx,rc);return rc;
#else
 return mqtt_publish(&client->mqtt,&p);
#endif
}
static int setup(struct xm_zclient *client,struct xt_bootstrap *saved,bool world,bool *source_unavailable){
 *source_unavailable=false;
 struct xt_bootstrap boot;int rc=xd_context_get(&boot);if(rc){*source_unavailable=true;return rc;}
 if(world&&(!boot.world_broker_ipv4[0]||strcmp(boot.world_profile,lp_selected_config.id)))return -ENODATA;
 if(saved->identity.vm_id[0]&&same_identity(saved,&boot)&&
   (!world||(!strcmp(saved->world_broker_ipv4,boot.world_broker_ipv4)&&saved->world_port==boot.world_port&&
    !strcmp(saved->world_username,boot.world_username)&&!strcmp(saved->world_password,boot.world_password))))return 0;
 if(saved->identity.vm_id[0])xm_zclient_close(client);
 *saved=boot;
 struct xm_zconfig cfg={.host=world?saved->world_broker_ipv4:saved->broker_ipv4,.port=world?saved->world_port:saved->port,
  .username=world?saved->world_username:saved->username,.password=world?saved->world_password:saved->password,
  .boot_id=saved->identity.boot_id,.instance_id=saved->identity.publisher_id,.keepalive_s=10};
 rc=xm_zclient_init(client,saved->identity.vm_id,NULL,world?"drive-world":"drive-state",&cfg);
 if(rc){saved->identity.vm_id[0]=0;return rc;}
 /* Client connection labels are not app/execution identities. */
 snprintf(client->client_id_buf,sizeof(client->client_id_buf),"%s-%s-%.8s",saved->identity.vm_id,world?"world":"state",saved->identity.publisher_id);
 client->client_id.size=strlen(client->client_id_buf);
 client->mqtt.client_id=client->client_id;
 if(world)client->binary=(struct xm_zbinary){WORLD_TOPIC,map_wire,sizeof(map_wire),map_received,&map_input_allowed};
 else{
  snprintf(in_topic,sizeof(in_topic),"xenova/v1/vms/%s/drive/link/in",saved->identity.vm_id);
  snprintf(out_topic,sizeof(out_topic),"xenova/v1/vms/%s/drive/link/out",saved->identity.vm_id);
  client->binary=(struct xm_zbinary){in_topic,link_wire,sizeof(link_wire),link_received,&link_input_allowed};
 }
 rc=xm_zclient_subscribe(client,world?WORLD_TOPIC:in_topic,0);
 if(rc){xm_zclient_close(client);saved->identity.vm_id[0]=0;}
 return rc;
}
#if defined(CONFIG_XENOVA_DRIVE_INPUT_DIAGNOSTICS)
static int ct_setup(struct xm_zclient *client,struct xt_bootstrap *saved,bool world,bool *source_unavailable){
 uint64_t begin=0,end=0;bool before=ct_stamp(&begin);
 int rc=setup(client,saved,world,source_unavailable);bool after=ct_stamp(&end);
 if(ct_open){ct_increment(&ct_active.setup_calls);ct_active.setup_rc=rc;uint64_t elapsed;
  if(before&&after&&ct_span(begin,end,&elapsed)){ct_active.known|=CT_SETUP;if(elapsed>ct_active.setup_max)ct_active.setup_max=elapsed;}
 }
 return rc;
}
#define LINK_SETUP(client,saved,world,unavailable) ct_setup(client,saved,world,unavailable)
#else
#define LINK_SETUP(client,saved,world,unavailable) setup(client,saved,world,unavailable)
#endif
static void maps_step(void){
  bool was_open=map_client.socket_open,source_unavailable;
  map_input_allowed=false;
  int prepared=setup(&map_client,&map_boot,true,&source_unavailable);
  map_input_allowed=!prepared;
  /* Drain an existing transport without reconnecting or accepting app data.
   * Only the exact first context-read failure permits this limited pump. */
  if(!prepared||(source_unavailable&&was_open&&!stopped()))(void)xm_zclient_poll(&map_client,0);
  struct dl_clock mapping;uint64_t now;
  if(!prepared&&!dl_clock_get(&mapping,&now)){
#if defined(CONFIG_XENOVA_DRIVE_INPUT_DIAGNOSTICS)
   unsigned before=(engine->receive_pending?1U:0U)|(engine->receive_awaiting?2U:0U);
   uint64_t published=engine->maps.published;
   enum de_result rc=de_receive_poll(engine,now);
   if(before){
    unsigned after=(engine->receive_pending?1U:0U)|(engine->receive_awaiting?2U:0U);
    unsigned phase=published!=engine->maps.published?2U:after&2U?1U:after&1U?4U:3U;
    receive_diagnostic(phase,rc,before,after,now);
   }
#else
   (void)de_receive_poll(engine,now);
#endif
  }
}
static void maps(void *a,void *b,void *c){
 ARG_UNUSED(a);ARG_UNUSED(b);ARG_UNUSED(c);
 while(!stopped()){maps_step();k_sleep(K_MSEC(2));}
 map_input_allowed=false;
 xm_zclient_close(&map_client);
 while(de_receive_drain(engine)==DE_BUSY)k_sleep(K_MSEC(1));
 memset(&map_boot,0,sizeof(map_boot));
}
static void state_step(void){
  bool was_open=link_client.socket_open,source_unavailable;
  link_input_allowed=false;
  int prepared=LINK_SETUP(&link_client,&link_boot,false,&source_unavailable);
  link_input_allowed=!prepared;
  if(prepared&&source_unavailable&&was_open&&!stopped())(void)LINK_POLL(&link_client);
  if(!prepared){
   (void)LINK_POLL(&link_client);uint64_t now=clock_us();
   bool scheduled=now>=next_probe;
   if(link_client.connected&&(atomic_get(&episode_probe_requested)||scheduled)){
    struct xt_bootstrap boot;
    if(!xd_context_get(&boot))for(unsigned role=0;role<2;role++){
     if(role&&!scheduled)continue;
     const char *role_app=role?boot.manual_instance_id:boot.auto_instance_id;if(!role_app[0])continue;
     uint64_t sent_at=clock_us();if(!sent_at||sent_at>INT64_MAX)break;
     if(k_mutex_lock(&input_context_lock,K_NO_WAIT))break;
     struct guest_clock_slot *slot=&guest_clocks[role];
     /* A registered phase result uses a real subsequent probe, not a fabricated
      * clock ACK. Pending-only recovery cannot wait beyond the request budget. */
     bool pending=!role&&episode_pending&&!episode_reply_available;
     bool urgent=!role&&atomic_cas(&episode_probe_requested,1,0)&&pending;
     uint64_t retry=lp_selected_config.clock_lease_us/2;
     if(pending){uint64_t bounded=MIN(retry,lp_selected_config.pose_age_us/4);retry=bounded>2000?bounded:2000;}
     bool send=(urgent||!slot->sent||(sent_at>=slot->sent&&sent_at-slot->sent>=retry))&&slot->sequence<INT64_MAX;
     if(send){memset(&outgoing,0,sizeof(outgoing));outgoing.major=2;outgoing.which_payload=xenova_drive_DriveLink_probe_tag;
      xenova_drive_ClockProbe *p=&outgoing.payload.probe;strcpy(p->vm,boot.identity.vm_id);
      strcpy(p->execution,boot.identity.execution_id);strcpy(p->app_instance,role_app);
      p->sequence=++slot->sequence;p->guest_send_us=sent_at;slot->sent=sent_at;
      if(!role){p->has_supported_state_features=true;p->supported_state_features=DW_STATE_KNOWN_FEATURES;}
      p->has_receipt=slot->pending_valid&&sent_at<slot->pending.expires_guest;
      if(p->has_receipt)p->receipt=slot->receipt;
      slot->sent_has_receipt=p->has_receipt;if(p->has_receipt)slot->sent_receipt=p->receipt;
     }
     k_mutex_unlock(&input_context_lock);if(send)(void)binary_publish(&link_client,out_topic,&outgoing);
    }
    uint64_t interval=MIN(lp_selected_config.clock_lease_us/4,lp_selected_config.pose_age_us/4);
    if(scheduled)next_probe=now+(interval>2000?interval:2000);
   }
   if(link_client.connected&&!k_mutex_lock(&applied_lock,K_NO_WAIT)){
    bool send=application_pending;
    if(send){memset(&outgoing,0,sizeof(outgoing));outgoing.major=2;outgoing.which_payload=xenova_drive_DriveLink_applied_tag;
     outgoing.payload.applied=applied;application_pending=false;}
    k_mutex_unlock(&applied_lock);
#if defined(CONFIG_XENOVA_DRIVE_INPUT_DIAGNOSTICS)
    if(send){int published=binary_publish(&link_client,out_topic,&outgoing);
     APPLIED_DIAG(published?3:4,published,&outgoing.payload.applied);}
#else
    if(send)(void)binary_publish(&link_client,out_topic,&outgoing);
#endif
   }
   if(link_client.connected&&(atomic_get(&snapshot_requested)||now>=next_snapshot)){
    bool forced=atomic_cas(&snapshot_requested,1,0);
    memset(&outgoing,0,sizeof(outgoing));outgoing.major=2;outgoing.which_payload=xenova_drive_DriveLink_snapshot_tag;
    int captured=drive_lattice_snapshot(&outgoing.payload.snapshot);
    if(!captured)captured=binary_publish(&link_client,out_topic,&outgoing);
    if(captured&&forced)atomic_set(&snapshot_requested,1);
    next_snapshot=now+lp_selected_config.state_period_us;
   }
   if(link_client.connected&&!k_mutex_lock(&input_context_lock,K_NO_WAIT)){
    bool send=episode_pending&&episode_send_pending&&!episode_reply_available;
    if(send){
     uint64_t guest=clock_us();
     bool valid=clock_available&&current_clock.acknowledged&&guest<current_clock.expires_guest&&
      (current_clock.upper<=0||(uint64_t)current_clock.upper<=INT64_MAX-guest)&&
      (int64_t)guest+current_clock.upper>=0&&episode_request.valid_until_pi_us>(uint64_t)((int64_t)guest+current_clock.upper);
     if(valid){memset(&outgoing,0,sizeof(outgoing));outgoing.major=2;outgoing.which_payload=xenova_drive_DriveLink_episode_request_tag;outgoing.payload.episode_request=episode_request;}
     else send=false;
     episode_send_pending=false;
    }
    k_mutex_unlock(&input_context_lock);
    if(send){int sent=binary_publish(&link_client,out_topic,&outgoing);
     if(!k_mutex_lock(&input_context_lock,K_NO_WAIT)){
      if(sent){if(episode_pending&&!episode_reply_available)episode_send_pending=true;}
      else if(episode_pending&&!episode_reply_available&&!episode_probe_started){episode_probe_started=true;atomic_set(&episode_probe_requested,1);}
      k_mutex_unlock(&input_context_lock);
     }
    }
   }
   if(link_client.connected&&!k_mutex_lock(&input_context_lock,K_NO_WAIT)){
    bool send=budget_pending&&budget_send_pending&&!budget_reply_available;
    if(send){memset(&outgoing,0,sizeof(outgoing));outgoing.major=2;outgoing.which_payload=xenova_drive_DriveLink_budget_request_tag;
     outgoing.payload.budget_request=budget_request;budget_send_pending=false;}
    k_mutex_unlock(&input_context_lock);
    if(send&&binary_publish(&link_client,out_topic,&outgoing)&&!k_mutex_lock(&input_context_lock,K_NO_WAIT)){
     if(budget_pending&&!budget_reply_available)budget_send_pending=true;
     k_mutex_unlock(&input_context_lock);
    }
   }
  }
}
static void state(void *a,void *b,void *c){
 ARG_UNUSED(a);ARG_UNUSED(b);ARG_UNUSED(c);
 while(!stopped()){state_step();k_sleep(K_MSEC(2));}
#if defined(CONFIG_XENOVA_DRIVE_INPUT_DIAGNOSTICS)
 if(ct_open&&!ct_reply_seen&&!ct_wire_failed)ct_bump(&ct_counts[CT_NO_REPLY]);
 ct_open=false;
#endif
 link_input_allowed=false;
 xm_zclient_close(&link_client);memset(&link_boot,0,sizeof(link_boot));
}
int dl_start(struct drive_engine *value){
 if(engine||!value)return -EALREADY;
 engine=value;
 struct xm_zbinary binary={VEHICLE_TOPIC,vehicle_wire,sizeof(vehicle_wire),vehicle_received,NULL};
 int rc=xd_io_binary(&binary);if(rc)return rc;
 map_started=k_thread_create(&map_thread,map_stack,K_THREAD_STACK_SIZEOF(map_stack),maps,NULL,NULL,NULL,13,0,K_NO_WAIT)!=NULL;
 if(!map_started)return -ENOMEM;
 link_started=k_thread_create(&link_thread,link_stack,K_THREAD_STACK_SIZEOF(link_stack),state,NULL,NULL,NULL,12,0,K_NO_WAIT)!=NULL;
 if(!link_started)return -ENOMEM;
 k_thread_name_set(&map_thread,"drive-world");k_thread_name_set(&link_thread,"drive-state");return 0;
}
int dl_join(int64_t deadline){
 atomic_set(&stop,1);int rc=0;
 struct k_thread *threads[]={&map_thread,&link_thread};bool started[]={map_started,link_started};bool *joined[]={&map_joined,&link_joined};
 for(unsigned k=0;k<2;k++)if(started[k]&&!*joined[k]){
  int64_t left=deadline-k_uptime_get();int status=k_thread_join(threads[k],left>0?K_MSEC(left):K_NO_WAIT);
  if(status)rc=-ETIMEDOUT;else *joined[k]=true;
 }
 return rc;
}
const char *dl_status(void){return atomic_get(&reason)?"INPUT_OBSERVATION_FAILED":accepted_maps?"INPUT_AVAILABLE":"WORLD_UNAVAILABLE";}
void dl_report(void){
#if defined(CONFIG_XENOVA_DRIVE_INPUT_DIAGNOSTICS)
 LOG_INF("XD_VEHICLE_COUNTS group=1 p1=%llu p2=%llu p3=%llu p4=%llu p5=%llu p6=%llu p7=%llu p8=%llu p9=%llu",
  (unsigned long long)vehicle_phase_count[1],(unsigned long long)vehicle_phase_count[2],
  (unsigned long long)vehicle_phase_count[3],(unsigned long long)vehicle_phase_count[4],
  (unsigned long long)vehicle_phase_count[5],(unsigned long long)vehicle_phase_count[6],
  (unsigned long long)vehicle_phase_count[7],(unsigned long long)vehicle_phase_count[8],(unsigned long long)vehicle_phase_count[9]);
 LOG_INF("XD_VEHICLE_COUNTS group=2 p10=%llu p11=%llu p12=%llu p13=%llu p14=%llu p15=%llu p16=%llu p17=%llu",
  (unsigned long long)vehicle_phase_count[10],(unsigned long long)vehicle_phase_count[11],
  (unsigned long long)vehicle_phase_count[12],(unsigned long long)vehicle_phase_count[13],
  (unsigned long long)vehicle_phase_count[14],(unsigned long long)vehicle_phase_count[15],
  (unsigned long long)vehicle_phase_count[16],(unsigned long long)vehicle_phase_count[17]);
#endif
 printf("XD_WORLD {\"accepted_maps\":%llu,\"rejected_maps\":%llu,\"rejected_links\":%llu,\"rejected_vehicles\":%llu,\"clock\":%d,\"context\":%d,\"vehicle_us\":%llu,\"map_reason\":\"%s\"}\n",
  (unsigned long long)accepted_maps,(unsigned long long)rejected_maps,(unsigned long long)rejected_links,
  (unsigned long long)rejected_vehicles,clock_available,context_available,(unsigned long long)received_vehicle,
  dw_reason(atomic_load(&engine->last_world_result)));
 printf("XD_BINARY_IO {\"map_received\":%u,\"map_dropped\":%u,\"map_rc\":%d,\"state_received\":%u,\"state_dropped\":%u,\"state_rc\":%d,\"offset_lower\":%lld,\"offset_upper\":%lld}\n",
  map_client.received,map_client.dropped,map_client.last_io_rc,link_client.received,link_client.dropped,link_client.last_io_rc,
  (long long)current_clock.lower,(long long)current_clock.upper);
#if defined(CONFIG_XENOVA_DRIVE_NATIVE_TEST)
 for(unsigned k=0;k<clock_trace_count;k++)printf("XD_CLOCK [%llu,%llu,%llu,%llu]\n",
  (unsigned long long)clock_trace[k][0],(unsigned long long)clock_trace[k][1],
  (unsigned long long)clock_trace[k][2],(unsigned long long)clock_trace[k][3]);
#endif
}
