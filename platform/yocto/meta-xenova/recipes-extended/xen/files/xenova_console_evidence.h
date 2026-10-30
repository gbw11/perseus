/* SPDX-License-Identifier: MIT */
/* Filter only structural evidence in the existing xenconsoled owner. Console
 * forwarding is untouched. Bounded lines; no arbitrary text/register dump. */
#ifndef XENOVA_CONSOLE_EVIDENCE_H
#define XENOVA_CONSOLE_EVIDENCE_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
struct xdlog {
 char line[256],binding[86];size_t used;bool overflow;
 uint64_t dropped,stream_generation;
};
typedef int (*xdlog_emit)(void *,const char *,size_t);
static bool xdlog_uuid(const char *s){
 for(unsigned i=0;i<36;i++){
  if(i==8||i==13||i==18||i==23){if(s[i]!='-')return false;}
  else if(!((s[i]>='0'&&s[i]<='9')||(s[i]>='a'&&s[i]<='f')))return false;
 }return true;
}
static int xdlog_line(struct xdlog *s,xdlog_emit emit,void *context){
 char *p=strstr(s->line,"XDIAG_BIND ");
 if(p&&strlen(p)==85&&p[47]==' '&&xdlog_uuid(p+11)&&xdlog_uuid(p+48)){
  memcpy(s->binding,p,85);s->binding[85]=0;return emit(context,p,85);
 }
 p=strstr(s->line,">>> ZEPHYR FATAL ERROR ");
 if(p){unsigned code;char out[48];if(sscanf(p,">>> ZEPHYR FATAL ERROR %u:",&code)==1&&code<=9999){
  int n=snprintf(out,sizeof(out),"XDIAG_FATAL %u\n",code);return emit(context,out,(size_t)n);
 }}
 p=strstr(s->line,"XDIAG_EVENT ");
 if(p){unsigned long long seq,ms;int code;char out[160];
  if(sscanf(p,"XDIAG_EVENT {\"seq\":%llu,\"uptime_ms\":%llu,\"code\":%d,",&seq,&ms,&code)==3&&seq&&code<0){
   int n=snprintf(out,sizeof(out),"XDIAG_FACT %llu %llu %d\n",seq,ms,code);return emit(context,out,(size_t)n);
  }
 }
 return 0;
}
static int xdlog_feed(struct xdlog *s,const char *bytes,size_t count,xdlog_emit emit,void *context){
 int rc=0;
 for(size_t i=0;i<count;i++){
  if(bytes[i]=='\n'){
   if(s->used&&s->line[s->used-1]=='\r')s->used--;
   if(s->overflow)s->dropped++;
   else{s->line[s->used++]='\n';s->line[s->used]=0;int r=xdlog_line(s,emit,context);if(r&&!rc)rc=r;}
   s->used=0;s->overflow=false;
  }else if(s->used<sizeof(s->line)-2)s->line[s->used++]=bytes[i];
  else s->overflow=true;
 }
 return rc;
}
#endif
