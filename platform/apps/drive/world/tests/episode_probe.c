/* Shared codec/fingerprint boundary probe. Synthetic input, no driver or authority. */
#include "world.h"
#include "pb_decode.h"
#include <stdio.h>
#include <string.h>
static uint8_t wire[DW_WIRE_MAX];
static dw_state state;
static xenova_drive_EpisodeCore core;
static xenova_drive_EpisodeSourceContext source;
static xenova_drive_MissionExecution goal;
static xenova_drive_DriveLink link;
int main(int argc,char **argv){
 if(argc!=3)return 2;
 FILE *f=fopen(argv[2],"rb");if(!f)return 2;
 size_t n=fread(wire,1,sizeof(wire),f);bool good=!ferror(f);if(fclose(f))good=false;
 if(!good)return 2;
 uint8_t hash[32];pb_istream_t stream=pb_istream_from_buffer(wire,n);
 if(!strcmp(argv[1],"link")){puts(dw_link_decode(wire,n,&link)==DW_OK?"OK":"REJECT");return 0;}
 if(!strcmp(argv[1],"core"))good=pb_decode(&stream,xenova_drive_EpisodeCore_fields,&core)&&dw_episode_core_hash(&core,hash);
 else if(!strcmp(argv[1],"source"))good=pb_decode(&stream,xenova_drive_EpisodeSourceContext_fields,&source)&&dw_episode_source_hash(&source,hash);
 else if(!strcmp(argv[1],"plan"))good=dw_state_decode(wire,n,&state)==DW_OK&&dw_plan_hash(&state,hash);
 else if(!strcmp(argv[1],"goal"))good=pb_decode(&stream,xenova_drive_MissionExecution_fields,&goal)&&dw_selected_goal_hash(&goal,hash);
 else return 2;
 if(!good){puts("REJECT");return 0;}
 for(unsigned i=0;i<32;i++)printf("%02x",hash[i]);
 putchar('\n');return 0;
}
