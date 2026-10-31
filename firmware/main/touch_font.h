// Copyright 2026 fanzipei. SPDX-License-Identifier: Apache-2.0
#pragma once
#include "lvgl.h"
const lv_font_t *touch_font(int size);
int touch_glyph_width(uint32_t codepoint, int size);
bool touch_font_has_glyph(uint32_t codepoint, int size);
uint32_t touch_font_fallback_glyphs(void);
uint32_t touch_font_missing_renders(void);
uint32_t touch_font_missing_last(void);
