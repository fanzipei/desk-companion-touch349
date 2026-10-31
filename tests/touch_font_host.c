// Copyright 2026 fanzipei. SPDX-License-Identifier: Apache-2.0
// Exercise the production callbacks, using only an LVGL type shim on the host.
#include "touch_font.h"
#include <stdio.h>

int main(void) {
    unsigned cp;int size;
    while(scanf("%u %d",&cp,&size)==2) {
        const lv_font_t *font=touch_font(size);
        lv_font_glyph_dsc_t dsc;
        if(!font->get_glyph_dsc(font,&dsc,cp,0)) return 2;
        const uint8_t *pixels=font->get_glyph_bitmap(font,cp);
        printf("%u %d %d %u %u %u ",cp,size,touch_font_has_glyph(cp,size),dsc.adv_w,dsc.box_w,dsc.box_h);
        unsigned bytes=(dsc.box_w*dsc.box_h+7)/8;
        if(bytes>256 || (bytes && !pixels)) return 3;
        for(unsigned i=0;i<bytes;i++) printf("%02x",pixels[i]);
        printf("\n");
    }
    return 0;
}
