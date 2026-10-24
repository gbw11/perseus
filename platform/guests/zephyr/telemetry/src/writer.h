/* SPDX-License-Identifier: Apache-2.0 */
/* A bounded UTF-8 JSON writer; any failure invalidates the entire packet. */
#ifndef XT_WRITER_H
#define XT_WRITER_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
struct xt_writer { char *data; size_t size, used; int error; };
void xt_raw(struct xt_writer *, const char *, ...);
void xt_string(struct xt_writer *, const char *);
void xt_u64(struct xt_writer *, uint64_t);
void xt_i64(struct xt_writer *, int64_t);
bool xt_utf8_valid(const char *);
#endif
