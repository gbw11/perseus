/* Actual input owner in a separate TU from lattice_app.c integration.
 * RTOS clock/boot/MQTT functions are declared transport leaves supplied by the
 * integration fixture. No confirmed offset/ACK/expiry is assigned here. Raw
 * callbacks require actual setup admission; all codec/clock/source guards remain.
 * mqtt_publish must capture the original binary_publish bytes, not rebuild them. */
#include "input_bridge.h"
#define XENOVA_LATTICE_APP_H
int drive_lattice_snapshot(dw_state *);
#include "lattice_input.c"

int b08_input_bind(struct drive_engine *value){
 if(!value)return -EINVAL;
 if(engine&&engine!=value)return -EALREADY;
 engine=value;return 0;
}
void b08_input_control(const struct xd_inputs *value){dl_context(value);}
void b08_input_step(void){if(engine&&!stopped())state_step();}
void b08_input_map_step(void){if(engine&&!stopped())maps_step();}
int b08_input_link(const uint8_t *bytes,size_t count){return link_received(&link_input_allowed,bytes,count,0);}
int b08_input_world(const uint8_t *bytes,size_t count){b08_input_map_step();return map_received(&map_input_allowed,bytes,count,0);}
int b08_input_vehicle(const uint8_t *bytes,size_t count){return vehicle_received(NULL,bytes,count,0);}
int b08_input_request_body(uint8_t *out,size_t cap){
 if(!out)return -EINVAL;
 if(!episode_pending)return -EAGAIN;
 if(cap<episode_wire_size)return -ENOSPC;
 memcpy(out,episode_wire,episode_wire_size);return (int)episode_wire_size;
}
int b08_input_receipt_view(uint8_t *out,size_t cap){
 if(!out)return -EINVAL;
 if(!episode_reply_available)return -EAGAIN;
 pb_ostream_t stream=pb_ostream_from_buffer(out,cap);
 return pb_encode(&stream,xenova_drive_EpisodeReceipt_fields,&episode_reply)?(int)stream.bytes_written:-EMSGSIZE;
}
