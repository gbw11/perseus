/* SPDX-License-Identifier: Apache-2.0 */
/* Host-only probe. All input/pose/pool operations execute the product C core. */
#include "world.h"
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static struct dw_snapshot sample;
static struct dw_pool pool;
static struct dw_odometry odom;
static uint8_t bytes[DW_WIRE_MAX+1];
static struct dw_trust trust={.vehicle="rc-test-1",.mapper_boot="11111111-1111-4111-8111-111111111111",
 .mapper_instance="22222222-2222-4222-8222-222222222222",.map_epoch="33333333-3333-4333-8333-333333333333",
 .frame_epoch="44444444-4444-4444-8444-444444444444",.odom_epoch="55555555-5555-4555-8555-555555555555",
 .configuration="lattice-lab-1",.calibration="encoder-lab-1",.primitive=TEST_PRIMITIVE_SHA,
 .clock={.epoch="66666666-6666-4666-8666-666666666666",.lower_offset_us=100000,
 .upper_offset_us=101000,.expires_pi_us=3000000,.max_error_us=2000},.max_live_age_us=300000};

static void ownership(void) {
 struct dw_handle writer,a,b,c;struct dw_snapshot *next;const struct dw_snapshot *reader;
 dw_pool_init(&pool);assert(dw_pool_source(&pool,&trust)==DW_OK);
 assert(dw_reserve(&pool,&writer,&next)==DW_OK);*next=sample;
 assert(dw_publish(&pool,writer)==DW_OK);assert(dw_publish(&pool,writer)==DW_HANDLE);
 assert(dw_acquire(&pool,&a,&reader)==DW_OK);assert(reader->world.sequence==1);
 assert(dw_acquire(&pool,&b,&reader)==DW_OK);
 assert(dw_release(&pool,a)==DW_OK);assert(dw_release(&pool,a)==DW_HANDLE);
 assert(dw_reserve(&pool,&writer,&next)==DW_OK);*next=sample;next->world.sequence=2;
 assert(dw_publish(&pool,writer)==DW_OK);
 assert(dw_acquire(&pool,&a,&reader)==DW_OK);
 assert(dw_reserve(&pool,&writer,&next)==DW_OK);*next=sample;next->world.sequence=3;
 assert(dw_publish(&pool,writer)==DW_OK);assert(dw_acquire(&pool,&c,&reader)==DW_OK);
 assert(dw_reserve(&pool,&writer,&next)==DW_NO_SPACE);
 assert(dw_release(&pool,b)==DW_OK);assert(dw_reserve(&pool,&writer,&next)==DW_OK);
 *next=sample;next->world.sequence=4;
 assert(dw_pool_source(&pool,&trust)==DW_OK);
 assert(dw_publish(&pool,writer)==DW_SOURCE);assert(dw_abort(&pool,writer)==DW_OK);
 assert(dw_source_valid(&pool,c)==DW_SOURCE);
 struct dw_handle unavailable;assert(dw_acquire(&pool,&unavailable,&reader)==DW_SOURCE);
 assert(dw_release(&pool,a)==DW_OK);assert(dw_release(&pool,c)==DW_OK);
 assert(dw_reserve(&pool,&writer,&next)==DW_OK);*next=sample;next->world.sequence=4;
 assert(dw_publish(&pool,writer)==DW_OK);
 assert(dw_acquire(&pool,&a,&reader)==DW_OK);assert(reader->world.sequence==4);
 assert(dw_source_valid(&pool,a)==DW_OK);assert(dw_release(&pool,a)==DW_OK);
}
static void coordinates(void) {
 dw_vehicle v={.major=1,.sequence=1,.source_sample=1,.source_us=100,.pi_sample_us=1100000,.quality=3};
 strcpy(v.vehicle,trust.vehicle);strcpy(v.odom_epoch,trust.odom_epoch);strcpy(v.calibration,trust.calibration);
 strcpy(v.device_session,trust.mapper_boot);strcpy(v.host_boot,trust.mapper_instance);
 assert(dw_odom_add(&odom,&v)==DW_OK);
 v.sequence=2;v.source_sample=2;v.source_us=200;v.pi_sample_us=1200000;v.odom_pose.x=1;
 assert(dw_odom_add(&odom,&v)==DW_OK);assert(dw_odom_add(&odom,&v)==DW_STALE);
 dw_pose pose;assert(dw_current_pose(&sample.world,&odom,1250000,100000,&pose,NULL)==DW_OK);
 assert(fabsf(pose.x-1.3f)<.0001f&&fabsf(pose.y-1.6f)<.0001f);
 sample.world.reference_odom_seq=100;
 assert(dw_current_pose(&sample.world,&odom,1250000,100000,&pose,NULL)==DW_NO_REFERENCE);
 sample.world.reference_odom_seq=1;
 assert(dw_current_pose(&sample.world,&odom,1500000,100000,&pose,NULL)==DW_TIME);
 strcpy(v.odom_epoch,trust.mapper_boot);v.sequence++;
 assert(dw_odom_add(&odom,&v)==DW_EPOCH);
}
int main(int argc,char **argv) {
 if(argc!=2)return 2;
 FILE *f=fopen(argv[1],"rb");if(!f)return 3;
 size_t length=fread(bytes,1,sizeof(bytes),f);fclose(f);
 enum dw_result result=dw_decode(bytes,length,&trust,1200000,&sample);
 printf("{\"result\":\"%s\",\"bytes\":%zu,\"world_size\":%zu,\"pool_size\":%zu,\"odom_size\":%zu}\n",
        dw_reason(result),length,sizeof(dw_world),sizeof(pool),sizeof(odom));
 if(result==DW_OK&&sample.world.sequence==1){
  uint64_t deadline=0;
  assert(dw_snapshot_time(&sample,1199000,300000,&deadline)==DW_OK&&deadline==1400000);
  assert(dw_snapshot_time(&sample,1400001,300000,&deadline)==DW_TIME);
  assert(dw_snapshot_time(&sample,1101000,300000,&deadline)==DW_TIME);
  assert(sample.received_us==1200000&&sample.observed_upper_age_us==100000);
  ownership();coordinates();
 }
 return 0;
}
