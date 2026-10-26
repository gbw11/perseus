/* SPDX-License-Identifier: Apache-2.0 */
/* UTC quality lease, independent of all scheduling/deadline clocks. */
#include <xenova/telemetry_core.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
int xt_utc_update(struct xt_utc *c,uint64_t request,uint64_t received,uint64_t utc,
 uint64_t error,uint32_t ppm,const char *source)
{
 if(!c)return -EINVAL;
 bool prior=c->valid;
 c->valid=false;
 if(received<request||received-request>5000||error>10000||ppm>1000||
    utc<1577836800000ULL||utc>4102444800000ULL||!source||strlen(source)>31||!source[0])return -ERANGE;
 if(prior&&received>=c->at_ms&&received<c->expires_ms){
  uint64_t predicted=c->unix_ms+(received-c->at_ms);
  uint64_t difference=utc>predicted?utc-predicted:predicted-utc;
  uint64_t allowed=c->uncertainty_ms+error+(received-request)+
   ((received-c->at_ms)*c->drift_ppm+999999)/1000000+1;
  if(difference>allowed)return -EAGAIN; /* invalidate jump; next correspondence reacquires */
 }
 *c=(struct xt_utc){.at_ms=received,.unix_ms=utc,.uncertainty_ms=error+(received-request)+1,
  .expires_ms=received+30000,.drift_ppm=ppm,.valid=true};
 strcpy(c->source,source);return 0;
}
int xt_utc_read(const struct xt_utc *c,uint64_t sent,char iso[25],uint64_t *error)
{
 if(!c||!iso||!error)return -EINVAL;
 if(!c->valid||sent<c->at_ms||sent>=c->expires_ms)return -ENODATA;
 uint64_t age=sent-c->at_ms,ms=c->unix_ms+age;
 *error=c->uncertainty_ms+(age*c->drift_ppm+999999)/1000000;
 /* Gregorian civil date from days since Unix epoch; bounded to 2020..2100. */
 int64_t z=(int64_t)(ms/86400000)+719468;
 int64_t era=z/146097;unsigned doe=(unsigned)(z-era*146097);
 unsigned yoe=(doe-doe/1460+doe/36524-doe/146096)/365;
 int year=(int)(yoe+era*400);unsigned doy=doe-(365*yoe+yoe/4-yoe/100);
 unsigned mp=(5*doy+2)/153,day=doy-(153*mp+2)/5+1,month=mp+(mp<10?3:-9);
 year+=month<=2;unsigned seconds=(unsigned)(ms/1000%86400);
 int n=snprintf(iso,25,"%04d-%02u-%02uT%02u:%02u:%02u.%03uZ",year,month,day,
  seconds/3600,seconds/60%60,seconds%60,(unsigned)(ms%1000));
 return n==24?0:-ERANGE;
}
void xt_collection_result(struct xt_counters *c,bool failed,uint64_t end)
{
 c->collection_degraded=failed;
 if(failed){if(c->collection_failures!=UINT64_MAX)c->collection_failures++;}
 else{c->has_success=true;c->last_success_ms=end;}
}
