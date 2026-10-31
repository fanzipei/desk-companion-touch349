// Copyright 2026 fanzipei. SPDX-License-Identifier: Apache-2.0
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <math.h>

#define ORIENTATION_SETTLE_MS 300U
typedef struct {
    bool initialized, flipped, gravity;
    int candidate;
    uint32_t last_at, candidate_at, changes;
    float angle;
} orientation_t;

static inline float orientation_wrap(float value) {
    while(value>180) value-=360;
    while(value<-180) value+=360;
    return value;
}
static inline void orientation_init(orientation_t *o,bool flipped,uint32_t now) {
    *o=(orientation_t){.flipped=flipped,.candidate=-1,.last_at=now,.angle=flipped?180:0};
}
static inline bool orientation_tick(orientation_t *o,float ax,float ay,float az,
        float gx,float gy,float gz,float bias_z,bool gyro_ready,uint32_t now) {
    uint32_t elapsed=now-o->last_at; o->last_at=now;
    float norm=ax*ax+ay*ay+az*az, speed=gx*gx+gy*gy+gz*gz;
    if(!isfinite(norm) || !isfinite(speed) || !isfinite(bias_z) || elapsed>200) {
        o->candidate=-1; return false;
    }
    if(gyro_ready && fabsf(gz-bias_z)>1.5f)
        o->angle=orientation_wrap(o->angle+(gz-bias_z)*elapsed/1000);
    o->gravity=norm>0.72f && norm<1.32f && ax*ax+ay*ay>0.12f;
    if(o->gravity) {
        // Rev1.1 landscape: negative sensor Y is normal; positive Y needs 180°.
        float absolute=atan2f(-ax,-ay)*57.2957795f;
        if(!o->initialized) o->angle=absolute;
        else o->angle=orientation_wrap(o->angle+0.15f*orientation_wrap(absolute-o->angle));
        o->initialized=true;
    } else if(gyro_ready) o->initialized=true;
    int target=fabsf(o->angle)<65?0:fabsf(o->angle)>115?1:-1;
    if(!o->initialized || norm<0.72f || norm>1.32f || speed>625 || target<0 || target==(int)o->flipped) {
        o->candidate=-1; return false;
    }
    if(o->candidate!=target) { o->candidate=target; o->candidate_at=now; return false; }
    if(now-o->candidate_at<ORIENTATION_SETTLE_MS) return false;
    o->flipped=target!=0; o->candidate=-1; o->changes++; return true;
}
