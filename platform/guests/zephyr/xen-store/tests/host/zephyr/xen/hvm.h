/* SPDX-License-Identifier: Apache-2.0 */
#include <stdint.h>
#define HVM_PARAM_STORE_PFN 1
#define HVM_PARAM_STORE_EVTCHN 2
#define HVM_PARAM_CONSOLE_EVTCHN 3
#define HVM_PARAM_CONSOLE_PFN 4
#define DOMID_SELF 0x7ff0
int hvm_get_parameter(int,int,uint64_t *);
