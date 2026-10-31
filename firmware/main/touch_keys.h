// Copyright 2026 fanzipei. SPDX-License-Identifier: Apache-2.0
#pragma once
#include "button_logic.h"
#define TOUCH_KEY_POWER ((button_action_t)6)
#define TOUCH_KEY_WAKE ((button_action_t)7)
#define TOUCH_POWER_HOLD_MS 3000
#define TOUCH_BOOT_DOUBLE_MS 500
// BOOT: single=open on PC, double=toggle note/news, hold=delete.
// Delay singles until the double-click window closes. PWR is power-only.
static inline void touch_boot_tick(button_state_t *key,bool down,int64_t ms,button_emit_t emit) {
    if(down!=key->down) {
        key->down=down;
        if(down) {
            if(key->pending_at && ms-key->pending_at>TOUCH_BOOT_DOUBLE_MS) {
                key->pending_at=0; emit(BUTTON_OPEN);
            }
            key->pressed_at=ms; key->long_fired=false;
        } else if(!key->long_fired) {
            // Expired first clicks are flushed on the second press, so an
            // existing pending click means that press began within the window.
            if(key->pending_at) {
                key->pending_at=0; emit(BUTTON_TOGGLE);
            } else {
                if(key->pending_at) emit(BUTTON_OPEN);
                key->pending_at=ms;
            }
        }
    }
    if(key->down && !key->long_fired && ms-key->pressed_at>=BUTTON_LONG_MS) {
        key->long_fired=true; key->pending_at=0; emit(BUTTON_DELETE);
    }
    if(!key->down && key->pending_at && ms-key->pending_at>TOUCH_BOOT_DOUBLE_MS) {
        key->pending_at=0; emit(BUTTON_OPEN);
    }
}
static inline void touch_keys_tick(buttons_t *buttons,bool boot,bool pwr,int64_t ms,button_emit_t emit) {
    touch_boot_tick(&buttons->key[0],boot,ms,emit);
    button_state_t *key=&buttons->key[1];
    if(pwr) {
        if(!key->down) { key->down=true; key->pressed_at=ms; key->long_fired=false; }
        if(ms-key->pressed_at>=TOUCH_POWER_HOLD_MS) {
            if(!key->long_fired) { key->long_fired=true; emit(TOUCH_KEY_POWER); }
        }
    } else if(key->down) {
        key->down=false;
        if(!key->long_fired) emit(TOUCH_KEY_WAKE);
    }
}
