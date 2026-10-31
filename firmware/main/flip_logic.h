// Copyright 2026 fanzipei. SPDX-License-Identifier: Apache-2.0
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <math.h>
#define FLIP_SETTLE_MS 650U
#define FLIP_REARM_MS 1800U
typedef struct {
    bool ready,flipped,gravity_reference;
    int candidate,calibration_samples;
    uint32_t last_at,calibration_at,candidate_at,last_flip,quiet_at,flips;
    float reference_x,reference_y,bias_z,angle,turn;
} flip_t;
static inline float flip_wrap(float value) {
    while(value>180) value-=360;
    while(value<-180) value+=360;
    return value;
}
static inline void flip_init(flip_t *f,uint32_t now) {
    memset(f,0,sizeof(*f)); f->candidate=-1; f->last_at=f->calibration_at=f->quiet_at=now;
}
static inline bool flip_tick(flip_t *f,float ax,float ay,float az,float gx,float gy,float gz,uint32_t now,bool allowed) {
    uint32_t elapsed=now-f->last_at; f->last_at=now;
    float norm=ax*ax+ay*ay+az*az;
    float speed=sqrtf(gx*gx+gy*gy+gz*gz);
    if(!isfinite(norm) || !isfinite(speed)) { f->candidate=-1; return false; }
    if(!f->ready) {
        if(norm<0.72f || norm>1.32f || speed>12 || elapsed>200) {
            f->calibration_samples=0; f->reference_x=f->reference_y=f->bias_z=0; f->calibration_at=now; return false;
        }
        f->reference_x+=ax; f->reference_y+=ay; f->bias_z+=gz; f->calibration_samples++;
        if(now-f->calibration_at<600 || f->calibration_samples<20) return false;
        f->reference_x/=f->calibration_samples; f->reference_y/=f->calibration_samples; f->bias_z/=f->calibration_samples;
        f->gravity_reference=f->reference_x*f->reference_x+f->reference_y*f->reference_y>0.12f;
        f->ready=true; f->quiet_at=now; return false;
    }
    if(elapsed>200) { f->candidate=-1; return false; }
    float z=gz-f->bias_z;
    if(fabsf(z)>1.5f) { f->angle=flip_wrap(f->angle+z*elapsed/1000); f->turn+=z*elapsed/1000; }
    float projection=ax*ax+ay*ay;
    if(f->gravity_reference && projection>0.12f && norm>0.72f && norm<1.32f) {
        // Gravity expressed in sensor coordinates turns opposite to the body.
        float absolute=atan2f(f->reference_y*ax-f->reference_x*ay,f->reference_x*ax+f->reference_y*ay)*57.2957795f;
        f->angle=flip_wrap(f->angle+0.08f*flip_wrap(absolute-f->angle));
    }
    int target=fabsf(f->angle)<25?0:fabsf(f->angle)>155?1:-1;
    bool quiet=norm>0.72f && norm<1.32f && speed<18;
    if(target==(int)f->flipped && quiet) {
        if(now-f->quiet_at>750) {
            f->turn=0;
            if(speed<4) f->bias_z=0.995f*f->bias_z+0.005f*gz;
        }
    } else f->quiet_at=now;
    if(!allowed || !quiet || target<0 || target==(int)f->flipped || fabsf(f->turn)<100 || now-f->last_flip<FLIP_REARM_MS) {
        f->candidate=-1; return false;
    }
    if(f->candidate!=target) { f->candidate=target; f->candidate_at=now; return false; }
    if(now-f->candidate_at<FLIP_SETTLE_MS) return false;
    f->flipped=target!=0; f->last_flip=now; f->turn=0; f->candidate=-1; f->quiet_at=now; f->flips++;
    return true;
}
