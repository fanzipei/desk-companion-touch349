// Copyright 2026 fanzipei. SPDX-License-Identifier: Apache-2.0
#pragma once
#include "touch_keys.h"
#include "cJSON.h"
void touch_key_input_init(void);
bool touch_key_input_held(void);
bool touch_key_input_pop(button_action_t *action);
void touch_key_input_state(cJSON *reply);
