// Copyright 2026 fanzipei. SPDX-License-Identifier: Apache-2.0
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#define READING_IDLE_MS 10000U
#define READING_START_MS 5000U
#define READING_BOTTOM_MS 12000U
#define READING_SPEED_PX_S 10
#define READING_DRAG_THRESHOLD 12
#define READING_SWIPE_THRESHOLD 48
#define READING_HOLD_MS 600U
#define READING_HOLD_SLOP 16
#define READING_ACTION_SWIPE 80
#define READING_DOUBLE_MS 700U
#define READING_DOUBLE_DISTANCE 80
#define READING_FOCUS_GUARD_MS 2000U
typedef enum { READING_NONE, READING_TAP, READING_VERTICAL, READING_PREVIOUS, READING_NEXT, READING_DELETE, READING_SEND, READING_FOCUS } reading_gesture_t;
typedef struct {
    bool focused, down, autoplay, restart_pending, hold_eligible, hold_armed, tap_pending;
    int axis, direction, start_x, start_y, drag_zoom, gesture_dx, tap_x, tap_y;
    int32_t offset_milli, maximum, start_offset;
    uint32_t input_at, tick_at, hold_until, pressed_at, tap_at, focus_at;
} reading_t;

static inline int32_t reading_clamp(int32_t value,int32_t maximum) {
    return value<0?0:value>maximum?maximum:value;
}
static inline int32_t reading_y(const reading_t *r) { return r->offset_milli/1000; }
static inline void reading_activity(reading_t *r,uint32_t now) {
    r->input_at=r->tick_at=now; r->hold_until=now+READING_START_MS; r->autoplay=false;
    r->restart_pending=false;
}
static inline void reading_init(reading_t *r,uint32_t now) {
    memset(r,0,sizeof(*r)); r->direction=1; r->drag_zoom=256; reading_activity(r,now);
}
static inline void reading_bounds(reading_t *r,int32_t content_height,int viewport_height) {
    r->maximum=content_height>viewport_height?content_height-viewport_height:0;
    r->offset_milli=reading_clamp(r->offset_milli,r->maximum*1000);
}
static inline void reading_seek(reading_t *r,int32_t pixels) {
    r->offset_milli=reading_clamp(pixels,r->maximum)*1000;
}
static inline void reading_bottom(reading_t *r,uint32_t now) {
    r->direction=1; r->restart_pending=true; r->autoplay=false;
    r->hold_until=now+READING_BOTTOM_MS;
}
static inline void reading_press(reading_t *r,int x,int y,uint32_t now) {
    reading_activity(r,now); r->down=true; r->axis=0;
    r->start_x=x; r->start_y=y; r->start_offset=reading_y(r);
    r->pressed_at=now; r->hold_eligible=r->focused; r->hold_armed=false; r->gesture_dx=0;
    if(r->focused) r->tap_pending=false;
}
static inline void reading_cancel(reading_t *r,uint32_t now) {
    r->down=r->hold_eligible=r->hold_armed=r->tap_pending=false; r->gesture_dx=0; reading_activity(r,now);
}
static inline void reading_pointer_lost(reading_t *r,uint32_t now) {
    if(r->down) reading_cancel(r,now);
}
static inline void reading_focus(reading_t *r,bool focused,uint32_t now) {
    if(r->focused==focused) return;
    r->focused=focused; r->down=r->hold_eligible=r->hold_armed=r->tap_pending=false; r->gesture_dx=0;
    if(focused) { r->focus_at=now; r->hold_until=now+READING_START_MS; }
}
static inline void reading_move(reading_t *r,int x,int y,uint32_t now) {
    if(!r->down) return;
    reading_activity(r,now);
    int dx=x-r->start_x,dy=y-r->start_y;
    r->gesture_dx=dx;
    if(r->hold_eligible && !r->hold_armed) {
        if(abs(dx)>=READING_HOLD_SLOP || abs(dy)>=READING_HOLD_SLOP) r->hold_eligible=false;
        else if(now-r->pressed_at>=READING_HOLD_MS) r->hold_armed=true;
    }
    int threshold=r->hold_eligible || r->hold_armed?READING_HOLD_SLOP:READING_DRAG_THRESHOLD;
    if(!r->axis && (abs(dx)>=threshold || abs(dy)>=threshold)) {
        if(abs(dx)>abs(dy)*3/2) r->axis=1;
        else if(abs(dy)>abs(dx)*3/2) r->axis=2;
        else if(abs(dx)>=20 || abs(dy)>=20) r->axis=abs(dx)>abs(dy)?1:2;
    }
    if(r->axis) r->tap_pending=false;
    if(r->axis==2) reading_seek(r,r->start_offset-dy*256/(r->drag_zoom?r->drag_zoom:256));
}
static inline reading_gesture_t reading_release(reading_t *r,int x,int y,uint32_t now) {
    if(!r->down) return READING_NONE;
    reading_move(r,x,y,now); r->down=false; reading_activity(r,now);
    int dx=x-r->start_x,dy=y-r->start_y;
    if(r->axis==2) {
        r->direction=1;
        if(r->maximum && reading_y(r)==r->maximum) reading_bottom(r,now);
        return READING_VERTICAL;
    }
    if(r->hold_armed) {
        if(r->axis==1 && abs(dx)>=READING_ACTION_SWIPE) return dx<0?READING_DELETE:READING_SEND;
        return READING_NONE;
    }
    if(r->axis==1 && abs(dx)>=READING_SWIPE_THRESHOLD)
        return dx<0?READING_NEXT:READING_PREVIOUS;
    if(!r->axis && now-r->pressed_at>=READING_HOLD_MS) { r->tap_pending=false; return READING_NONE; }
    if(!r->axis && abs(dx)<READING_DRAG_THRESHOLD && abs(dy)<READING_DRAG_THRESHOLD) {
        if(r->focused && r->pressed_at-r->focus_at<READING_FOCUS_GUARD_MS) { r->tap_pending=false; return READING_NONE; }
        int tx=x-r->tap_x,ty=y-r->tap_y;
        if(!r->focused && r->tap_pending && r->pressed_at-r->tap_at<=READING_DOUBLE_MS && tx*tx+ty*ty<=READING_DOUBLE_DISTANCE*READING_DOUBLE_DISTANCE) {
            r->tap_pending=false; return READING_FOCUS;
        }
        r->tap_pending=!r->focused; r->tap_at=now; r->tap_x=x; r->tap_y=y;
        return READING_TAP;
    }
    r->tap_pending=false;
    return READING_NONE;
}
// Returns true exactly when inactivity enters fullscreen. A held finger, popup,
// reminder or sleeping display pauses both inactivity transition and playback.
static inline bool reading_tick(reading_t *r,uint32_t now,bool allowed) {
    uint32_t elapsed=now-r->tick_at; r->tick_at=now; r->autoplay=false;
    if(!allowed || r->down) return false;
    if(!r->focused && now-r->input_at>=READING_IDLE_MS) {
        reading_focus(r,true,now); return true;
    }
    if(!r->focused || !r->maximum || (int32_t)(now-r->hold_until)<0) return false;
    if(r->restart_pending) {
        reading_seek(r,0); r->restart_pending=false;
        r->hold_until=now+READING_START_MS; return false;
    }
    r->autoplay=true;
    if(elapsed>100) elapsed=100; // No jump after a slow BLE request or suspend.
    r->offset_milli+=(int32_t)elapsed*READING_SPEED_PX_S;
    if(r->offset_milli>=r->maximum*1000) {
        r->offset_milli=r->maximum*1000; reading_bottom(r,now);
    }
    return false;
}
