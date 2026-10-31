// Copyright 2026 fanzipei. SPDX-License-Identifier: Apache-2.0
#include "key_input.h"
#include "key_filter.h"
#include <assert.h>
#include <inttypes.h>
#include "driver/gpio.h"
#include "esp_attr.h"
#include "esp_intr_alloc.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "soc/gpio_struct.h"

typedef struct { int key,level; int64_t at; } edge_t;
static QueueHandle_t actions,raw_edges,traces;
static DRAM_ATTR StaticQueue_t raw_control;
static DRAM_ATTR uint8_t raw_storage[64*sizeof(edge_t)];
static portMUX_TYPE lock=portMUX_INITIALIZER_UNLOCKED;
static struct {
    int levels[2]; uint32_t edges[2],raw[2],emitted,dropped,raw_dropped,trace_dropped,max_gap_ms;
} diagnostic={.levels={1,1}};

static void IRAM_ATTR input_edge(void *arg) {
    int key=(int)(intptr_t)arg;
    edge_t edge={.key=key,.level=(GPIO.in>>(key?16:0))&1,.at=esp_timer_get_time()};
    BaseType_t woken=pdFALSE;
    bool sent=xQueueSendFromISR(raw_edges,&edge,&woken)==pdTRUE;
    portENTER_CRITICAL_ISR(&lock);
    diagnostic.raw[key]++; if(!sent) diagnostic.raw_dropped++;
    portEXIT_CRITICAL_ISR(&lock);
    if(woken) portYIELD_FROM_ISR();
}
static void emit(button_action_t action) {
    bool sent=xQueueSend(actions,&action,0)==pdTRUE;
    portENTER_CRITICAL(&lock);
    diagnostic.emitted++; if(!sent) diagnostic.dropped++;
    portEXIT_CRITICAL(&lock);
}
static void stable_edge(int key,int level,int64_t at) {
    edge_t edge={.key=key,.level=level,.at=at};
    bool sent=xQueueSend(traces,&edge,0)==pdTRUE;
    portENTER_CRITICAL(&lock);
    diagnostic.levels[key]=level; diagnostic.edges[key]++;
    if(!sent) diagnostic.trace_dropped++;
    portEXIT_CRITICAL(&lock);
}
static void sample(void *unused) {
    touch_key_filter_t filter;
    touch_key_filter_init(&filter,gpio_get_level(0),gpio_get_level(16),esp_timer_get_time()/1000);
    portENTER_CRITICAL(&lock);
    diagnostic.levels[0]=filter.stable[0]; diagnostic.levels[1]=filter.stable[1];
    portEXIT_CRITICAL(&lock);
    TickType_t due=xTaskGetTickCount(); int64_t last=esp_timer_get_time()/1000;
    for(;;) {
        // Capture the tick before draining: an edge arriving just after the
        // drain must never be replayed behind an already advanced clock.
        int64_t now=esp_timer_get_time()/1000;
        edge_t edge;
        while(xQueueReceive(raw_edges,&edge,0)==pdTRUE) {
            edge.at/=1000;
            edge_t raw_trace=edge; raw_trace.key|=2;
            if(xQueueSend(traces,&raw_trace,0)!=pdTRUE) {
                portENTER_CRITICAL(&lock); diagnostic.trace_dropped++; portEXIT_CRITICAL(&lock);
            }
            touch_key_filter_raw(&filter,edge.key,edge.level,edge.at,emit,stable_edge);
            if(edge.at>now) now=edge.at;
        }
        touch_key_filter_tick(&filter,now,emit,stable_edge);
        portENTER_CRITICAL(&lock);
        if(now-last>diagnostic.max_gap_ms) diagnostic.max_gap_ms=now-last;
        portEXIT_CRITICAL(&lock); last=now;
        // No logging, display calls, I2C or blocking writes in this task.
        vTaskDelayUntil(&due,pdMS_TO_TICKS(5));
    }
}
void touch_key_input_init(void) {
    actions=xQueueCreate(16,sizeof(button_action_t)); traces=xQueueCreate(64,sizeof(edge_t));
    raw_edges=xQueueCreateStatic(64,sizeof(edge_t),raw_storage,&raw_control);
    assert(actions && traces && raw_edges);
    gpio_config_t pins={.pin_bit_mask=(1ULL<<0)|(1ULL<<16),.mode=GPIO_MODE_INPUT,
        .pull_up_en=GPIO_PULLUP_ENABLE,.pull_down_en=GPIO_PULLDOWN_DISABLE,.intr_type=GPIO_INTR_ANYEDGE};
    ESP_ERROR_CHECK(gpio_config(&pins));
    ESP_ERROR_CHECK(gpio_install_isr_service(ESP_INTR_FLAG_IRAM));
    ESP_ERROR_CHECK(gpio_isr_handler_add(0,input_edge,(void*)0));
    ESP_ERROR_CHECK(gpio_isr_handler_add(16,input_edge,(void*)1));
    BaseType_t created=xTaskCreate(sample,"touch_keys",3072,NULL,6,NULL);
    assert(created==pdPASS);
}
bool touch_key_input_held(void) {
    portENTER_CRITICAL(&lock);
    bool held=!diagnostic.levels[0] || !diagnostic.levels[1];
    portEXIT_CRITICAL(&lock); return held;
}
bool touch_key_input_pop(button_action_t *action) {
    edge_t edge;
    for(int i=0;i<8 && xQueueReceive(traces,&edge,0)==pdTRUE;i++)
        ESP_LOGI("touch_keys","%s %s %s captured_at=%"PRId64,(edge.key&2)?"raw":"stable",(edge.key&1)?"PWR":"BOOT",edge.level?"up":"down",edge.at);
    return xQueueReceive(actions,action,0)==pdTRUE;
}
void touch_key_input_state(cJSON *reply) {
    portENTER_CRITICAL(&lock);
    int boot=diagnostic.levels[0],pwr=diagnostic.levels[1];
    uint32_t boot_edges=diagnostic.edges[0],pwr_edges=diagnostic.edges[1];
    uint32_t emitted=diagnostic.emitted,dropped=diagnostic.dropped;
    uint32_t boot_raw=diagnostic.raw[0],pwr_raw=diagnostic.raw[1],raw_dropped=diagnostic.raw_dropped;
    uint32_t trace_dropped=diagnostic.trace_dropped,max_gap=diagnostic.max_gap_ms;
    portEXIT_CRITICAL(&lock);
    cJSON *item=cJSON_AddObjectToObject(reply,"keys");
    cJSON_AddNumberToObject(item,"boot_level",boot); cJSON_AddNumberToObject(item,"pwr_level",pwr);
    cJSON_AddNumberToObject(item,"boot_edges",boot_edges); cJSON_AddNumberToObject(item,"pwr_edges",pwr_edges);
    cJSON_AddNumberToObject(item,"boot_raw_edges",boot_raw); cJSON_AddNumberToObject(item,"pwr_raw_edges",pwr_raw);
    cJSON_AddNumberToObject(item,"actions",emitted); cJSON_AddNumberToObject(item,"dropped",dropped);
    cJSON_AddNumberToObject(item,"raw_dropped",raw_dropped); cJSON_AddNumberToObject(item,"trace_dropped",trace_dropped);
    cJSON_AddNumberToObject(item,"max_worker_gap_ms",max_gap);
    cJSON_AddNumberToObject(item,"sample_ms",5); cJSON_AddNumberToObject(item,"double_ms",TOUCH_BOOT_DOUBLE_MS);
    cJSON_AddNumberToObject(item,"debounce_ms",TOUCH_DEBOUNCE_MS);
    cJSON_AddStringToObject(item,"capture","gpio-irq-iram");
}
