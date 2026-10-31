// Copyright 2026 fanzipei. SPDX-License-Identifier: Apache-2.0
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

typedef struct { const uint8_t *bitmap; unsigned width; } touch_fallback_glyph_t;
static inline uint32_t touch_font_u32(const uint8_t *p) {
    return (uint32_t)p[0]|(uint32_t)p[1]<<8|(uint32_t)p[2]<<16|(uint32_t)p[3]<<24;
}
static inline uint32_t touch_fallback_count(const uint8_t *data,size_t length) {
    if(length<8 || memcmp(data,"TFB1",4)) return 0;
    uint32_t count=touch_font_u32(data+4);
    return count<=(length-8)/16?count:0;
}
static inline bool touch_fallback_lookup(const uint8_t *data,size_t length,uint32_t cp,int size,
                                         touch_fallback_glyph_t *glyph) {
    if(size!=12 && size!=16) return false;
    uint32_t count=touch_fallback_count(data,length),lo=0,hi=count;
    while(lo<hi) {
        uint32_t mid=lo+(hi-lo)/2;
        uint32_t found=touch_font_u32(data+8+16*mid);
        if(found<cp) lo=mid+1; else hi=mid;
    }
    if(lo>=count) return false;
    const uint8_t *record=data+8+16*lo;
    if(touch_font_u32(record)!=cp) return false;
    uint32_t offset=touch_font_u32(record+(size==12?4:8));
    unsigned width=record[size==12?12:14]|(unsigned)record[size==12?13:15]<<8;
    size_t bytes=(width*size+7)/8;
    if(!width || width>(unsigned)size*2 || offset<8+16*count || offset>length || bytes>length-offset) return false;
    *glyph=(touch_fallback_glyph_t){.bitmap=data+offset,.width=width};
    return true;
}
