// Copyright 2026 fanzipei. SPDX-License-Identifier: Apache-2.0
// Existing WQY metrics are retained; missing Unicode uses SIL-OFL Noto CJK.
#include "touch_font.h"
#include "touch_fallback.h"
#include "u8g2.h"
#include <string.h>
extern const uint8_t fallback_start[] asm("_binary_touch_fallback_bin_start");
extern const uint8_t fallback_end[] asm("_binary_touch_fallback_bin_end");
static u8g2_t canvas;
static uint8_t canvas_pixels[128], bitmap[256];
static bool initialized;
static int canvas_size;
static uint32_t missing_renders, missing_last;
enum { GLYPH_WQY, GLYPH_NOTO, GLYPH_SPACE, GLYPH_CONTROL, GLYPH_MISSING };
typedef struct { uint32_t letter; unsigned width; int kind; const uint8_t *pixels; } glyph_t;
static uint8_t setup(u8x8_t *u, uint8_t message, uint8_t arg, void *data) {
    static const u8x8_display_info_t info={.tile_width=4,.tile_height=4,.pixel_width=32,.pixel_height=32};
    if(message==U8X8_MSG_DISPLAY_SETUP_MEMORY) u8x8_d_helper_display_setup_memory(u,&info);
    return 1;
}
static void prepare(int size) {
    if(!initialized) {
        u8g2_SetupDisplay(&canvas,setup,u8x8_cad_empty,NULL,NULL);
        u8g2_SetupBuffer(&canvas,canvas_pixels,4,u8g2_ll_hvline_vertical_top_lsb,U8G2_R0);
        initialized=true;
    }
    if(canvas_size!=size) {
        u8g2_SetFont(&canvas,size==12?u8g2_font_wqy12_t_gb2312b:u8g2_font_wqy16_t_gb2312b);
        u8g2_SetFontPosTop(&canvas); u8g2_SetFontMode(&canvas,1); canvas_size=size;
    }
}
static glyph_t resolve(uint32_t cp,int size) {
    static uint32_t cached_cp=UINT32_MAX;
    static int cached_size;
    static glyph_t cached;
    if(cp==cached_cp && size==cached_size) return cached;
    uint32_t original=cp;
    prepare(size);
    glyph_t result={.letter=cp,.kind=GLYPH_CONTROL};
    if(cp<32 || (cp>=0x7f && cp<0xa0) || cp==0xad || cp==0x61c || (cp>=0x200b && cp<=0x200f) ||
       (cp>=0x202a && cp<=0x202e) || (cp>=0x2060 && cp<=0x206f) || cp==0xfeff ||
       (cp>=0xfe00 && cp<=0xfe0f) || (cp>=0xe0100 && cp<=0xe01ef)) goto done;
    if(cp==0xa0 || cp==0x2007 || cp==0x2009 || cp==0x202f) {
        unsigned width=u8g2_GetGlyphWidth(&canvas,' ');
        if(cp==0x2009 || cp==0x202f) width=(width+1)/2;
        result=(glyph_t){.letter=cp,.kind=GLYPH_SPACE,.width=width?width:1}; goto done;
    }
    // Dot operators have the same visible dot when the source font lacks them.
    if(cp==0x2219 || cp==0x22c5) cp=0xb7;
    int width=cp<=65535?u8g2_GetGlyphWidth(&canvas,cp):0;
    if(width>0) {
        result=(glyph_t){.letter=cp,.kind=cp==' '?GLYPH_SPACE:GLYPH_WQY,.width=width}; goto done;
    }
    touch_fallback_glyph_t glyph;
    if(touch_fallback_lookup(fallback_start,(size_t)(fallback_end-fallback_start),cp,size,&glyph))
        result=(glyph_t){.letter=cp,.kind=GLYPH_NOTO,.width=glyph.width,.pixels=glyph.bitmap};
    else result=(glyph_t){.letter=cp,.kind=GLYPH_MISSING,.width=size};
done:
    cached_cp=original; cached_size=size; cached=result;
    return result;
}
int touch_glyph_width(uint32_t codepoint,int size) {
    int scale=size>=24?2:1;
    return resolve(codepoint,size/scale).width*scale;
}
bool touch_font_has_glyph(uint32_t cp,int size) { return resolve(cp,size>=24?size/2:size).kind!=GLYPH_MISSING; }
uint32_t touch_font_fallback_glyphs(void) { return touch_fallback_count(fallback_start,(size_t)(fallback_end-fallback_start)); }
uint32_t touch_font_missing_renders(void) { return missing_renders; }
uint32_t touch_font_missing_last(void) { return missing_last; }
static bool descriptor(const lv_font_t *font,lv_font_glyph_dsc_t *dsc,uint32_t letter,uint32_t next) {
    int size=font->line_height,scale=size>=24?2:1;
    glyph_t glyph=resolve(letter,size/scale);
    bool blank=glyph.kind==GLYPH_SPACE || glyph.kind==GLYPH_CONTROL;
    *dsc=(lv_font_glyph_dsc_t){.adv_w=glyph.width*scale,.box_w=blank?0:glyph.width*scale,
        .box_h=blank?0:size,.ofs_y=0,.bpp=1};
    return true;
}
static const uint8_t *pixels(const lv_font_t *font,uint32_t letter) {
    int size=font->line_height,scale=size>=24?2:1,base=size/scale;
    glyph_t glyph=resolve(letter,base);
    if(glyph.kind==GLYPH_SPACE || glyph.kind==GLYPH_CONTROL) return NULL;
    unsigned width=glyph.width*scale;
    if(glyph.kind==GLYPH_WQY) {
        prepare(base); u8g2_ClearBuffer(&canvas); u8g2_DrawGlyph(&canvas,0,1,glyph.letter);
    } else if(glyph.kind==GLYPH_MISSING) { missing_last=letter; missing_renders++; }
    memset(bitmap,0,sizeof(bitmap));
    for(int y=0;y<size;y++) for(unsigned x=0;x<width;x++) {
        int gx=x/scale, gy=y/scale;
        bool set=false;
        if(glyph.kind==GLYPH_WQY) set=!!(canvas_pixels[(gy/8)*32+gx] & (1<<(gy%8)));
        else if(glyph.kind==GLYPH_NOTO) {
            unsigned bit=gy*glyph.width+gx; set=!!(glyph.pixels[bit/8] & (0x80>>(bit%8)));
        } else set=(gx==1 || gx==(int)glyph.width-2 || gy==1 || gy==base-2) &&
            gx>=1 && gx<(int)glyph.width-1 && gy>=1 && gy<base-1;
        if(set) { unsigned bit=y*width+x; bitmap[bit/8]|=0x80>>(bit%8); }
    }
    return bitmap;
}
static const lv_font_t fonts[]={
    {.get_glyph_dsc=descriptor,.get_glyph_bitmap=pixels,.line_height=12},
    {.get_glyph_dsc=descriptor,.get_glyph_bitmap=pixels,.line_height=16},
    {.get_glyph_dsc=descriptor,.get_glyph_bitmap=pixels,.line_height=24},
    {.get_glyph_dsc=descriptor,.get_glyph_bitmap=pixels,.line_height=32},
};
const lv_font_t *touch_font(int size) { return &fonts[size==12?0:size==24?2:size==32?3:1]; }
