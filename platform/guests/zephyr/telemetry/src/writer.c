/* SPDX-License-Identifier: Apache-2.0 */
#include "writer.h"
#include <errno.h>
#include <inttypes.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

bool xt_utf8_valid(const char *value)
{
	if (!value) { return false; }
	const unsigned char *s = (const unsigned char *)value;
	while (*s) {
		unsigned n; uint32_t code, minimum;
		if (*s < 0x80) { ++s; continue; }
		if (*s >= 0xc2 && *s <= 0xdf) { n=1; code=*s & 31; minimum=0x80; }
		else if (*s >= 0xe0 && *s <= 0xef) { n=2; code=*s & 15; minimum=0x800; }
		else if (*s >= 0xf0 && *s <= 0xf4) { n=3; code=*s & 7; minimum=0x10000; }
		else { return false; }
		++s;
		while (n--) { if ((*s & 0xc0) != 0x80) { return false; } code=(code<<6)|(*s++ & 63); }
		if (code < minimum || code > 0x10ffff || (code>=0xd800 && code<=0xdfff)) { return false; }
	}
	return true;
}

void xt_raw(struct xt_writer *w, const char *format, ...)
{
	if (w->error) { return; }
	va_list args; va_start(args, format);
	int n = vsnprintf(w->data + w->used, w->size - w->used, format, args);
	va_end(args);
	if (n < 0) { w->error=-EIO; }
	else if ((size_t)n >= w->size-w->used) { w->error=-ENOSPC; }
	else { w->used+=(size_t)n; }
}

void xt_string(struct xt_writer *w, const char *s)
{
	if (!s) { xt_raw(w,"null"); return; }
	if (!xt_utf8_valid(s)) { w->error=-EILSEQ; return; }
	xt_raw(w,"\"");
	for (const unsigned char *p=(const unsigned char *)s; *p; ++p) {
		if (*p=='"' || *p=='\\') { xt_raw(w,"\\%c",*p); }
		else if (*p<32) { xt_raw(w,"\\u%04x",(unsigned)*p); }
		else { xt_raw(w,"%c",*p); }
	}
	xt_raw(w,"\"");
}
void xt_u64(struct xt_writer *w, uint64_t n) { xt_raw(w,"\"%" PRIu64 "\"",n); }
void xt_i64(struct xt_writer *w, int64_t n) { xt_raw(w,"\"%" PRId64 "\"",n); }
