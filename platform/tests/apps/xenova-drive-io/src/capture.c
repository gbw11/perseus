/* Real recorder and I/O owner; delay only the external MQTT completion. */
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <zephyr/shell/shell_dummy.h>
#ifndef XL_CAPTURE_DRIVE_SOURCE
#define XL_CAPTURE_DRIVE_SOURCE "../../../../apps/common/xenova-drive-runtime/src/runtime.c"
#endif
#include XL_CAPTURE_DRIVE_SOURCE

static struct xm_zclient test_client;
K_SEM_DEFINE(send_entered, 0, 1);
K_SEM_DEFINE(send_release, 0, 1);

int __wrap_xm_zclient_poll(struct xm_zclient *client, int32_t timeout)
{
	ARG_UNUSED(client); ARG_UNUSED(timeout); return 0;
}
int __wrap_xm_zclient_publish_until(struct xm_zclient *client, const char *topic,
		const char *type, const char *json, int64_t deadline)
{
	ARG_UNUSED(topic); ARG_UNUSED(json);
	assert(!strcmp(type, "drive.command") && deadline > k_uptime_get());
	k_sem_give(&send_entered);
	assert(!k_sem_take(&send_release, K_MSEC(150)));
	client->message_counter++;
	return 0;
}
void __wrap_xm_zclient_close(struct xm_zclient *client) { client->connected = false; }

int main(void)
{
	/* The fixture supplies a bootstrapped/CONNACKed client, not real broker
	 * authentication evidence. Capture, exchange, thread, clock and shell
	 * callback code remain the product implementation. */
	app = "manual";
	struct xm_zconfig cfg = {.host="192.0.2.1", .port=1883,
		.boot_id="11111111-1111-4111-8111-111111111111",
		.instance_id="22222222-2222-4222-8222-222222222222"};
	assert(!xm_zclient_init(&test_client,"33333333-3333-4333-8333-333333333333",NULL,app,&cfg));
	test_client.connected = true; client_ready = true; app_client = &test_client;
	assert(!xdiag_loop_init(&loop, 20000, 20000));
	assert(!xd_client_prepare(&test_client,"control","input"));
	for (unsigned i=0; i<20; i++) {
		struct xd_inputs state; assert(!xd_io_snapshot(&state));
		if (state.connected) { break; }
		k_sleep(K_MSEC(10));
	}
	char *arm[] = {"drive","capture","0","20"};
	assert(!drive_cmd(NULL,4,arm));
	assert(!xd_iteration_begin() && !xd_step_begin() && !xd_step_end());
	xd_command(&test_client,"events","{}",2,0,0,0,-1,-1,0,200);
	assert(!current.rc && !k_sem_take(&send_entered,K_MSEC(100)));
	k_sleep(K_MSEC(30)); xd_iteration_end();
	assert(capture_complete); /* The calculation window has ended. */
	char *transmissions[] = {"drive","transmissions","0","1"};
	char *records[] = {"drive","records","0","1"};
	assert(drive_cmd(NULL,4,transmissions)==-EBUSY);
	assert(drive_cmd(NULL,4,records)==-EBUSY);
	assert(drive_cmd(NULL,4,arm)==-EBUSY); /* no reset across in-flight work */
	k_sem_give(&send_release);
	assert(!xd_io_join(k_uptime_get()+1000));
	assert(!drive_cmd(NULL,4,transmissions));
	size_t sealed_count=delivery_count;
	assert(sealed_count==1 && !drive_cmd(NULL,4,transmissions));
	assert(delivery_count==sealed_count && !drive_cmd(NULL,4,records));
	const struct shell *sh=shell_backend_dummy_get_ptr(); assert(sh);
	for (unsigned page=0; page<2; page++) {
		shell_backend_dummy_clear_output(sh);
		assert(!shell_execute_cmd(sh,"xenova drive transmissions 0 1"));
		size_t bytes; const char *output=shell_backend_dummy_get_output(sh,&bytes);
		assert(bytes && strstr(output,"XD_PAGE {\"offset\":0,\"next\":1,\"total\":1}"));
		assert(strstr(output,"\"transmissions_complete\":true"));
		printf("PAGE_EVIDENCE %.*s\n",(int)bytes,output);
	}
	puts("PASS window ended before socket completion; no premature pages/reset; sealed transmission total stable");
	exit(0);
}
