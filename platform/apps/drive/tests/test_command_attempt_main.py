"""Compile the actual main/controller callsite against finite external owners.

RTOS time, calculation/transport owners and the lattice engine are explicit
fixtures here. The actual main, repeat helper and MQTT JSON encoder run; this
does not claim a real planner, thread-scheduling or broker performance test.
"""
from pathlib import Path
import json
import subprocess

import pytest


HOME = Path(__file__).resolve().parents[1]
PLATFORM = HOME.parents[1]

KERNEL = r'''
#include <stdint.h>
#include <stdbool.h>
#define ARG_UNUSED(x) (void)(x)
#define K_MSEC(x) (x)
#define IS_ENABLED(x) TEST_ENABLED1(x)
#define TEST_ENABLED1(x) TEST_ENABLED2(TEST_ENABLED_##x)
#define TEST_ENABLED_1 ignored,
#define TEST_ENABLED2(x) TEST_ENABLED3(x 1,0)
#define TEST_ENABLED3(ignore,value,...) value
int64_t k_uptime_get(void);
void k_sleep(int64_t);
'''

HARNESS = r'''
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <xenova/drive_runtime.h>
#include "controller.h"
#include "workers.h"
#define XENOVA_LATTICE_APP_H
int drive_lattice_start(struct drive_controller *);
void drive_lattice_context(const struct xd_inputs *);
void drive_lattice_completed(const struct drive_completion *);
int drive_lattice_reset_poll(int64_t);
void drive_lattice_adapter(struct drive_adapter *,bool);
int drive_lattice_join(int64_t);
void drive_lattice_report(void);
#define main product_main
#include "main.c"
#undef main

#define VM "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
#define INSTANCE "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
static uint64_t at_us;
static struct drive_job offered[2];
static bool ready[2];
static unsigned offers[2],reads[2],loops,beats,supervisions,health_calls,attempts;
static unsigned restricted_attempts,neutral_attempts,moving_attempts,failed_attempts;
static unsigned first_restricted_iteration,restricted_reentry_iteration,last_restricted_iteration;
#if defined(CONFIG_XENOVA_DRIVE_WARM) && defined(CONFIG_XENOVA_DRIVE_LATTICE)
static uint64_t restricted_check;
#endif
static const char *last_reason;

int64_t k_uptime_get(void){return (int64_t)(at_us/1000);}
void k_sleep(int64_t ms){at_us+=(uint64_t)ms*1000;}
int xdiag_clock_us(uint64_t *out){*out=at_us;return 0;}
int xdiag_error(const char *where,int error){(void)where;(void)error;return 0;}
int xd_start(const char *app,uint32_t period){assert(app&&period==20);return 0;}
bool xd_stopping(void){return at_us>=1200000;}
int xd_finish(struct xm_zclient *client,int rc){(void)client;return rc;}
int xd_client_prepare_dual(struct xm_zclient *c,const char *a,const char *b,const char *p)
{(void)c;assert(a&&b&&p);return 0;}
uint64_t xd_next_release_us(void){return 0;}
int xd_iteration_begin(void){return 0;}
int xd_inputs_status(void){return 0;}
void xd_inputs_copy(struct xd_inputs *out){
 memset(out,0,sizeof(*out));strcpy(out->vm_id,VM);out->dual_mode=true;out->connected=true;
 out->control_revision=out->connection_revision=1;
 strcpy(out->app_instances[0],INSTANCE);strcpy(out->app_instances[1],INSTANCE);
 snprintf(out->control.payload,sizeof(out->control.payload),
  "{\"generation\":1,\"mode\":\"AUTO\",\"active_app\":\"autonomous\",\"active_vm_id\":\"%s\"}",VM);
 strcpy(out->input.payload,"{\"keys\":[]}");
 strcpy(out->perception.payload,"{\"lane_offset_m\":0,\"heading_err_rad\":0,\"stop\":false,\"obstacles\":[]}");
 struct xd_received *rows[]={&out->control,&out->input,&out->perception};
 for(unsigned i=0;i<3;i++){rows[i]->length=strlen(rows[i]->payload);rows[i]->seq=1;rows[i]->received_ms=k_uptime_get();}
}
int drive_workers_start(const struct drive_adapter *a,const struct drive_adapter *m){assert(a&&m);return 0;}
int drive_workers_status(enum drive_task task,struct drive_task_status *out){
 assert((unsigned)task<2);memset(out,0,sizeof(*out));out->started=true;out->lifetime=1;out->state=DRIVE_TASK_WAITING;return 0;
}
int drive_workers_offer(enum drive_task task,const struct drive_job *job){
 assert(job->deadline_us-job->release_us==20000);offered[task]=*job;ready[task]=true;offers[task]++;return 0;
}
int drive_workers_read(enum drive_task task,uint64_t revision,struct drive_completion *out){
 if(!ready[task])return -ENODATA;
 ready[task]=false;memset(out,0,sizeof(*out));out->source=offered[task];out->context_revision=revision;
 out->sequence=out->source.sequence;out->progress=++reads[task];out->performed=true;
 out->begin_us=out->source.release_us;out->end_us=out->begin_us+1;out->finished_ms=k_uptime_get();
 out->sample.binary_input=true;
 if(at_us<600000||at_us>=1000000)out->sample.reason="STATE_PREPARING";
 else if(at_us>=800000)out->sample.v_mps=.3f;
 last_reason=out->sample.reason;return 0;
}
void drive_workers_stop(void){}
int drive_workers_join(int64_t end){assert(end>0);return 0;}
int drive_task_cancel(enum drive_task t,uint64_t life,int64_t end){(void)t;(void)life;(void)end;assert(0);return 0;}
int drive_task_join_poll(enum drive_task t,uint64_t life){(void)t;(void)life;assert(0);return 0;}
int drive_task_restart(enum drive_task t,uint64_t life,const struct drive_adapter *a){(void)t;(void)life;(void)a;assert(0);return 0;}
int xd_completed_work(uint64_t release,uint64_t begin,uint64_t end,uint64_t iteration,int64_t finished,const struct xd_inputs *in){
 assert(release==begin&&end==begin+1&&iteration<=end&&finished<=k_uptime_get()&&in->control_revision==1);loops++;return 0;
}
void xd_health_record(const struct xdh_health *a,const struct xdh_health *m){assert(a&&m);health_calls++;}
void xd_health_task_record(unsigned task,const struct xdh_health *h){assert(task<2&&h);health_calls++;}
void xd_health_task_clear(unsigned task){assert(task<2);}
int xd_publish_completed(const char *topic,const char *type,const char *payload,uint64_t seq,int64_t produced,const struct xd_inputs *source){
 assert(topic&&payload&&seq&&produced<=k_uptime_get()&&source);if(!strcmp(type,"app.heartbeat"))beats++;return 0;
}
int xd_publish_supervision(const char *topic,const char *payload,uint64_t check,int64_t at,const struct xd_inputs *source){
 assert(topic&&payload&&check&&at<=k_uptime_get()&&source);supervisions++;return 0;
}
int xd_command_input(const char *type){assert(type);return 0;}
int xd_command_bound(int64_t until){assert(until>0);return 0;}
void xd_command(struct xm_zclient *client,const char *topic,const char *json,int encoded,uint32_t generation,float v,float steer,int64_t input_seq,int64_t control_seq,int64_t age,uint32_t timeout){
 (void)client;(void)input_seq;(void)control_seq;(void)age;(void)timeout;
 assert(topic&&json&&encoded>0&&generation==1&&steer==0);attempts++;
 if(last_reason){
  assert(v==0);restricted_attempts++;
  if(!first_restricted_iteration)first_restricted_iteration=loops;
  if(at_us>=1000000&&!restricted_reentry_iteration)restricted_reentry_iteration=loops;
  last_restricted_iteration=loops;
#if defined(CONFIG_XENOVA_DRIVE_WARM) && defined(CONFIG_XENOVA_DRIVE_LATTICE)
  /* The initial check may be0. After the first result a new real check may
   * permit the next attempt; later repetitions cannot reuse one check. */
  if(restricted_attempts>1&&at_us<600000)assert(lifecycle[0].check!=restricted_check);
  restricted_check=lifecycle[0].check;
#endif
  if(restricted_attempts<=2){failed_attempts++;return;} /* void offer/TX failures */
 }else if(v==0)neutral_attempts++;
 else moving_attempts++;
}
void xd_iteration_end(void){}
int64_t xd_cleanup_deadline_ms(void){return 2000;}
void xd_app_cleanup_error(int rc){assert(!rc);}
int drive_lattice_start(struct drive_controller *c){assert(c);return 0;}
void drive_lattice_context(const struct xd_inputs *in){assert(in);}
void drive_lattice_completed(const struct drive_completion *work){assert(work);}
int drive_lattice_reset_poll(int64_t end){assert(end);return 0;}
void drive_lattice_adapter(struct drive_adapter *a,bool manual){(void)a;(void)manual;assert(0);}
int drive_lattice_join(int64_t end){assert(end);return 0;}
void drive_lattice_report(void){}
int main(void){
 assert(!product_main());
 assert(loops==60&&reads[0]==60&&first_restricted_iteration==1&&restricted_reentry_iteration==51);
 assert(last_restricted_iteration>=51&&last_restricted_iteration<=60);
 assert(neutral_attempts==10&&moving_attempts==10&&failed_attempts==2&&beats>=2);
#if defined(CONFIG_XENOVA_DRIVE_WARM)
 assert(offers[1]==0&&supervisions>=20&&health_calls==60);
#else
 assert(offers[1]==60&&supervisions==0);
#endif
#if defined(CONFIG_XENOVA_DRIVE_WARM) && defined(CONFIG_XENOVA_DRIVE_LATTICE)
 assert(restricted_attempts<20&&restricted_attempts>=8);
#else
 assert(restricted_attempts==40);
#endif
 printf("{\"loops\":%u,\"auto_offers\":%u,\"manual_offers\":%u,\"restricted_attempts\":%u,\"neutral_attempts\":%u,\"moving_attempts\":%u,\"heartbeat_reports\":%u,\"supervision_reports\":%u,\"gate_bytes\":%zu}\n",
  loops,offers[0],offers[1],restricted_attempts,neutral_attempts,moving_attempts,beats,supervisions,sizeof(struct drive_command_gate));
 return 0;
}
'''


@pytest.mark.parametrize('profile,defines', [
    ('warm-lattice', ['CONFIG_XENOVA_DRIVE_WARM=1', 'CONFIG_XENOVA_DRIVE_LATTICE=1']),
    ('warm-existing', ['CONFIG_XENOVA_DRIVE_WARM=1']),
    ('legacy-existing', []),
])
def test_actual_main_limits_only_repeated_restricted_zero(tmp_path, profile, defines):
    value = compile_main(tmp_path, HARNESS, defines)
    assert value['loops'] == value['auto_offers'] == 60
    assert value['neutral_attempts'] == value['moving_attempts'] == 10
    assert value['restricted_attempts'] < 20 if profile == 'warm-lattice' else value['restricted_attempts'] == 40


def compile_main(tmp_path, harness, defines):
    include = tmp_path / 'include'
    (include / 'zephyr').mkdir(parents=True)
    (include / 'xenova').mkdir()
    (include / 'zephyr/kernel.h').write_text(KERNEL)
    (include / 'xenova/diagnostics.h').write_text(
        '#include <stdint.h>\nint xdiag_clock_us(uint64_t *);\nint xdiag_error(const char *,int);\n')
    (include / 'xm_zclient.h').write_text(
        '#ifndef TEST_ZCLIENT_H\n#define TEST_ZCLIENT_H\n#include <xenova/mqtt/xm.h>\n'
        'struct xm_zclient {int unused;};\nstruct xm_zbinary;\n#endif\n')
    source = tmp_path / 'main-harness.c'
    source.write_text(harness)
    binary = tmp_path / 'actual-main'
    mqtt = PLATFORM / 'apps/common/xenova-mqtt-c'
    paths = [include, HOME / 'zephyr/src', PLATFORM / 'apps/common/xenova-drive-runtime/include',
             PLATFORM / 'guests/zephyr/diagnostics/include', mqtt / 'include',
             PLATFORM / 'apps/autonomous/zephyr/src', PLATFORM / 'apps/manual/zephyr/src']
    sources = [source, HOME / 'zephyr/src/controller.c', HOME / 'zephyr/src/adapter.c',
               PLATFORM / 'guests/zephyr/diagnostics/src/health.c',
               PLATFORM / 'apps/autonomous/zephyr/src/autonomous.c',
               PLATFORM / 'apps/manual/zephyr/src/manual.c']
    sources += [mqtt / 'src' / name for name in (
        'xm_topic.c', 'xm_policy.c', 'xm_envelope.c', 'xm_json.c', 'xm_seq.c', 'xm_payload.c')]
    argv = ['cc', '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
            '-fsanitize=address,undefined', '-DCONFIG_XENOVA_DRIVE_APP_STOP_MS=500',
            *['-D' + value for value in defines], *['-I' + str(path) for path in paths],
            '-I' + str(mqtt / 'third_party/jsmn'), *map(str, sources), '-lm', '-o', str(binary)]
    built = subprocess.run(argv, text=True, capture_output=True)
    assert built.returncode == 0, built.stderr
    ran = subprocess.run([str(binary)], text=True, capture_output=True)
    assert ran.returncode == 0, ran.stderr
    return json.loads(ran.stdout)


def test_actual_main_skips_only_unobserved_busy_context(tmp_path):
    # Same actual main/controller/JSON. Only snapshot source and transport,
    # worker and lattice-context owners are finite explicit fixtures.
    harness = HARNESS.replace('static uint64_t at_us;',
        'static uint64_t at_us; static unsigned context_seen[7];')
    harness = harness.replace('return at_us>=1200000;', 'return at_us>=7000;')
    harness = harness.replace('int xd_inputs_status(void){return 0;}',
        'int xd_inputs_status(void){return at_us==0?-EBUSY:at_us==1000?-EIO:at_us==2000?-EAGAIN:0;}')
    harness = harness.replace('for(unsigned i=0;i<3;i++){rows[i]->length=strlen(rows[i]->payload);rows[i]->seq=1;rows[i]->received_ms=k_uptime_get();}',
        '''for(unsigned i=0;i<3;i++){rows[i]->length=strlen(rows[i]->payload);rows[i]->seq=1;rows[i]->received_ms=k_uptime_get();}
 if(xd_inputs_status()){memset(out,0,sizeof(*out));out->control.error=out->input.error=out->perception.error=xd_inputs_status();}
 if(at_us==3000)out->connected=false;
 if(at_us==4000)out->control.error=-EBUSY;
 if(at_us==5000)strcpy(out->control.instance,"cccccccc-cccc-4ccc-8ccc-cccccccccccc");''')
    harness = harness.replace('void drive_lattice_context(const struct xd_inputs *in){assert(in);}',
        '''void drive_lattice_context(const struct xd_inputs *in){
 assert(in&&xd_inputs_status()!=-EBUSY);unsigned row=at_us/1000;assert(row<7);context_seen[row]++;
 if(row==1||row==2)assert(!in->connected&&in->control.error==xd_inputs_status());
 if(row==3)assert(!in->connected);
 if(row==4)assert(in->connected&&in->control.error==-EBUSY);
 if(row==5)assert(!strcmp(in->control.instance,"cccccccc-cccc-4ccc-8ccc-cccccccccccc"));
}''')
    harness = harness.replace('assert(job->deadline_us-job->release_us==20000);',
        'assert(xd_inputs_status()!=-EBUSY);assert(job->deadline_us-job->release_us==20000);')
    harness = harness.replace('assert(topic&&json&&encoded>0&&generation==1&&steer==0);attempts++;',
        'assert(xd_inputs_status()!=-EBUSY);assert(topic&&json&&encoded>0&&generation==1&&steer==0);attempts++;')
    harness = harness[:harness.index('int main(void){')] + r'''
int main(void){
 assert(!product_main());assert(context_seen[0]==0&&offers[0]==0&&offers[1]==0&&attempts==0);
 for(unsigned i=1;i<7;i++)assert(context_seen[i]==1);
 printf("{\"context_updates\":6,\"busy_updates\":0,\"busy_offers\":0,\"commands\":0}\n");return 0;
}
'''
    result = compile_main(tmp_path, harness,
        ['CONFIG_XENOVA_DRIVE_WARM=1', 'CONFIG_XENOVA_DRIVE_LATTICE=1'])
    assert result == dict(context_updates=6, busy_updates=0, busy_offers=0, commands=0)


def test_actual_publication_time_pair_preserves_raw_sample_and_deadline(tmp_path):
    # Invoke the actual static main boundary. These are adjacent clock-read
    # fixtures, not a replacement for the conversion under test.
    harness = HARNESS.replace('static uint64_t at_us;', '''static uint64_t at_us;
static int64_t pair_before,pair_after;static uint64_t pair_counter;
static unsigned pair_reads,pair_clocks;static int pair_rc;''')
    harness = harness.replace('int64_t k_uptime_get(void){return (int64_t)(at_us/1000);}',
        'int64_t k_uptime_get(void){return pair_reads++?pair_after:pair_before;}')
    harness = harness.replace('int xdiag_clock_us(uint64_t *out){*out=at_us;return 0;}',
        'int xdiag_clock_us(uint64_t *out){pair_clocks++;if(pair_rc)return pair_rc;*out=pair_counter;return 0;}')
    harness = harness[:harness.index('int main(void){')] + r'''
struct old_sample {float v_mps,steer_rad,nearest_m;enum auto_reason auto_reason;const char *reason;
 bool binary_input;int64_t input_sequence,input_received_ms,valid_until_ms;char map_epoch[37];
 uint64_t reference_revision,observation_sequence;uint32_t path_generation,tracker_index,maneuver_phase;};
_Static_assert(sizeof(struct old_sample)==sizeof(struct drive_sample),"sample padding must absorb domain");
_Static_assert(offsetof(struct old_sample,input_sequence)==offsetof(struct drive_sample,input_sequence),"source offsets unchanged");
static unsigned checks;
static int pair(struct drive_completion *work,int64_t before,uint64_t counter,int64_t after,
 int rc,int expected,int64_t bound,int64_t age){
 struct drive_sample original=work->sample;int64_t actual_bound=777,actual_age=888;
 pair_before=before;pair_counter=counter;pair_after=after;pair_reads=pair_clocks=0;pair_rc=rc;
 int value=command_time(work,123,&actual_bound,&actual_age);
 assert(value==expected&&!memcmp(&work->sample,&original,sizeof(original)));
 if(!value)assert(actual_bound==bound&&actual_age==age);
 if(value==-ETIME)assert(actual_age==age&&actual_bound==777);
 assert(pair_clocks==1);checks++;return value;
}
int main(void){
 struct drive_completion work={0};work.sample.clock_domain=DRIVE_SAMPLE_COUNTER_MS;work.sample.binary_input=true;
 work.end_us=2514000;work.sample.input_received_ms=2510;work.sample.valid_until_ms=2535;
 pair(&work,500,2514567,500,0,0,520,6); /* raw epoch ahead */
 work.end_us=2500000;work.sample.input_received_ms=2499;work.sample.valid_until_ms=2521;
 pair(&work,5000,2500501,5000,0,0,5020,3); /* raw epoch behind */
 work.end_us=0;work.sample.input_received_ms=0;work.sample.valid_until_ms=30;
 pair(&work,0,0,0,0,0,30,1); /* a genuine source timestamp0 */
 work.sample.valid_until_ms=10;
 pair(&work,100,10000,100,0,-ETIME,0,11); /* exact expiry */
 pair(&work,100,9100,100,0,-ETIME,0,11); /* floor never extends sub-ms lease */
 work.sample.valid_until_ms=30;
 pair(&work,100,10000,110,0,0,120,21); /* preemption charged conservatively */
 work.sample.valid_until_ms=18;
 pair(&work,100,10000,110,0,-ETIME,0,21); /* expiry during the sampling interval */
 work.sample.valid_until_ms=30;work.end_us=10001;
 pair(&work,100,10000,100,0,-ERANGE,0,0); /* raw clock reversed since work */
 work.end_us=0;
 pair(&work,100,10000,99,0,-ERANGE,0,0);
 pair(&work,-1,10000,0,0,-ERANGE,0,0);
 pair(&work,100,10000,100,-EIO,-EIO,0,0); /* exact clock errno */
 work.sample.input_received_ms=-1;
 pair(&work,100,10000,100,0,-ERANGE,0,0);
 work.sample.input_received_ms=11;
 pair(&work,100,10000,100,0,-ERANGE,0,0);
 work.sample.input_received_ms=INT64_MAX;
 pair(&work,100,10000,100,0,-EOVERFLOW,0,0);
 work.sample.input_received_ms=0;work.sample.valid_until_ms=INT64_MAX;
 pair(&work,100,10000,100,0,-EOVERFLOW,0,0);
 work.sample.valid_until_ms=30;
 pair(&work,INT64_MAX-10,10000,INT64_MAX-10,0,-EOVERFLOW,0,0);
 pair(&work,0,10000,INT64_MAX,0,-EOVERFLOW,0,0);
 work.sample.valid_until_ms=0;
 pair(&work,100,10000,100,0,-ETIME,0,11);
 work.sample.binary_input=false;work.source.input_received_ms=495;work.sample.valid_until_ms=2535;
 work.sample.input_received_ms=INT64_MIN; /* MANUAL source is uptime, not this field */
 pair(&work,500,2514567,500,0,0,520,5);
 work.sample.clock_domain=(enum drive_sample_clock)77;pair_clocks=pair_reads=0;
 int64_t bound=555,age=666;assert(command_time(&work,500,&bound,&age)==-EINVAL&&pair_clocks==0);
 work.sample.clock_domain=DRIVE_SAMPLE_UPTIME_MS;work.sample.binary_input=true;
 work.sample.input_received_ms=0;work.sample.valid_until_ms=25;
 assert(!command_time(&work,0,&bound,&age)&&bound==25&&age==0&&pair_clocks==0);
 printf("{\"checks\":%u,\"sample_bytes\":%zu,\"source_offset\":%zu}\n",checks+2,sizeof(work.sample),offsetof(struct drive_sample,input_sequence));
 return 0;
}
'''
    result = compile_main(tmp_path, harness, ['CONFIG_XENOVA_DRIVE_LATTICE=1'])
    assert result == dict(checks=21, sample_bytes=128, source_offset=32)


def epoch_harness(raw_epoch_us, uptime_epoch_ms, manual=False):
    harness = HARNESS.replace('int64_t k_uptime_get(void){return (int64_t)(at_us/1000);}',
        f'int64_t k_uptime_get(void){{return {uptime_epoch_ms}+(int64_t)(at_us/1000);}}')
    harness = harness.replace('int xdiag_clock_us(uint64_t *out){*out=at_us;return 0;}',
        f'int xdiag_clock_us(uint64_t *out){{*out=at_us+{raw_epoch_us}+1;return 0;}}')
    harness = harness.replace('uint64_t xd_next_release_us(void){return 0;}',
        f'uint64_t xd_next_release_us(void){{return {raw_epoch_us};}}')
    harness = harness.replace('out->sample.binary_input=true;', f'''out->sample.binary_input={'false' if manual else 'true'};
 out->sample.clock_domain=DRIVE_SAMPLE_COUNTER_MS;
 out->sample.valid_until_ms=(int64_t)((at_us+{raw_epoch_us})/1000)+50;
 out->sample.input_received_ms={'INT64_MIN' if manual else f'(int64_t)((at_us+{raw_epoch_us})/1000)-10'};''')
    harness = harness.replace('int xd_command_bound(int64_t until){assert(until>0);return 0;}',
        'int xd_command_bound(int64_t until){assert(until==k_uptime_get()+49);return 0;}')
    harness = harness.replace('(void)client;(void)input_seq;(void)control_seq;(void)age;(void)timeout;',
        f'''(void)client;(void)input_seq;(void)control_seq;(void)timeout;
 assert(age=={0 if manual else 12});
 assert(completed[{1 if manual else 0}].sample.valid_until_ms==(int64_t)((at_us+{raw_epoch_us})/1000)+50);''')
    if manual:
        harness = harness.replace('\\"mode\\":\\"AUTO\\",\\"active_app\\":\\"autonomous\\"',
                                  '\\"mode\\":\\"MANUAL\\",\\"active_app\\":\\"manual\\"')
        harness = harness.replace('lifecycle[0].check', 'lifecycle[1].check')
        harness = harness[:harness.index('int main(void){')] + r'''
int main(void){
 assert(!product_main());assert(offers[0]==0&&offers[1]==60&&reads[1]==60&&attempts>20);
 printf("{\"manual_offers\":%u,\"attempts\":%u}\n",offers[1],attempts);return 0;
}
'''
    return harness


@pytest.mark.parametrize('raw_epoch,uptime_epoch', [(2014000, 0), (1000000, 5000)])
def test_actual_main_converts_nonzero_epochs_without_mutating_completion(tmp_path, raw_epoch, uptime_epoch):
    result = compile_main(tmp_path, epoch_harness(raw_epoch, uptime_epoch),
        ['CONFIG_XENOVA_DRIVE_WARM=1', 'CONFIG_XENOVA_DRIVE_LATTICE=1'])
    assert result['loops'] == result['auto_offers'] == 60


def test_actual_main_manual_only_converts_counter_bound(tmp_path):
    result = compile_main(tmp_path, epoch_harness(2014000, 0, manual=True),
        ['CONFIG_XENOVA_DRIVE_WARM=1', 'CONFIG_XENOVA_DRIVE_LATTICE=1'])
    assert result['manual_offers'] == 60


def test_actual_main_expired_counter_result_is_rejected_then_recovers(tmp_path):
    harness = epoch_harness(2014000, 0)
    harness = harness.replace('static uint64_t at_us;', 'static uint64_t at_us;static unsigned late;')
    harness = harness.replace('(int64_t)((at_us+2014000)/1000)+50',
        '(int64_t)((at_us+2014000)/1000)+(at_us<400000?-1:50)')
    harness = harness.replace('assert(topic&&json&&encoded>0&&generation==1&&steer==0);attempts++;',
        '''assert(topic&&json&&generation==1&&steer==0);
 if(encoded==-ETIME){assert(at_us<400000);late++;return;}
 assert(encoded>0&&at_us>=400000);attempts++;''')
    harness = harness[:harness.index('int main(void){')] + r'''
int main(void){
 assert(!product_main());assert(loops==60&&reads[0]==60&&late>0);
 assert(neutral_attempts==10&&moving_attempts==10&&attempts>20);
 printf("{\"loops\":%u,\"late_rejections\":%u,\"neutral\":%u,\"moving\":%u}\n",loops,late,neutral_attempts,moving_attempts);
 return 0;
}
'''
    result = compile_main(tmp_path, harness,
        ['CONFIG_XENOVA_DRIVE_WARM=1', 'CONFIG_XENOVA_DRIVE_LATTICE=1'])
    assert result['loops'] == 60 and result['late_rejections'] > 0
    assert result['neutral'] == result['moving'] == 10


def test_actual_main_counter_conversion_does_not_bypass_mode_revocation(tmp_path):
    harness = epoch_harness(2014000, 0)
    harness = harness.replace('strcpy(out->input.payload,"{\\"keys\\":[]}");', r'''
 if(at_us>=300000&&at_us<500000)
  strcpy(out->control.payload,"{\"generation\":2,\"mode\":\"SAFE_STOP\",\"active_app\":\"\",\"active_vm_id\":\"\"}");
 else if(at_us>=500000)
  snprintf(out->control.payload,sizeof(out->control.payload),"{\"generation\":3,\"mode\":\"AUTO\",\"active_app\":\"autonomous\",\"active_vm_id\":\"%s\"}",VM);
 strcpy(out->input.payload,"{\"keys\":[]}");''')
    harness = harness.replace('assert(topic&&json&&encoded>0&&generation==1&&steer==0);attempts++;',
        '''assert(topic&&json&&encoded>0&&generation==(at_us<300000?1:3)&&steer==0);
 assert(at_us<300000||at_us>=500000);attempts++;''')
    harness = harness[:harness.index('int main(void){')] + r'''
int main(void){
 assert(!product_main());assert(loops==50&&offers[0]==50&&reads[0]==50&&offers[1]==0);
 assert(neutral_attempts==10&&moving_attempts==10);
 printf("{\"loops\":%u,\"offers\":%u,\"neutral\":%u,\"moving\":%u}\n",loops,offers[0],neutral_attempts,moving_attempts);return 0;
}
'''
    result = compile_main(tmp_path, harness,
        ['CONFIG_XENOVA_DRIVE_WARM=1', 'CONFIG_XENOVA_DRIVE_LATTICE=1'])
    assert result == dict(loops=50, offers=50, neutral=10, moving=10)
