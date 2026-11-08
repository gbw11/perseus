/* Native integration fixture only; not installed or called by production. */
#ifndef B08_INPUT_BRIDGE_H
#define B08_INPUT_BRIDGE_H
#include "engine.h"
#include <xenova/drive_runtime.h>
int b08_input_bind(struct drive_engine *);
void b08_input_control(const struct xd_inputs *);
void b08_input_step(void);
void b08_input_map_step(void);
int b08_input_link(const uint8_t *,size_t);
int b08_input_world(const uint8_t *,size_t);
int b08_input_vehicle(const uint8_t *,size_t);
int b08_input_request_body(uint8_t *,size_t);
int b08_input_receipt_view(uint8_t *,size_t);
#endif
