/* SPDX-License-Identifier: Apache-2.0 */
#include <xenova/telemetry.h>
#include <xenova_build_identity.h>
const char *xt_build_id(void)
{
 static const char marker[]="xenova-build:" XENOVA_BUILD_ID;
 return marker+13;
}
