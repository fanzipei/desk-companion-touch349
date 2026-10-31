// Copyright 2026 fanzipei. SPDX-License-Identifier: Apache-2.0
#pragma once
#include <stdbool.h>
#include "cJSON.h"
#ifdef __cplusplus
extern "C" {
#endif
bool touch_motion_init(void);
void touch_motion_restore(bool flipped);
bool touch_motion_orientation(bool *flipped);
void touch_motion_allow(bool enabled);
bool touch_motion_poll(bool *flipped);
void touch_motion_state(cJSON *imu);
#ifdef __cplusplus
}
#endif
