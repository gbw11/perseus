/* Real v1 encoder/owner code; only MQTT/TCP submission and close are replaced. */
#include <xenova/drive_io.h>
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static struct xm_zclient c;
static unsigned writes,aborts,closes;
static bool fail=true;
static int64_t ceiling;
int __wrap_mqtt_publish(struct mqtt_client *m,const struct mqtt_publish_param *p) {
 assert(m==&c.mqtt&&p->message.payload.len>0);
 assert(c.socket_io.deadline_ms==ceiling);writes++;
 return fail?-ETIMEDOUT:0;
}
int __wrap_mqtt_abort(struct mqtt_client *m){assert(m==&c.mqtt);aborts++;return 0;}
int __wrap_xt_socket_close(struct xt_socket *s){assert(s==&c.socket_io);closes++;s->fd=-1;return 0;}
int main(void) {
 struct xm_zconfig cfg={.host="192.0.2.1",.port=1883,.boot_id="11111111-1111-4111-8111-111111111111",.instance_id="22222222-2222-4222-8222-222222222222"};
 assert(!xm_zclient_init(&c,"33333333-3333-4333-8333-333333333333",NULL,"manual",&cfg));
 c.connected=c.socket_open=true;c.socket_io.fd=9;
 ceiling=k_uptime_get()+100;
 const char *topic="xenova/v1/apps/manual/events";
 const char *payload="{\"generation\":1,\"v_mps\":1,\"steer_rad\":0}";
 assert(xm_zclient_publish_until(&c,topic,"drive.command",payload,ceiling)==-ETIMEDOUT);
 assert(writes==1&&aborts==1&&closes==1&&!c.connected&&!c.socket_open&&c.socket_io.fd==-1);
 assert(c.message_counter==1&&c.published==0&&c.pubs[0].seq==0&&c.retry_at_ms>k_uptime_get());
 assert(!c.publish_deadline_ms);
 assert(xm_zclient_publish_until(&c,topic,"drive.command",payload,k_uptime_get())==-ETIMEDOUT);
 assert(writes==1&&c.message_counter==1);
 assert(xm_zclient_publish_until(&c,topic,"drive.command",payload,ceiling)==-ENOTCONN);
 assert(writes==1&&c.message_counter==1);
 fail=false;c.connected=c.socket_open=true;c.socket_io.fd=10;
 assert(!xm_zclient_publish_until(&c,topic,"drive.command",payload,ceiling));
 assert(writes==2&&c.message_counter==2&&c.published==1&&c.pubs[0].seq==1);
 puts("PASS production MQTT remaining deadline, partial-frame close, no automatic retry, v1 sequences");exit(0);
}
