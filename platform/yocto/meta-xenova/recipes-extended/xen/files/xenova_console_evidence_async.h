/* SPDX-License-Identifier: MIT */
/* Optional evidence only. The PV/PTY owner never opens, rotates or writes files,
 * waits for queue space, or joins this worker. Loss is allowed, attribution is not.
 * Jobs own bytes and binding; no console/domain pointers cross the queue. */
#ifndef XENOVA_CONSOLE_EVIDENCE_ASYNC_H
#define XENOVA_CONSOLE_EVIDENCE_ASYNC_H
#include "xenova_console_evidence_file.h"
#include <fcntl.h>
#include <errno.h>
#include <pthread.h>
#include <poll.h>
#include <stdlib.h>
#include <sys/eventfd.h>
#define XDLOG_QUEUE 64
#define XDLOG_FILES 32
struct xdlog_job { char path[256],binding[86],bytes[256];size_t count; };
typedef void (*xdlog_sink)(const struct xdlog_job *);
static struct {
 pthread_mutex_t lock;
 int wake;
 struct xdlog_job jobs[XDLOG_QUEUE];
 size_t first,count;
 bool ready;
 xdlog_sink sink;
} xdlog_async={.lock=PTHREAD_MUTEX_INITIALIZER,.wake=-1};

static int xdlog_write_all(int fd,const char *bytes,size_t count)
{
 while(count){ssize_t n=write(fd,bytes,count);if(n<0&&errno==EINTR)continue;
  if(n<=0)return -1;
  bytes+=n;count-=(size_t)n;}
 return 0;
}
static void xdlog_file_sink(const struct xdlog_job *job)
{
 /* Only the I/O worker touches this bounded descriptor cache. O_NOFOLLOW and
  * regular-file validation also prevent a substituted FIFO from blocking open. */
 static struct {char path[256];int fd;struct xdlog filter;} files[XDLOG_FILES];
 static size_t victim;
 size_t slot;
 for(slot=0;slot<XDLOG_FILES;slot++)if(!strcmp(files[slot].path,job->path))break;
 if(slot==XDLOG_FILES){
  slot=victim++%XDLOG_FILES;
  if(files[slot].path[0])close(files[slot].fd);
  memset(&files[slot],0,sizeof(files[slot]));
  int fd=open(job->path,O_WRONLY|O_CREAT|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC,0600);
  struct stat st;
  if(fd<0)return;
  if(fstat(fd,&st)||!S_ISREG(st.st_mode)||st.st_uid!=0){close(fd);return;}
  files[slot].fd=fd;strcpy(files[slot].path,job->path);
 }
 struct xdlog *filter=&files[slot].filter;
 if(strcmp(filter->binding,job->binding)){
  memcpy(filter->binding,job->binding,sizeof(filter->binding));
 }
 struct xdlog_file file={.fd=files[slot].fd,.filter=filter,.write_all=xdlog_write_all};
 /* Every fact repeats its original binding. A delayed old job can never borrow
  * the most recently observed execution, including when a domid is reused. */
 if((job->binding[0]&&xdlog_file_write(&file,job->binding,85))||
    xdlog_file_write(&file,job->bytes,job->count)){
  close(files[slot].fd);memset(&files[slot],0,sizeof(files[slot]));
 }
}
static void *xdlog_writer(void *unused)
{
 (void)unused;
 for(;;){
  pthread_mutex_lock(&xdlog_async.lock);
  if(!xdlog_async.count){
   pthread_mutex_unlock(&xdlog_async.lock);
   struct pollfd fd={.fd=xdlog_async.wake,.events=POLLIN};
   if(poll(&fd,1,-1)<0&&errno!=EINTR)return NULL;
   uint64_t token;ssize_t got=read(xdlog_async.wake,&token,sizeof(token));
   if(got<0&&errno!=EAGAIN&&errno!=EINTR)return NULL;
   continue;
  }
  struct xdlog_job job=xdlog_async.jobs[xdlog_async.first];
  xdlog_async.first=(xdlog_async.first+1)%XDLOG_QUEUE;xdlog_async.count--;
  pthread_mutex_unlock(&xdlog_async.lock);
  xdlog_async.sink(&job);
 }
 return NULL;
}
static int xdlog_async_start(xdlog_sink sink)
{
 pthread_attr_t attr;pthread_t thread;
 if(xdlog_async.ready)return 0;
 if(pthread_attr_init(&attr))return -1;
 xdlog_async.wake=eventfd(0,EFD_NONBLOCK|EFD_CLOEXEC);
 if(xdlog_async.wake<0){pthread_attr_destroy(&attr);return -1;}
 int rc=pthread_attr_setstacksize(&attr,64*1024);
 if(!rc)rc=pthread_attr_setdetachstate(&attr,PTHREAD_CREATE_DETACHED);
 xdlog_async.sink=sink;
 if(!rc)rc=pthread_create(&thread,&attr,xdlog_writer,NULL);
 pthread_attr_destroy(&attr);
 if(rc){close(xdlog_async.wake);xdlog_async.wake=-1;}
 xdlog_async.ready=!rc;return rc;
}
struct xdlog_enqueue {struct xdlog *filter;const char *directory,*suffix;unsigned domid,attempts;};
static int xdlog_enqueue(void *context,const char *bytes,size_t count)
{
 struct xdlog_enqueue *e=context;
 /* Parsing is already bounded by one ring read; cap queue attempts too. Never
  * log a failure per fact, which could turn evidence flood into a log flood. */
 if(!xdlog_async.ready||!e->filter->binding[0]||count>256||e->attempts++>=8)goto drop;
 struct xdlog_job job={.count=count};
 int n=snprintf(job.path,sizeof(job.path),"%s/guest-domid-%u%s.log",e->directory,e->domid,e->suffix);
 if(n<0||(size_t)n>=sizeof(job.path))goto drop;
 memcpy(job.binding,e->filter->binding,sizeof(job.binding));memcpy(job.bytes,bytes,count);
 if(pthread_mutex_trylock(&xdlog_async.lock))goto drop;
 if(xdlog_async.count==XDLOG_QUEUE){pthread_mutex_unlock(&xdlog_async.lock);goto drop;}
 xdlog_async.jobs[(xdlog_async.first+xdlog_async.count)%XDLOG_QUEUE]=job;xdlog_async.count++;
 pthread_mutex_unlock(&xdlog_async.lock);
 /* Nonblocking eventfd avoids the condition-variable implementation's own
  * waiter locks. A full notification counter already implies a pending wake. */
 uint64_t token=1;ssize_t notified=write(xdlog_async.wake,&token,sizeof(token));
 (void)notified; /* A notification failure is not a durable-write success. */
 return 0;
drop:
 e->filter->dropped++;return -1;
}
#endif
