/* SPDX-License-Identifier: Apache-2.0 */
#ifndef G1_HOST_EVENT_CHANNEL_H
#define G1_HOST_EVENT_CHANNEL_H
/* Host hypercall boundary. Actual ABI is checked by the pinned target build. */
#include <stdint.h>
#define EVTCHNOP_unmask 9
struct evtchn_unmask { uint32_t port; };

#endif
