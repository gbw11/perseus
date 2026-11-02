/* SPDX-License-Identifier: Apache-2.0 */
#ifndef XENOVA_STARTER_DIAGNOSTICS_H
#define XENOVA_STARTER_DIAGNOSTICS_H
#include <stdint.h>
#include <stddef.h>
#if defined(CONFIG_XENOVA_DIAGNOSTICS)
void starter_diag_network_result(int rc);
void starter_diag_boot(const char *stage, int rc);
#else
static inline void starter_diag_network_result(int rc) { (void)rc; }
static inline void starter_diag_boot(const char *stage, int rc) { (void)stage; (void)rc; }
#endif
#if defined(CONFIG_XENOVA_DIAGNOSTICS_EXERCISE)
struct shell;
int starter_exercise_start(void);
int starter_exercise_stop(int64_t deadline_ms);
int starter_exercise_cmd(const struct shell *, size_t argc, char **argv);
#else
static inline int starter_exercise_start(void) { return 0; }
static inline int starter_exercise_stop(int64_t deadline_ms) { (void)deadline_ms; return 0; }
#endif
#endif
