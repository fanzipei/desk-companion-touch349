// Copyright 2026 fanzipei. SPDX-License-Identifier: Apache-2.0
#include "motion.h"
#include "flip_logic.h"
#include "orientation_logic.h"
#include "i2c_bsp.h"
#include "esp_timer.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include <assert.h>
static QueueHandle_t flips;
static QueueHandle_t orientations;
static bool initial_flipped;
static orientation_t orientation;
static portMUX_TYPE lock=portMUX_INITIALIZER_UNLOCKED;
static bool enabled,allowed;
static flip_t reader;
static float sample[6];
static uint32_t errors,samples;
static esp_err_t write_reg(uint8_t reg,uint8_t value) {
    uint8_t data[2]={reg,value}; return i2c_master_transmit(imu_dev_handle,data,2,30);
}
static void motion_task(void *unused) {
    flip_t state; flip_init(&state,(uint32_t)(esp_timer_get_time()/1000));
    orientation_t upright; orientation_init(&upright,initial_flipped,(uint32_t)(esp_timer_get_time()/1000));
    TickType_t due=xTaskGetTickCount();
    for(;;) {
        uint8_t reg=0x35,raw[12]; float value[6];
        esp_err_t err=i2c_master_transmit_receive(imu_dev_handle,&reg,1,raw,sizeof(raw),15);
        if(err==ESP_OK) {
            // CTRL2: +/-4 g, CTRL3: +/-1024 degrees/s, little endian.
            for(int i=0;i<6;i++) value[i]=(int16_t)((uint16_t)raw[i*2]|((uint16_t)raw[i*2+1]<<8))*(i<3?4.0f:1024.0f)/32768;
            portENTER_CRITICAL(&lock); bool can_flip=allowed; portEXIT_CRITICAL(&lock);
            bool changed=flip_tick(&state,value[0],value[1],value[2],value[3],value[4],value[5],(uint32_t)(esp_timer_get_time()/1000),can_flip);
            bool rotated=orientation_tick(&upright,value[0],value[1],value[2],value[3],value[4],value[5],
                state.bias_z,state.ready,(uint32_t)(esp_timer_get_time()/1000));
            portENTER_CRITICAL(&lock); reader=state; orientation=upright; memcpy(sample,value,sizeof(sample)); samples++; portEXIT_CRITICAL(&lock);
            if(changed) { bool inverted=state.flipped; xQueueOverwrite(flips,&inverted); }
            if(rotated) { bool inverted=upright.flipped; xQueueOverwrite(orientations,&inverted); }
        } else {
            state.candidate=-1;
            upright.candidate=-1;
            portENTER_CRITICAL(&lock); errors++; portEXIT_CRITICAL(&lock);
        }
        vTaskDelayUntil(&due,pdMS_TO_TICKS(20));
    }
}
bool touch_motion_init(void) {
    // Both sensors at 112.1 Hz; read at 50 Hz. No reset of the shared I2C bus.
    esp_err_t err=write_reg(0x08,0);
    if(err==ESP_OK) err=write_reg(0x02,0x40); // auto-increment, little endian
    if(err==ESP_OK) err=write_reg(0x03,0x16); // accelerometer: 4 g, nominal 125 Hz
    if(err==ESP_OK) err=write_reg(0x04,0x66); // gyro: 1024 dps, 112.1 Hz
    if(err==ESP_OK) err=write_reg(0x06,0x33); // LPF mode 1 on both sensors
    if(err==ESP_OK) err=write_reg(0x08,0x03);
    if(err!=ESP_OK) { ESP_LOGW("touch_motion","QMI8658 configuration failed: %s",esp_err_to_name(err)); return false; }
    flips=xQueueCreate(1,sizeof(bool)); assert(flips);
    orientations=xQueueCreate(1,sizeof(bool)); assert(orientations);
    BaseType_t created=xTaskCreate(motion_task,"touch_motion",4096,NULL,4,NULL);
    enabled=created==pdPASS;
    return enabled;
}
void touch_motion_restore(bool value) { initial_flipped=value; }
bool touch_motion_orientation(bool *value) { return enabled && xQueueReceive(orientations,value,0)==pdTRUE; }
void touch_motion_allow(bool value) { portENTER_CRITICAL(&lock); allowed=value; portEXIT_CRITICAL(&lock); }
bool touch_motion_poll(bool *flipped) { return enabled && xQueueReceive(flips,flipped,0)==pdTRUE; }
void touch_motion_state(cJSON *imu) {
    portENTER_CRITICAL(&lock); flip_t state=reader; float value[6]; memcpy(value,sample,sizeof(value));
    uint32_t count=samples,failed=errors; bool can_flip=allowed; orientation_t upright=orientation; portEXIT_CRITICAL(&lock);
    cJSON_AddBoolToObject(imu,"gestures_enabled",enabled); cJSON_AddBoolToObject(imu,"ready",state.ready);
    cJSON_AddBoolToObject(imu,"flipped",state.flipped); cJSON_AddBoolToObject(imu,"allowed",can_flip);
    cJSON_AddNumberToObject(imu,"angle",state.angle); cJSON_AddNumberToObject(imu,"turn",state.turn);
    cJSON_AddNumberToObject(imu,"flips",state.flips); cJSON_AddNumberToObject(imu,"samples",count); cJSON_AddNumberToObject(imu,"errors",failed);
    cJSON_AddNumberToObject(imu,"settle_ms",FLIP_SETTLE_MS);
    cJSON_AddBoolToObject(imu,"orientation_ready",upright.initialized);
    cJSON_AddBoolToObject(imu,"orientation_flipped",upright.flipped);
    cJSON_AddBoolToObject(imu,"orientation_gravity",upright.gravity);
    cJSON_AddNumberToObject(imu,"orientation_changes",upright.changes);
    cJSON_AddNumberToObject(imu,"orientation_settle_ms",ORIENTATION_SETTLE_MS);
    cJSON_AddItemToObject(imu,"accel",cJSON_CreateFloatArray(value,3));
    cJSON_AddItemToObject(imu,"gyro",cJSON_CreateFloatArray(value+3,3));
}
