/* Copyright 2026 fanzipei
 * SPDX-License-Identifier: Apache-2.0
 */
#ifndef COMPANION_BUTTON_LOGIC_H
#define COMPANION_BUTTON_LOGIC_H
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#define BUTTON_DOUBLE_MS 350
#define BUTTON_LONG_MS 1000
typedef enum { BUTTON_NEXT, BUTTON_PREVIOUS, BUTTON_OPEN, BUTTON_TOGGLE, BUTTON_DELETE, BUTTON_PIN } button_action_t;
typedef struct { bool down, long_fired; int64_t pressed_at, pending_at; } button_state_t;
typedef struct { button_state_t key[2]; } buttons_t;
typedef void (*button_emit_t)(button_action_t action);
static inline void buttons_reset(buttons_t *buttons) { memset(buttons,0,sizeof(*buttons)); }
#endif
