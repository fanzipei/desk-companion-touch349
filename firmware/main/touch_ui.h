// Copyright 2026 fanzipei. SPDX-License-Identifier: Apache-2.0
#pragma once
#include <stdbool.h>
#include "display_logic.h"
#include "cJSON.h"
#ifdef __cplusplus
extern "C" {
#endif
typedef struct {
    const char *text, *identity, *time, *toast, *source;
    int mode, index, count, unread, page, pages, battery;
    bool connected, pinned, news_alert;
    uint32_t news_alert_count;
    display_settings_t display;
} touch_view_t;
typedef struct { const char *action; char identity[48]; } touch_command_t;
void touch_board_init(void);
void touch_board_power(bool enabled);
bool touch_board_touched(void);
bool touch_board_awake(void);
void touch_board_flipped(bool flipped);
void touch_board_state(cJSON *reply);
const uint16_t *touch_board_frame(void);
void touch_ui_init(void);
void touch_ui_tick(void);
bool touch_ui_motion_allowed(void);
bool touch_ui_fullscreen(void);
bool touch_ui_action(touch_command_t *command);
void touch_ui_render(const touch_view_t *view);
int touch_ui_text(const char *text, int page, const display_settings_t *settings, bool render);
int touch_ui_lines(const display_settings_t *settings);
bool touch_ui_local_action(const char *action);
void touch_ui_activity(void);
void touch_ui_reset_position(int page);
bool touch_ui_step(int direction);
int touch_ui_page(void);
const char *touch_ui_delete_identity(void);
void touch_ui_state(cJSON *reply);
bool touch_ui_frame(cJSON *reply);
#ifdef __cplusplus
}
#endif
