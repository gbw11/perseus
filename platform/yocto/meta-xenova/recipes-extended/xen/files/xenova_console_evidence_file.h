/* SPDX-License-Identifier: MIT */
/* Bounded filtered scratch file, never the PV/UART stream. The sole writer
 * stamps each truncation so inode/offset reuse cannot imply continuity. */
#ifndef XENOVA_CONSOLE_EVIDENCE_FILE_H
#define XENOVA_CONSOLE_EVIDENCE_FILE_H
#include "xenova_console_evidence.h"
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
struct xdlog_file {
 int fd;
 struct xdlog *filter;
 int (*write_all)(int,const char *,size_t);
};
static inline int xdlog_file_write(void *context,const char *bytes,size_t count)
{
 struct xdlog_file *f=context;struct xdlog *s=f->filter;struct stat st;
 const off_t limit=256*1024;
 if(count>256||fstat(f->fd,&st)||st.st_size<0)return -1;
 if(!s->stream_generation||st.st_size>limit-(off_t)count){
  struct timespec now;
  if(clock_gettime(CLOCK_MONOTONIC,&now)||now.tv_sec<0||s->stream_generation==UINT64_MAX)return -1;
  uint64_t next=s->stream_generation?s->stream_generation+1:
   (uint64_t)now.tv_sec*1000000000+(uint64_t)now.tv_nsec;
  if(!next)return -1;
  /* An interrupted header must force a fresh initialization on retry. */
  s->stream_generation=0;
  if(fchmod(f->fd,0600)||ftruncate(f->fd,0)||lseek(f->fd,0,SEEK_SET)<0)return -1;
  char header[64];int n=snprintf(header,sizeof(header),"XDIAG_STREAM %llu\n",(unsigned long long)next);
  if(f->write_all(f->fd,header,(size_t)n))return -1;
  if(s->binding[0]&&f->write_all(f->fd,s->binding,85))return -1;
  s->stream_generation=next;
 }
 return f->write_all(f->fd,bytes,count);
}
#endif
