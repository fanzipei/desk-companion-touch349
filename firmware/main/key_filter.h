// Copyright 2026 fanzipei. SPDX-License-Identifier: Apache-2.0
#pragma once
#include "touch_keys.h"
#define TOUCH_DEBOUNCE_MS 5
typedef void (*touch_edge_emit_t)(int key,int level,int64_t at);
typedef struct {
    buttons_t buttons;
    int stable[2],candidate[2];
    bool armed[2];
    int64_t changed[2];
} touch_key_filter_t;
static inline void touch_key_filter_init(touch_key_filter_t *f,int boot,int pwr,int64_t now) {
    memset(f,0,sizeof(*f));
    f->stable[0]=f->candidate[0]=boot; f->stable[1]=f->candidate[1]=pwr;
    for(int i=0;i<2;i++) { f->changed[i]=now; f->armed[i]=f->stable[i]!=0; }
}
static inline void touch_key_filter_actions(touch_key_filter_t *f,int64_t now,button_emit_t emit) {
    touch_keys_tick(&f->buttons,f->armed[0] && !f->stable[0],f->armed[1] && !f->stable[1],now,emit);
}
// Replay debounce deadlines using captured edge times, even after processing
// was delayed. Drain captured edges before advancing to the current time.
static inline void touch_key_filter_tick(touch_key_filter_t *f,int64_t now,button_emit_t emit,touch_edge_emit_t edge) {
    for(;;) {
        int next=-1;
        for(int i=0;i<2;i++) if(f->stable[i]!=f->candidate[i] && f->changed[i]+TOUCH_DEBOUNCE_MS<=now &&
            (next<0 || f->changed[i]<f->changed[next])) next=i;
        if(next<0) break;
        int64_t deadline=f->changed[next]+TOUCH_DEBOUNCE_MS;
        touch_key_filter_actions(f,deadline,emit);
        f->stable[next]=f->candidate[next];
        if(f->stable[next]) f->armed[next]=true;
        if(edge) edge(next,f->stable[next],deadline);
        touch_key_filter_actions(f,deadline,emit);
    }
    touch_key_filter_actions(f,now,emit);
}
static inline void touch_key_filter_raw(touch_key_filter_t *f,int key,int level,int64_t now,button_emit_t emit,touch_edge_emit_t edge) {
    touch_key_filter_tick(f,now,emit,edge);
    if(f->candidate[key]!=level) { f->candidate[key]=level; f->changed[key]=now; }
}
