/* Copyright 2026 fanzipei
 * SPDX-License-Identifier: Apache-2.0
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdbool.h>
#include <inttypes.h>
#include <time.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "driver/gpio.h"
#include "esp_timer.h"
#include "esp_log.h"
#include "nvs_flash.h"
#include "nvs.h"
#include "cJSON.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "host/ble_hs.h"
#include "host/ble_uuid.h"
#include "host/util/util.h"
#include "services/gap/ble_svc_gap.h"
#include "services/gatt/ble_svc_gatt.h"
#include "touch_ui.h"
#include "touch_keys.h"
#include "key_input.h"
#include "motion.h"
#include "news_order.h"
#include "button_logic.h"
#include "display_logic.h"
#include "battery.h"
#include "device_clock.h"

#define NOTE_MAX 20
#define TEXT_MAX 3072
#define LEGACY_NEWS_MAX 20
#define NEWS_MAX 50
#define SUMMARY_MAX 768
#define NEWS_DELETED_MAX 256
#define NEWS_MAGIC 0x4e455732
#define GDELT_ID_BASE (1LL << 48)
#define RSS_ID_BASE (1LL << 49)
#define NEWS_FEEDBACK_MAX 64
#define NEWS_FEEDBACK_MAGIC 0x4e464231
typedef struct {
    uint32_t magic, seq;
    int64_t id, at;
    uint8_t kind;
    char title[512], domain[128];
} news_feedback_t;
static news_feedback_t *news_feedback;
static uint32_t feedback_seq, feedback_ack;
static news_order_t news_order;
static int64_t news_replay_at, news_replay_origin;
static bool news_arrival_pending;
static news_arrival_t *news_arrival;
static bool display_flipped;
#define RX_MAX 16384
#define DEVICE_NAME "DeskCompanion"
#define STORE_MAGIC 0x434f4d50

/* 9b7e0001/2/3-3c1a-4c25-bad1-6d1a9f3e6200, little endian. */
#define UUID_BYTES(x) 0x00,0x62,0x3e,0x9f,0x1a,0x6d,0xd1,0xba,0x25,0x4c,0x1a,0x3c,x,0x00,0x7e,0x9b
static const ble_uuid128_t svc_uuid = BLE_UUID128_INIT(UUID_BYTES(1));
static const ble_uuid128_t rx_uuid = BLE_UUID128_INIT(UUID_BYTES(2));
static const ble_uuid128_t tx_uuid = BLE_UUID128_INIT(UUID_BYTES(3));

typedef struct { char id[37]; int64_t created; uint8_t pinned; char text[TEXT_MAX+1]; } note_t;
typedef struct { int64_t id; int64_t timestamp; int score; int comments; char title[512]; char domain[128]; } news_t;
/* Keep the existing Touch349 NVS layout; these reserved cache fields preserve upgrades. */
typedef struct {
    uint32_t magic, version;
    int count, news_count, mode;
    int64_t news_updated;
    int seen_next;
    char seen[64][37];
    note_t notes[NOTE_MAX];
    news_t news[LEGACY_NEWS_MAX];
} store_t;
/* Separate versioned storage keeps the original note blob layout unchanged. */
typedef struct { news_t article; uint8_t pinned, title_only; char summary[SUMMARY_MAX+1]; } news_item_t;
typedef struct {
    uint32_t magic, version;
    int count, limit, deleted_count, deleted_next;
    int64_t updated, deleted[NEWS_DELETED_MAX];
    news_item_t items[NEWS_MAX];
} news_store_t;
typedef struct { char *text; uint32_t generation; uint16_t conn; } packet_t;


static const char *TAG = "companion";
static store_t *store;
static news_store_t *news, *news_stage;
static char stage_token[37];
static int stage_expected, stage_received;
static uint32_t stage_generation;
static display_settings_t display={.magic=DISPLAY_MAGIC,.hints=1,.font_size=16,.line_spacing=7};
static nvs_handle_t nvs;
static QueueHandle_t rx_queue, tx_queue;
static volatile uint16_t connection = BLE_HS_CONN_HANDLE_NONE;
static volatile uint32_t generation;
static volatile bool subscribed;
static uint16_t tx_handle;
static char rx_buffer[RX_MAX+1];
static size_t rx_length;
static int64_t rx_last;
static int note_index, news_index, page;
static uint8_t note_limit=NOTE_MAX;
static bool dirty = true;
static char toast[96];
static int64_t toast_until;

static int64_t now_ms(void) { return esp_timer_get_time()/1000; }
static void advertise(void);
static void action(const char *name);
static void draw(void);
static int page_count(void);
static void touch_dispatch(const char *name,const char *expected_identity);

static cJSON *display_json(void) {
    cJSON *obj=cJSON_CreateObject();
    cJSON_AddStringToObject(obj,"theme",display.dark?"dark":"light");
    cJSON_AddBoolToObject(obj,"show_hints",display.hints);
    cJSON_AddNumberToObject(obj,"font_size",display.font_size);
    cJSON_AddNumberToObject(obj,"line_spacing",display.line_spacing);
    cJSON_AddNumberToObject(obj,"visible_lines",touch_ui_lines(&display));
    return obj;
}

static const char *str_field(cJSON *obj, const char *name) {
    cJSON *item = cJSON_GetObjectItemCaseSensitive(obj, name);
    return cJSON_IsString(item) ? item->valuestring : "";
}
static int64_t num_field(cJSON *obj, const char *name) {
    cJSON *item = cJSON_GetObjectItemCaseSensitive(obj, name);
    return cJSON_IsNumber(item) ? (int64_t)item->valuedouble : 0;
}
static void copy_text(char *dest, const char *src, size_t cap) {
    size_t len = strlen(src);
    if (len >= cap) {
        len = cap-1;
        while (len > 0 && ((unsigned char)src[len] & 0xc0) == 0x80) len--;
    }
    memcpy(dest, src, len); dest[len] = 0;
}
static void show_toast(const char *text) {
    copy_text(toast, text, sizeof(toast)); toast_until = now_ms()+2200; dirty = true;
}
static bool persist(void) {
    esp_err_t err = nvs_set_blob(nvs, "state", store, sizeof(*store));
    if (err == ESP_OK) err = nvs_set_blob(nvs,"news_v2",news,sizeof(*news));
    if (err == ESP_OK) err = nvs_set_u8(nvs,"news_alert",news_arrival_pending);
    if (err == ESP_OK && news_arrival) err = nvs_set_blob(nvs,"news_alert_v1",news_arrival,sizeof(*news_arrival));
    if (err == ESP_OK) err = nvs_commit(nvs);
    if (err != ESP_OK) { ESP_LOGE(TAG, "persist: %s", esp_err_to_name(err)); show_toast("保存失败，请保留 PC 备份"); }
    return err == ESP_OK;
}
static void queue_json(cJSON *obj) {
    packet_t packet = { .text = cJSON_PrintUnformatted(obj), .generation = generation, .conn = connection };
    cJSON_Delete(obj);
    if (!packet.text) return;
    if (connection == BLE_HS_CONN_HANDLE_NONE || !subscribed || xQueueSend(tx_queue, &packet, 0) != pdTRUE) free(packet.text);
}
static void event(const char *type, const char *id, int pinned) {
    cJSON *obj = cJSON_CreateObject();
    cJSON_AddStringToObject(obj, "event", type);
    if (id) cJSON_AddStringToObject(obj, "id", id);
    if (pinned >= 0) cJSON_AddBoolToObject(obj, "pinned", pinned);
    cJSON_AddNumberToObject(obj, "mode", store->mode);
    queue_json(obj);
}
static void tx_task(void *arg) {
    packet_t packet;
    for (;;) {
        xQueueReceive(tx_queue, &packet, portMAX_DELAY);
        size_t length = strlen(packet.text), offset = 0;
        /* Keep a complete frame contiguous on the notify stream. */
        while (offset <= length && packet.generation == generation && packet.conn == connection && subscribed) {
            uint16_t mtu = ble_att_mtu(packet.conn);
            size_t cap = mtu > 23 ? mtu-3 : 20;
            if (cap > 180) cap = 180;
            char chunk[180]; size_t amount = length-offset;
            if (amount > cap) amount = cap;
            if (amount) memcpy(chunk, packet.text+offset, amount);
            if (amount < cap) { chunk[amount++] = '\n'; }
            struct os_mbuf *mb = ble_hs_mbuf_from_flat(chunk, amount);
            if (!mb) { vTaskDelay(pdMS_TO_TICKS(15)); continue; }
            int rc = ble_gatts_notify_custom(packet.conn, tx_handle, mb);
            if (rc != 0) { vTaskDelay(pdMS_TO_TICKS(20)); continue; }
            offset += amount;
            vTaskDelay(pdMS_TO_TICKS(7));
        }
        free(packet.text);
    }
}

static cJSON *note_json(const note_t *note) {
    cJSON *obj = cJSON_CreateObject();
    cJSON_AddStringToObject(obj, "id", note->id);
    cJSON_AddStringToObject(obj, "text", note->text);
    cJSON_AddNumberToObject(obj, "created", note->created);
    cJSON_AddBoolToObject(obj, "pinned", note->pinned);
    return obj;
}
static cJSON *news_json(const news_item_t *item) {
    cJSON *obj=cJSON_CreateObject(); const news_t *article=&item->article;
    cJSON_AddNumberToObject(obj,"id",article->id); cJSON_AddNumberToObject(obj,"time",article->timestamp);
    cJSON_AddStringToObject(obj,"title",article->title); cJSON_AddStringToObject(obj,"domain",article->domain);
    cJSON_AddNumberToObject(obj,"score",article->score); cJSON_AddNumberToObject(obj,"comments",article->comments);
    cJSON_AddStringToObject(obj,"summary",item->summary); cJSON_AddBoolToObject(obj,"pinned",item->pinned);
    cJSON_AddStringToObject(obj,"summary_basis",item->title_only?"title":"article");
    return obj;
}
static int find_news(int64_t id) { for(int i=0;i<news->count;i++) if(news->items[i].article.id==id) return i; return -1; }
static const char *news_body(const news_item_t *item) {
    static char body[1400];
    const char *label=news_order_read(&news_order,item->article.id)?NEWS_READ_LABEL:NEWS_UNREAD_LABEL;
    if(*item->summary && item->article.id>=RSS_ID_BASE)
        snprintf(body,sizeof(body),"%s%s\n\n%s",label,item->article.title,item->summary);
    else snprintf(body,sizeof(body),"%s%s",label,*item->summary?item->summary:item->article.title);
    return body;
}
static bool news_deleted(int64_t id) { for(int i=0;i<news->deleted_count;i++) if(news->deleted[i]==id) return true; return false; }
static void news_event(const char *type,int64_t id,int pinned) {
    cJSON *obj=cJSON_CreateObject(); cJSON_AddStringToObject(obj,"event",type); cJSON_AddNumberToObject(obj,"id",id);
    if(pinned>=0) cJSON_AddBoolToObject(obj,"pinned",pinned);
    queue_json(obj);
}
static cJSON *feedback_json(const news_feedback_t *record) {
    static const char *kinds[]={"browsed","deleted","opened","pinned"};
    cJSON *obj=cJSON_CreateObject();
    cJSON_AddStringToObject(obj,"event","news_feedback");
    cJSON_AddNumberToObject(obj,"seq",record->seq);
    cJSON_AddNumberToObject(obj,"id",record->id);
    cJSON_AddNumberToObject(obj,"time",record->at);
    cJSON_AddStringToObject(obj,"kind",kinds[record->kind]);
    cJSON_AddStringToObject(obj,"title",record->title);
    cJSON_AddStringToObject(obj,"domain",record->domain);
    return obj;
}
static void feedback_load(void) {
    news_feedback=calloc(NEWS_FEEDBACK_MAX,sizeof(*news_feedback)); assert(news_feedback);
    nvs_get_u32(nvs,"nf_ack",&feedback_ack);
    for(unsigned i=0;i<NEWS_FEEDBACK_MAX;i++) {
        char key[8]; snprintf(key,sizeof(key),"nf%02u",i);
        size_t bytes=sizeof(*news_feedback); news_feedback_t *record=&news_feedback[i];
        if(nvs_get_blob(nvs,key,record,&bytes)==ESP_OK && bytes==sizeof(*record) &&
           record->magic==NEWS_FEEDBACK_MAGIC && record->seq && (record->seq-1)%NEWS_FEEDBACK_MAX==i &&
           record->kind<4 && record->id>0 && memchr(record->title,0,sizeof(record->title)) && memchr(record->domain,0,sizeof(record->domain))) {
            if(record->seq>feedback_seq) feedback_seq=record->seq;
        } else memset(record,0,sizeof(*record));
    }
    if(feedback_ack>feedback_seq) feedback_ack=feedback_seq;
}
static void feedback_note(unsigned kind) {
    if(!store->mode || !news->count || feedback_seq==UINT32_MAX) return;
    uint32_t seq=feedback_seq+1; unsigned slot=(seq-1)%NEWS_FEEDBACK_MAX;
    news_feedback_t record={.magic=NEWS_FEEDBACK_MAGIC,.seq=seq,.kind=kind,
        .id=news->items[news_index].article.id,.at=device_clock_valid()?time(NULL):0};
    copy_text(record.title,news->items[news_index].article.title,sizeof(record.title));
    copy_text(record.domain,news->items[news_index].article.domain,sizeof(record.domain));
    char key[8]; snprintf(key,sizeof(key),"nf%02u",slot);
    esp_err_t result=nvs_set_blob(nvs,key,&record,sizeof(record));
    if(result==ESP_OK) result=nvs_commit(nvs);
    if(result!=ESP_OK) { ESP_LOGE(TAG,"news feedback save failed: %s",esp_err_to_name(result)); return; }
    news_feedback[slot]=record; feedback_seq=seq;
    queue_json(feedback_json(&record));
}
static void feedback_reply(cJSON *reply) {
    cJSON *items=cJSON_AddArrayToObject(reply,"items");
    uint32_t first=feedback_seq>=NEWS_FEEDBACK_MAX?feedback_seq-NEWS_FEEDBACK_MAX+1:1;
    uint32_t dropped=first>feedback_ack+1?first-feedback_ack-1:0;
    if(first<=feedback_ack) first=feedback_ack+1;
    for(uint32_t seq=first;seq && seq<=feedback_seq;seq++) {
        news_feedback_t *record=&news_feedback[(seq-1)%NEWS_FEEDBACK_MAX];
        if(record->seq==seq) cJSON_AddItemToArray(items,feedback_json(record));
    }
    cJSON_AddNumberToObject(reply,"latest_seq",feedback_seq);
    cJSON_AddNumberToObject(reply,"ack_seq",feedback_ack);
    cJSON_AddNumberToObject(reply,"dropped",dropped);
    cJSON *read_ids=cJSON_AddArrayToObject(reply,"news_read_ids");
    for(int i=0;i<news->count;i++) if(news_order_read(&news_order,news->items[i].article.id))
        cJSON_AddItemToArray(read_ids,cJSON_CreateNumber(news->items[i].article.id));
}
// Read progress is separate from news_v2 so existing notes/news keep their layout.
static bool news_read_save(void) {
    esp_err_t result=nvs_set_blob(nvs,"news_read_v1",&news_order.history,sizeof(news_order.history));
    if(result==ESP_OK) result=nvs_commit(nvs);
    if(result!=ESP_OK) ESP_LOGE(TAG,"news read progress: %s",esp_err_to_name(result));
    return result==ESP_OK;
}
static void news_mark_read(void) {
    if(store->mode && news->count && news_order_mark(&news_order,news->items[news_index].article.id)) news_read_save();
}
static int news_unread_total(void) {
    int64_t ids[NEWS_MAX];
    for(int i=0;i<news->count;i++) ids[i]=news->items[i].article.id;
    return news_order_unread(&news_order,ids,news->count);
}
static void news_read_load(void) {
    news_order_init(&news_order);
    size_t size=sizeof(news_order.history);
    if(nvs_get_blob(nvs,"news_read_v1",&news_order.history,&size)==ESP_OK && size==sizeof(news_order.history) &&
       news_order.history.magic==NEWS_READ_MAGIC && news_order.history.count<=NEWS_READ_MAX && news_order.history.next<NEWS_READ_MAX) return;
    news_order_init(&news_order);
    // Migrate known manual browsing/open/pin history from the existing journal.
    for(int i=0;i<NEWS_FEEDBACK_MAX;i++) {
        news_feedback_t *record=&news_feedback[i];
        if(record->seq && record->kind!=1 && find_news(record->id)>=0) news_order_mark(&news_order,record->id);
    }
    news_read_save();
}
static void news_arrival_load(void) {
    news_arrival=calloc(1,sizeof(*news_arrival)); assert(news_arrival);
    size_t bytes=sizeof(*news_arrival);
    if(nvs_get_blob(nvs,"news_alert_v1",news_arrival,&bytes)==ESP_OK && bytes==sizeof(*news_arrival) &&
       news_arrival->magic==NEWS_ARRIVAL_MAGIC && news_arrival->count<=NEWS_ARRIVAL_MAX && news_arrival->next<NEWS_ARRIVAL_MAX) {
        news_arrival_pending=news_arrival->total>0; return;
    }
    news_arrival_init(news_arrival);
    if(news_arrival_pending) {
        for(int i=0;i<news->count;i++) if(!news_order_read(&news_order,news->items[i].article.id)) news_arrival_record(news_arrival,news->items[i].article.id);
        if(!news_arrival->total) news_arrival->total=1;
        esp_err_t result=nvs_set_blob(nvs,"news_alert_v1",news_arrival,sizeof(*news_arrival));
        if(result==ESP_OK) result=nvs_commit(nvs);
        if(result!=ESP_OK) ESP_LOGE(TAG,"news alert migration: %s",esp_err_to_name(result));
    }
}
static bool news_advance(void) {
    if(!news->count) return false;
    news_mark_read();
    int64_t ids[NEWS_MAX];
    for(int i=0;i<news->count;i++) ids[i]=news->items[i].article.id;
    news_order_choice_t choice=news_order_next(&news_order,ids,news->count,news_index);
    if(choice.notify_replay) {
        show_toast("未读新闻已读完，即将循环已读新闻");
        news_replay_origin=news->items[news_index].article.id;
        news_replay_at=now_ms()+1800;
        return false;
    }
    news_index=choice.index; return true;
}
static void news_replay_tick(void) {
    if(!news_replay_at) return;
    if(!store->mode || !news->count || news->items[news_index].article.id!=news_replay_origin || touch_board_touched()) {
        news_replay_at=0; return;
    }
    if(now_ms()<news_replay_at || !touch_ui_motion_allowed()) return;
    int64_t ids[NEWS_MAX];
    for(int i=0;i<news->count;i++) ids[i]=news->items[i].article.id;
    news_order_choice_t choice=news_order_next(&news_order,ids,news->count,news_index);
    news_index=choice.index; news_replay_at=0; page=0;
    touch_ui_reset_position(0); dirty=true;
}
static void remove_news(int index) {
    memmove(&news->items[index],&news->items[index+1],(news->count-index-1)*sizeof(news_item_t)); news->count--;
    if(news_index>=news->count) news_index=news->count?news->count-1:0;
    if(news_unread_total()) {
        for(int step=0;step<news->count;step++) {
            int candidate=(index+step)%news->count;
            if(!news_order_read(&news_order,news->items[candidate].article.id)) { news_index=candidate; break; }
        }
        news_order.replay_announced=false;
    }
    page=0; dirty=true;
}
static void sort_news_pins(void) {
    int64_t current=news->count?news->items[news_index].article.id:0;
    for(int i=1;i<news->count;i++) if(news->items[i].pinned) {
        news_item_t *temp=malloc(sizeof(*temp)); if(!temp) break;
        *temp=news->items[i]; int j=i;
        while(j>0 && !news->items[j-1].pinned) { news->items[j]=news->items[j-1]; j--; }
        news->items[j]=*temp; free(temp);
    }
    int found=find_news(current); news_index=found>=0?found:0;
}
static bool stage_item(cJSON *obj) {
    int64_t id=num_field(obj,"id");
    if(!cJSON_IsObject(obj) || id<=0 || !*str_field(obj,"title") || strlen(str_field(obj,"summary"))>SUMMARY_MAX) return false;
    for(int i=0;i<news_stage->count;i++) if(news_stage->items[i].article.id==id) return false;
    if(news_stage->count>=NEWS_MAX) return false;
    news_item_t *item=&news_stage->items[news_stage->count++]; memset(item,0,sizeof(*item));
    item->article.id=id; item->article.timestamp=num_field(obj,"time");
    item->article.score=num_field(obj,"score"); item->article.comments=num_field(obj,"comments");
    copy_text(item->article.title,str_field(obj,"title"),sizeof(item->article.title));
    copy_text(item->article.domain,str_field(obj,"domain"),sizeof(item->article.domain));
    copy_text(item->summary,str_field(obj,"summary"),sizeof(item->summary));
    item->pinned=cJSON_IsTrue(cJSON_GetObjectItem(obj,"pinned"));
    item->title_only=!strcmp(str_field(obj,"summary_basis"),"title"); return true;
}
static const char *commit_news(void) {
    int64_t current=news->count?news->items[news_index].article.id:0;
    int count=0;
    /* Merge hardware mutations made during transfer; deletion cannot be undone by a stale packet. */
    for(int i=0;i<news_stage->count;i++) {
        news_item_t *item=&news_stage->items[i]; if(news_deleted(item->article.id)) continue;
        int old=find_news(item->article.id); if(old>=0) item->pinned=news->items[old].pinned;
        news_stage->items[count++]=*item;
    }
    news_stage->count=count;
    for(int i=0;i<news->count;i++) if(news->items[i].pinned && !news_deleted(news->items[i].article.id)) {
        bool found=false; for(int j=0;j<news_stage->count;j++) if(news_stage->items[j].article.id==news->items[i].article.id) found=true;
        if(!found) {
            if(news_stage->count>=news_stage->limit) {
                int j=news_stage->count-1; while(j>=0 && news_stage->items[j].pinned) j--;
                if(j<0) return "limit_below_pinned";
                memmove(&news_stage->items[j],&news_stage->items[j+1],(news_stage->count-j-1)*sizeof(news_item_t)); news_stage->count--;
            }
            news_stage->items[news_stage->count++]=news->items[i];
        }
    }
    memcpy(news_stage->deleted,news->deleted,sizeof(news->deleted));
    news_stage->deleted_count=news->deleted_count; news_stage->deleted_next=news->deleted_next;
    int64_t old_ids[NEWS_MAX], new_ids[NEWS_MAX];
    for(int i=0;i<news->count;i++) old_ids[i]=news->items[i].article.id;
    for(int i=0;i<news_stage->count;i++) new_ids[i]=news_stage->items[i].article.id;
    bool previous_alert=news_arrival_pending;
    news_arrival_pending=news_order_alert(&news_order,old_ids,news->count,new_ids,news_stage->count,previous_alert);
    news_arrival_t *previous_arrival=news_arrival;
    news_arrival_t *next_arrival=malloc(sizeof(*next_arrival));
    if(!next_arrival) { news_arrival_pending=previous_alert; return "out_of_memory"; }
    memcpy(next_arrival,previous_arrival,sizeof(*next_arrival));
    if(news_arrival_pending) for(int i=0;i<news_stage->count;i++) {
        if(!news_order_read(&news_order,new_ids[i])) news_arrival_record(next_arrival,new_ids[i]);
    }
    news_arrival=next_arrival;
    news_store_t *old=news; news=news_stage; news_stage=NULL;
    if(!persist()) {
        news_arrival_pending=previous_alert;
        free(news_arrival); news_arrival=previous_arrival;
        news_stage=news; news=old; return "storage_error";
    }
    free(old); news_index=0; for(int i=0;i<news->count;i++) if(news->items[i].article.id==current) news_index=i;
    free(previous_arrival);
    sort_news_pins();
    if(news_arrival_pending) news_replay_at=0;
    if(news_unread_total()) {
        news_order.replay_announced=false;
        if(find_news(current)<0) for(int i=0;i<news->count;i++) {
            if(!news_order_read(&news_order,news->items[i].article.id)) { news_index=i; break; }
        }
    }
    page=0; dirty=true; return NULL;
}
static void add_state(cJSON *reply) {
    page=touch_ui_page();
    cJSON_AddStringToObject(reply,"hardware","waveshare-touch-lcd-3.49-v2");
    cJSON_AddNumberToObject(reply,"screen_width",640); cJSON_AddNumberToObject(reply,"screen_height",172);
    touch_ui_state(reply);
    touch_key_input_state(reply);
    touch_board_state(reply);
    cJSON_AddBoolToObject(reply,"news_feedback_supported",true);
    cJSON_AddBoolToObject(reply,"news_read_sync_supported",true);
    cJSON_AddNumberToObject(reply,"news_unread_count",news_unread_total());
    cJSON_AddBoolToObject(reply,"news_replaying",news_order.replay_announced && !news_unread_total());
    cJSON_AddBoolToObject(reply,"news_replay_pending",news_replay_at!=0);
    cJSON_AddBoolToObject(reply,"news_arrival_pending",news_arrival_pending);
    cJSON_AddNumberToObject(reply,"news_arrival_count",news_arrival?news_arrival->total:0);
    cJSON *read_ids=cJSON_AddArrayToObject(reply,"news_read_ids");
    for(int i=0;i<news->count;i++) if(news_order_read(&news_order,news->items[i].article.id))
        cJSON_AddItemToArray(read_ids,cJSON_CreateNumber(news->items[i].article.id));
    cJSON *notes = cJSON_AddArrayToObject(reply, "notes");
    for (int i=0; i<store->count; i++) cJSON_AddItemToArray(notes, note_json(&store->notes[i]));
    cJSON_AddNumberToObject(reply, "mode", store->mode);
    cJSON_AddNumberToObject(reply, "news_count", news->count);
    cJSON_AddNumberToObject(reply, "news_updated", news->updated);
    cJSON_AddNumberToObject(reply, "news_limit", news->limit);
    cJSON_AddNumberToObject(reply, "max_note_bytes", TEXT_MAX);
    cJSON_AddNumberToObject(reply, "version", 2);
    cJSON *clock=cJSON_AddObjectToObject(reply,"clock");
    cJSON_AddBoolToObject(clock,"valid",device_clock_valid());
    cJSON_AddStringToObject(clock,"source",device_clock_source());
    cJSON_AddBoolToObject(clock,"rtc_available",device_clock_rtc_available());
    cJSON_AddNumberToObject(clock,"unix",device_clock_valid()?time(NULL):0);
    cJSON_AddNumberToObject(clock,"last_pc_sync",device_clock_last_sync());
    char local_time[32]; device_clock_format(local_time,sizeof(local_time),true);
    cJSON_AddStringToObject(clock,"local",local_time);
    /* Compatibility capabilities only; reminder execution and storage are retired. */
    cJSON_AddBoolToObject(reply,"reminders_supported",false);
    cJSON_AddBoolToObject(reply,"reminder_active",false);
    cJSON_AddBoolToObject(reply,"reminder_flash",false);
    cJSON_AddNumberToObject(reply,"reminder_count",0);
    cJSON_AddNumberToObject(reply,"scheduled_reminders",0);
    cJSON_AddArrayToObject(reply,"reminders_completed");
    cJSON_AddItemToObject(reply,"display",display_json());
    cJSON_AddNumberToObject(reply,"content_pages",page_count());
    battery_state_t charge=battery_state();
    cJSON *battery=cJSON_AddObjectToObject(reply,"battery");
    cJSON_AddBoolToObject(battery,"valid",charge.valid);
    cJSON_AddBoolToObject(battery,"calibrated",charge.calibrated);
    cJSON_AddNumberToObject(battery,"millivolts",charge.millivolts);
    if(charge.valid) cJSON_AddNumberToObject(battery,"percent",charge.percent);
    else cJSON_AddNullToObject(battery,"percent");
    cJSON_AddNumberToObject(reply,"current_note_index",note_index);
    cJSON_AddNumberToObject(reply,"page",page);
    if(store->count) cJSON_AddStringToObject(reply,"current_note_id",store->notes[note_index].id);
    cJSON_AddNumberToObject(reply, "note_limit", note_limit);
    int gdelt_count=0;
    cJSON *meta=cJSON_AddObjectToObject(reply,"news_meta");
    cJSON *ids=cJSON_AddArrayToObject(meta,"ids"), *pinned=cJSON_AddArrayToObject(meta,"pinned"), *deleted=cJSON_AddArrayToObject(meta,"deleted");
    for(int i=0;i<news->count;i++) {
        int64_t id=news->items[i].article.id;
        if(id>=GDELT_ID_BASE) gdelt_count++;
        cJSON_AddItemToArray(ids,cJSON_CreateNumber(id));
        if(news->items[i].pinned) cJSON_AddItemToArray(pinned,cJSON_CreateNumber(id));
    }
    for(int i=0;i<news->deleted_count;i++) cJSON_AddItemToArray(deleted,cJSON_CreateNumber(news->deleted[i]));
    cJSON *sources=cJSON_AddObjectToObject(reply,"news_source_counts");
    cJSON_AddNumberToObject(sources,"hn",news->count-gdelt_count);
    cJSON_AddNumberToObject(sources,"gdelt",gdelt_count);
    if(news->count) {
        cJSON_AddNumberToObject(reply,"current_news_id",news->items[news_index].article.id);
        cJSON_AddBoolToObject(reply,"current_news_read",news_order_read(&news_order,news->items[news_index].article.id));
        cJSON_AddStringToObject(reply,"current_news_source",news->items[news_index].article.id>=GDELT_ID_BASE?"gdelt":"hn");
    }
}
static int find_note(const char *id) {
    for (int i=0; i<store->count; i++) if (!strcmp(store->notes[i].id, id)) return i;
    return -1;
}
static void sort_note_pins(void) {
    char current[37]=""; if(store->count) copy_text(current,store->notes[note_index].id,sizeof(current));
    note_t *temp=malloc(sizeof(*temp)); if(!temp) return;
    for(int i=1;i<store->count;i++) if(store->notes[i].pinned) {
        *temp=store->notes[i]; int j=i;
        while(j>0 && !store->notes[j-1].pinned) { store->notes[j]=store->notes[j-1]; j--; }
        store->notes[j]=*temp;
    }
    free(temp); int found=find_note(current); note_index=found>=0?found:0;
}
static void remove_note(int index) {
    memmove(&store->notes[index], &store->notes[index+1], (store->count-index-1)*sizeof(note_t));
    store->count--;
    if (note_index >= store->count) note_index = store->count > 0 ? store->count-1 : 0;
    page=0; dirty=true;
}

static void process_request(packet_t *packet) {
    if (packet->generation != generation) return;
    cJSON *request = cJSON_Parse(packet->text);
    if (!cJSON_IsObject(request)) { cJSON_Delete(request); return; }
    const char *rid = str_field(request, "rid"), *op = str_field(request, "op");
    cJSON *reply = cJSON_CreateObject();
    cJSON_AddStringToObject(reply, "rid", rid);
    cJSON_AddBoolToObject(reply, "ok", true);
    const char *error = NULL;
    bool mutated = false;
    if (!strcmp(op, "state")) {
        add_state(reply);
    } else if(!strncmp(op,"schedule_",9) || !strcmp(op,"remind")) {
        error="feature_removed";
    } else if(!strcmp(op,"time_sync")) {
        cJSON *value=cJSON_GetObjectItem(request,"unix");
        if(!cJSON_IsNumber(value) || value->valuedouble!=num_field(request,"unix") || !device_clock_set(num_field(request,"unix"))) error="invalid_time";
        else { dirty=true; cJSON_AddNumberToObject(reply,"unix",time(NULL)); cJSON_AddBoolToObject(reply,"rtc_available",device_clock_rtc_available()); }
    } else if(!strcmp(op,"display_config")) {
        display_settings_t requested=display;
        cJSON *theme=cJSON_GetObjectItem(request,"theme"), *hints=cJSON_GetObjectItem(request,"show_hints");
        cJSON *font=cJSON_GetObjectItem(request,"font_size"), *spacing=cJSON_GetObjectItem(request,"line_spacing");
        if(theme && (!cJSON_IsString(theme) || (strcmp(theme->valuestring,"light") && strcmp(theme->valuestring,"dark")))) error="invalid_theme";
        else if(hints && !cJSON_IsBool(hints)) error="invalid_hints";
        else if(font && (!cJSON_IsNumber(font) || font->valuedouble!=num_field(request,"font_size") || !display_size_valid(num_field(request,"font_size")))) error="invalid_font_size";
        else if(spacing && (!cJSON_IsNumber(spacing) || spacing->valuedouble!=num_field(request,"line_spacing") || num_field(request,"line_spacing")<0 || num_field(request,"line_spacing")>16)) error="invalid_line_spacing";
        if(!error) {
            if(theme) requested.dark=!strcmp(theme->valuestring,"dark");
            if(hints) requested.hints=cJSON_IsTrue(hints);
            if(font) requested.font_size=num_field(request,"font_size");
            if(spacing) requested.line_spacing=num_field(request,"line_spacing");
            if(memcmp(&requested,&display,sizeof(display))) {
                esp_err_t rc=nvs_set_blob(nvs,"display_v1",&requested,sizeof(requested));
                if(rc==ESP_OK) rc=nvs_commit(nvs);
                if(rc!=ESP_OK) error="storage_error";
                else { display=requested; page=0; dirty=true; }
            }
        }
        cJSON_AddItemToObject(reply,"display",display_json());
    } else if(!strcmp(op,"frame_get")) {
        draw();
        if(!touch_ui_frame(reply)) error="frame_too_complex";
    } else if (!strcmp(op, "config")) {
        int64_t requested=num_field(request,"note_limit");
        if(requested<1 || requested>NOTE_MAX) error="invalid_limit";
        else if(requested<store->count) error="limit_below_count";
        else {
            esp_err_t err=nvs_set_u8(nvs,"note_limit",(uint8_t)requested);
            if(err==ESP_OK) err=nvs_commit(nvs);
            if(err!=ESP_OK) error="storage_error";
            else { note_limit=requested; dirty=true; }
        }
        cJSON_AddNumberToObject(reply,"note_limit",note_limit);
        cJSON_AddNumberToObject(reply,"minimum",store->count);
    } else if (!strcmp(op, "add")) {
        const char *id = str_field(request, "id"), *text = str_field(request, "text");
        if (!*id || strlen(id)>36 || !*text || strlen(text)>TEXT_MAX) error="invalid_note";
        else {
            bool seen=false;
            for (int i=0; i<64; i++) if (!strcmp(store->seen[i], id)) seen=true;
            if (seen || find_note(id)>=0) cJSON_AddBoolToObject(reply, "duplicate", true);
            else {
                int evict=-1;
                if (store->count >= note_limit) {
                    for (int i=store->count-1; i>=0; i--) if (!store->notes[i].pinned) { evict=i; break; }
                    if (evict<0) error="all_pinned";
                }
                if (!error) {
                    if (evict>=0) { cJSON_AddStringToObject(reply,"evicted",store->notes[evict].id); remove_note(evict); }
                    memmove(&store->notes[1], &store->notes[0], store->count*sizeof(note_t));
                    note_t *note=&store->notes[0]; memset(note,0,sizeof(*note));
                    copy_text(note->id,id,sizeof(note->id)); copy_text(note->text,text,sizeof(note->text));
                    note->created=num_field(request,"created"); store->count++;
                    copy_text(store->seen[store->seen_next++%64],id,37);
                    store->seen_next%=64;
                    note_index=page=0; sort_note_pins(); store->mode=0; mutated=true;
                    show_toast("便签已收到");
                }
            }
        }
    } else if (!strcmp(op,"delete") || !strcmp(op,"pin")) {
        int i=find_note(str_field(request,"id"));
        if (i<0) error="not_found";
        else if (!strcmp(op,"delete")) { remove_note(i); mutated=true; }
        else { store->notes[i].pinned=cJSON_IsTrue(cJSON_GetObjectItem(request,"pinned")); sort_note_pins(); mutated=true; }
    } else if (!strcmp(op,"mode")) {
        store->mode = num_field(request,"mode") ? 1 : 0; page=0; mutated=true;
    } else if (!strcmp(op,"navigate")) {
        touch_dispatch(str_field(request,"action"),str_field(request,"identity"));
    }
    else if (!strcmp(op,"news_mark_read")) {
        cJSON *ids=cJSON_GetObjectItemCaseSensitive(request,"ids"), *id;
        if(!cJSON_IsArray(ids) || cJSON_GetArraySize(ids)>NEWS_MAX) error="invalid_news_ids";
        if(!error) cJSON_ArrayForEach(id,ids) {
            if(!cJSON_IsNumber(id) || id->valuedouble<=0 || id->valuedouble>9007199254740991.0 ||
               id->valuedouble!=(double)(int64_t)id->valuedouble) { error="invalid_news_ids"; break; }
        }
        if(!error) {
            news_read_history_t *before=malloc(sizeof(*before));
            if(!before) error="out_of_memory";
            else {
                *before=news_order.history; bool changed=false;
                cJSON_ArrayForEach(id,ids) changed=news_order_mark(&news_order,(int64_t)id->valuedouble)||changed;
                if(changed && !news_read_save()) { news_order.history=*before; error="storage_error"; }
                else if(changed) dirty=true;
                free(before);
            }
        }
    }
    else if (!strcmp(op,"news_feedback_get")) { feedback_reply(reply); }
    else if (!strcmp(op,"news_feedback_ack")) {
        int64_t seq=num_field(request,"seq");
        if(seq<feedback_ack || seq>feedback_seq) error="invalid_feedback_sequence";
        else {
            esp_err_t result=nvs_set_u32(nvs,"nf_ack",(uint32_t)seq);
            if(result==ESP_OK) result=nvs_commit(nvs);
            if(result==ESP_OK) feedback_ack=(uint32_t)seq; else error="storage_error";
        }
    }
    else if (!strcmp(op,"news_get")) {
        cJSON *items=cJSON_AddArrayToObject(reply,"items");
        for(int i=0;i<news->count;i++) cJSON_AddItemToArray(items,news_json(&news->items[i]));
        cJSON_AddNumberToObject(reply,"limit",news->limit); cJSON_AddNumberToObject(reply,"updated",news->updated);
    } else if (!strcmp(op,"news_begin") || !strcmp(op,"news")) {
        bool legacy=!strcmp(op,"news");
        cJSON *items=cJSON_GetObjectItemCaseSensitive(request,"items");
        int count=legacy?cJSON_GetArraySize(items):num_field(request,"count");
        int limit=cJSON_IsNumber(cJSON_GetObjectItem(request,"limit"))?num_field(request,"limit"):20;
        int pinned_count=0; for(int i=0;i<news->count;i++) pinned_count+=!!news->items[i].pinned;
        if(count<0 || count>NEWS_MAX || limit<1 || limit>NEWS_MAX || count>limit || (legacy && !cJSON_IsArray(items))) error="invalid_news";
        else if(limit<pinned_count) error="limit_below_pinned";
        else {
            free(news_stage); news_stage=calloc(1,sizeof(*news_stage));
            if(!news_stage) error="out_of_memory";
            else {
                news_stage->magic=NEWS_MAGIC; news_stage->version=2; news_stage->limit=limit;
                news_stage->updated=num_field(request,"updated"); stage_expected=count; stage_received=0;
                stage_generation=generation; copy_text(stage_token,str_field(request,"token"),sizeof(stage_token));
                if(legacy) {
                    cJSON *item; cJSON_ArrayForEach(item,items) if(!stage_item(item)) { error="invalid_news"; break; }
                    if(!error) error=commit_news();
                }
            }
        }
    } else if (!strcmp(op,"news_chunk")) {
        cJSON *items=cJSON_GetObjectItemCaseSensitive(request,"items");
        if(!news_stage || stage_generation!=generation || strcmp(stage_token,str_field(request,"token"))) error="invalid_news_transaction";
        else if(num_field(request,"offset")!=stage_received || !cJSON_IsArray(items) || stage_received+cJSON_GetArraySize(items)>stage_expected) error="invalid_news_offset";
        else {
            cJSON *item; cJSON_ArrayForEach(item,items) {
                if(!stage_item(item)) { error="invalid_news"; free(news_stage); news_stage=NULL; break; }
                stage_received++;
            }
        }
    } else if (!strcmp(op,"news_commit")) {
        if(!news_stage || stage_generation!=generation || strcmp(stage_token,str_field(request,"token")) || stage_received!=stage_expected) error="incomplete_news";
        else error=commit_news();
    } else error="unknown_operation";
    if (mutated) { dirty=true; if (!persist()) error="storage_error"; }
    if (error) {
        cJSON_ReplaceItemInObject(reply,"ok",cJSON_CreateBool(false));
        cJSON_AddStringToObject(reply,"error",error);
    }
    queue_json(reply); cJSON_Delete(request);
}

static int wrapped_text(const char *text,int selected_page,bool render) {
    return touch_ui_text(text,selected_page,&display,render);
}
static int page_count(void) {
    if(store->mode && news->count) {
        news_item_t *item=&news->items[news_index];
        return wrapped_text(news_body(item),0,false);
    }
    return store->count ? wrapped_text(store->notes[note_index].text,0,false) : 1;
}
static void action(const char *name) {
    if (!strcmp(name,"toggle")) {
        store->mode=!store->mode; page=0; persist(); event("mode",NULL,-1);
        touch_ui_reset_position(0);
    } else if (!strcmp(name,"next")) {
        if(!touch_ui_step(1)) {
            if(store->mode && news->count) { if(news_advance()) touch_ui_reset_position(0); }
            else if(!store->mode && store->count) { note_index=(note_index+1)%store->count; touch_ui_reset_position(0); }
        }
        page=touch_ui_page();
    } else if (!strcmp(name,"previous")) {
        if(!touch_ui_step(-1)) {
            if(store->mode && news->count) { news_mark_read(); news_index=(news_index+news->count-1)%news->count; }
            else if(!store->mode && store->count) note_index=(note_index+store->count-1)%store->count;
            touch_ui_reset_position(-1);
        }
        page=touch_ui_page();
    } else if (!strcmp(name,"delete") && !store->mode && store->count) {
        char id[37]; copy_text(id,store->notes[note_index].id,sizeof(id));
        remove_note(note_index); persist(); event("note_deleted",id,-1); show_toast("便签已删除");
    } else if (!strcmp(name,"pin") && !store->mode && store->count) {
        note_t *note=&store->notes[note_index]; note->pinned=!note->pinned;
        char id[37]; copy_text(id,note->id,sizeof(id)); bool pinned=note->pinned; sort_note_pins();
        persist(); event("note_pinned",id,pinned); show_toast(pinned?"已置顶":"已取消置顶");
    } else if (!strcmp(name,"delete") && store->mode && news->count) {
        feedback_note(1);
        int64_t id=news->items[news_index].article.id;
        if(!news_deleted(id)) {
            news->deleted[news->deleted_next++%NEWS_DELETED_MAX]=id; news->deleted_next%=NEWS_DELETED_MAX;
            if(news->deleted_count<NEWS_DELETED_MAX) news->deleted_count++;
        }
        remove_news(news_index); persist(); news_event("news_deleted",id,-1); show_toast("新闻已删除");
    } else if (!strcmp(name,"pin") && store->mode && news->count) {
        news_item_t *item=&news->items[news_index]; item->pinned=!item->pinned;
        int pinned=item->pinned; int64_t id=item->article.id; sort_news_pins();
        if(pinned) { news_mark_read(); feedback_note(3); }
        persist(); news_event("news_pinned",id,pinned); show_toast(pinned?"新闻已置顶":"已取消置顶");
    } else if (!strcmp(name,"open") && !store->mode && store->count) {
        if(connection==BLE_HS_CONN_HANDLE_NONE) show_toast("请先连接 PC 再显示便签");
        else { event("open_note",store->notes[note_index].id,-1); show_toast("已请求 PC 显示便签"); }
    } else if (!strcmp(name,"open") && store->mode && news->count) {
        if(connection==BLE_HS_CONN_HANDLE_NONE) show_toast("请先连接 PC 再打开网页");
        else {
            news_mark_read(); feedback_note(2);
            cJSON *obj=cJSON_CreateObject(); cJSON_AddStringToObject(obj,"event","open_news");
            cJSON_AddNumberToObject(obj,"id",news->items[news_index].article.id); queue_json(obj); show_toast("已请求 PC 打开网页");
        }
    }
    dirty=true;
}
static void touch_dispatch(const char *name,const char *expected_identity) {
    news_replay_at=0;
    touch_ui_activity();
    if(!strcmp(name,"dismiss_news_alert")) {
        news_arrival_t *empty=calloc(1,sizeof(*empty));
        if(!empty) return;
        news_arrival_init(empty);
        esp_err_t result=nvs_set_blob(nvs,"news_alert_v1",empty,sizeof(*empty));
        if(result==ESP_OK) result=nvs_set_u8(nvs,"news_alert",0);
        if(result==ESP_OK) result=nvs_commit(nvs);
        if(result==ESP_OK) { free(news_arrival); news_arrival=empty; news_arrival_pending=false; dirty=true; }
        else { free(empty); ESP_LOGE(TAG,"news alert acknowledgement: %s",esp_err_to_name(result)); }
        return;
    }
    if(news_arrival_pending) return;
    bool gesture=!strcmp(name,"gesture_delete") || !strcmp(name,"gesture_open");
    if(!strcmp(name,"confirm_delete") || gesture) {
        char current[48]="";
        if(store->mode && news->count) snprintf(current,sizeof(current),"news:%lld",(long long)news->items[news_index].article.id);
        else if(!store->mode && store->count) snprintf(current,sizeof(current),"note:%s",store->notes[note_index].id);
        const char *target=gesture?expected_identity:touch_ui_delete_identity();
        if(!target || !*target || strcmp(current,target)) {
            touch_ui_local_action("cancel"); show_toast("内容已更新，请重新操作"); return;
        }
    }
    if(gesture) {
        if(!strcmp(name,"gesture_delete")) { touch_ui_local_action("delete"); dirty=true; }
        else action("open");
        return;
    }
    if(touch_ui_local_action(name)) {
        dirty=true; return;
    }
    if(!strcmp(name,"notes") || !strcmp(name,"news")) {
        int mode=!strcmp(name,"news");
        if(store->mode!=mode) { store->mode=mode; page=0; touch_ui_reset_position(0); persist(); event("mode",NULL,-1); }
    } else if(!strcmp(name,"item_next") || !strcmp(name,"item_previous") || !strcmp(name,"swipe_next") || !strcmp(name,"swipe_previous")) {
        int direction=(!strcmp(name,"item_next") || !strcmp(name,"swipe_next"))?1:-1;
        if((!strcmp(name,"swipe_next") || !strcmp(name,"swipe_previous")) && store->mode && news->count>1) feedback_note(0);
        if(store->mode && news->count) {
            if(direction>0) { if(news_advance()) touch_ui_reset_position(0); }
            else { news_mark_read(); news_index=(news_index+news->count-1)%news->count; touch_ui_reset_position(0); }
        } else if(!store->mode && store->count) {
            note_index=(note_index+store->count+direction)%store->count; touch_ui_reset_position(0);
        }
        page=0;
    } else if(!strcmp(name,"theme") || !strcmp(name,"font_up") || !strcmp(name,"font_down")) {
        display_settings_t next=display;
        if(!strcmp(name,"theme")) next.dark=!display.dark;
        else {
            static const int sizes[]={12,16,24,32}; int index=0;
            while(index<3 && sizes[index]!=display.font_size) index++;
            index+=!strcmp(name,"font_up")?1:-1;
            if(index<0) index=0;
            if(index>3) index=3;
            next.font_size=sizes[index];
        }
        esp_err_t err=nvs_set_blob(nvs,"display_v1",&next,sizeof(next));
        if(err==ESP_OK) err=nvs_commit(nvs);
        if(err==ESP_OK) {
            display=next; page=0;
            cJSON *changed=display_json(); cJSON_AddStringToObject(changed,"event","display_changed"); queue_json(changed);
        } else show_toast("显示设置保存失败");
    } else action(!strcmp(name,"confirm_delete")?"delete":name);
    dirty=true;
}
static void draw(void) {
    const char *text="还没有便签\n选中文字后按 Ctrl+Alt+J 发送\nCtrl+Alt+空格 打开输入框";
    char key[48]="",source[100]="",clock[32];
    int count=store->mode?news->count:store->count, index=store->mode?news_index:note_index;
    bool pinned=false;
    if(store->mode && count) {
        news_item_t *item=&news->items[news_index];
        text=news_body(item); pinned=item->pinned;
        snprintf(key,sizeof(key),"news:%lld",(long long)item->article.id);
        snprintf(source,sizeof(source),"%s %.64s",item->article.id>=RSS_ID_BASE?"RSS":item->article.id>=GDELT_ID_BASE?"GDELT":"HN",item->article.domain);
    } else if(!store->mode && count) {
        note_t *note=&store->notes[note_index]; text=note->text; pinned=note->pinned;
        snprintf(key,sizeof(key),"note:%s",note->id);
    } else if(store->mode) text="等待 HN / GDELT 新闻\n请启动电脑上的桌面小伴侣后台";
    int pages=wrapped_text(text,0,false); if(page>=pages) page=pages-1;
    device_clock_format(clock,sizeof(clock),false);
    battery_state_t charge=battery_state();
    touch_view_t view={.text=text,.identity=key,.time=clock,.toast=now_ms()<toast_until?toast:"",.source=source,
        .mode=store->mode,.index=index,.count=count,.unread=news_unread_total(),.page=page,.pages=pages,.battery=charge.valid?charge.percent:-1,
        .connected=connection!=BLE_HS_CONN_HANDLE_NONE && subscribed,.pinned=pinned,
        .news_alert=news_arrival_pending,
        .news_alert_count=news_arrival?news_arrival->total:0,.display=display};
    touch_ui_render(&view); dirty=false;
}
static void touch_button_action(button_action_t value) {
    if(value!=TOUCH_KEY_POWER && value!=TOUCH_KEY_WAKE) {
        ESP_LOGI(TAG,"BOOT content action ignored"); return;
    }
    touch_ui_activity();
    ESP_LOGI(TAG,"PHYSICAL action=%s",value==TOUCH_KEY_POWER?"power":"wake");
    if(value==TOUCH_KEY_POWER) { touch_board_power(false); return; }
    if(value==TOUCH_KEY_WAKE) {
        if(!touch_board_awake()) { touch_board_power(true); touch_ui_local_action("controls"); dirty=true; }
        return;
    }
}
static void keys_tick(void) {
    touch_ui_tick(); touch_command_t command;
    while(touch_ui_action(&command)) { ESP_LOGI(TAG,"TOUCH action=%s",command.action); touch_dispatch(command.action,command.identity); }
    bool motion_allowed=touch_ui_motion_allowed();
    touch_motion_allow(motion_allowed);
    bool inverted;
    // Rotation applies to the entire display, including menus and persistent
    // news alerts. Wait only for finger release to keep touch coordinates stable.
    if(!touch_board_touched() && touch_motion_orientation(&inverted)) {
        display_flipped=inverted; touch_board_flipped(inverted); dirty=true;
        esp_err_t err=nvs_set_u8(nvs,"lcd_flip",inverted);
        if(err==ESP_OK) err=nvs_commit(nvs);
        if(err!=ESP_OK) ESP_LOGW(TAG,"orientation persistence failed: %s",esp_err_to_name(err));
        ESP_LOGI(TAG,"ORIENTATION flipped=%d",inverted);
    }
    if(motion_allowed && touch_motion_poll(&inverted)) {
        touch_ui_local_action("controls"); action("toggle");
        show_toast(store->mode?"已切换到新闻":"已切换到便签");
        ESP_LOGI(TAG,"MOTION mode=%d flipped=%d",store->mode,inverted);
    }
    if(touch_key_input_held()) touch_ui_activity();
    button_action_t input;
    while(touch_key_input_pop(&input)) touch_button_action(input);
    page=touch_ui_page();
}
static int access_rx(uint16_t conn, uint16_t attr, struct ble_gatt_access_ctxt *ctxt, void *arg) {
    if(ctxt->op!=BLE_GATT_ACCESS_OP_WRITE_CHR) return BLE_ATT_ERR_WRITE_NOT_PERMITTED;
    uint16_t size=OS_MBUF_PKTLEN(ctxt->om); char chunk[512]; uint16_t copied;
    if(size>sizeof(chunk)) return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
    if(ble_hs_mbuf_to_flat(ctxt->om,chunk,sizeof(chunk),&copied)!=0) return BLE_ATT_ERR_UNLIKELY;
    if(now_ms()-rx_last>5000) rx_length=0;
    rx_last=now_ms();
    for(int i=0;i<copied;i++) {
        if(chunk[i]=='\n') {
            rx_buffer[rx_length]=0;
            packet_t packet={.text=strdup(rx_buffer),.generation=generation,.conn=conn}; rx_length=0;
            if(!packet.text) return BLE_ATT_ERR_INSUFFICIENT_RES;
            if(xQueueSend(rx_queue,&packet,0)!=pdTRUE) { free(packet.text); return BLE_ATT_ERR_INSUFFICIENT_RES; }
        } else {
            if(rx_length>=RX_MAX) { rx_length=0; return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN; }
            rx_buffer[rx_length++]=chunk[i];
        }
    }
    return 0;
}
static int access_tx(uint16_t conn,uint16_t attr,struct ble_gatt_access_ctxt *ctxt,void *arg) {
    return os_mbuf_append(ctxt->om,"DeskCompanion/v1",16)==0?0:BLE_ATT_ERR_INSUFFICIENT_RES;
}
static const struct ble_gatt_svc_def services[]={
    {.type=BLE_GATT_SVC_TYPE_PRIMARY,.uuid=&svc_uuid.u,.characteristics=(struct ble_gatt_chr_def[]){
        {.uuid=&rx_uuid.u,.access_cb=access_rx,.flags=BLE_GATT_CHR_F_WRITE},
        {.uuid=&tx_uuid.u,.access_cb=access_tx,.val_handle=&tx_handle,.flags=BLE_GATT_CHR_F_NOTIFY|BLE_GATT_CHR_F_READ},
        {0}}}, {0}};
static int gap_event(struct ble_gap_event *event,void *arg) {
    switch(event->type) {
    case BLE_GAP_EVENT_CONNECT:
        if(!event->connect.status) { connection=event->connect.conn_handle; generation++; subscribed=false; rx_length=0; dirty=true; }
        else { advertise(); }
        break;
    case BLE_GAP_EVENT_DISCONNECT:
        connection=BLE_HS_CONN_HANDLE_NONE; generation++; subscribed=false; rx_length=0; dirty=true; advertise(); break;
    case BLE_GAP_EVENT_SUBSCRIBE:
        if(event->subscribe.attr_handle==tx_handle) { subscribed=event->subscribe.cur_notify; dirty=true; }
        break;
    default: break;
    }
    return 0;
}
static void advertise(void) {
    struct ble_hs_adv_fields fields={0};
    fields.flags=BLE_HS_ADV_F_DISC_GEN|BLE_HS_ADV_F_BREDR_UNSUP;
    fields.name=(uint8_t*)DEVICE_NAME; fields.name_len=strlen(DEVICE_NAME); fields.name_is_complete=1;
    ble_gap_adv_set_fields(&fields);
    struct ble_gap_adv_params params={0}; params.conn_mode=BLE_GAP_CONN_MODE_UND; params.disc_mode=BLE_GAP_DISC_MODE_GEN;
    int rc=ble_gap_adv_start(BLE_OWN_ADDR_PUBLIC,NULL,BLE_HS_FOREVER,&params,gap_event,NULL);
    ESP_LOGI(TAG,"advertise rc=%d",rc);
}
static void synced(void) { ble_hs_util_ensure_addr(0); advertise(); }
static void host_task(void *arg) { nimble_port_run(); nimble_port_freertos_deinit(); }

void app_main(void) {
    store=calloc(1,sizeof(*store)); assert(store);
    news=calloc(1,sizeof(*news)); assert(news);
    esp_err_t err=nvs_flash_init();
    if(err==ESP_ERR_NVS_NO_FREE_PAGES || err==ESP_ERR_NVS_NEW_VERSION_FOUND) ESP_ERROR_CHECK(err); /* Preserve existing NVS. */
    ESP_ERROR_CHECK(err); ESP_ERROR_CHECK(nvs_flash_init_partition("storage"));
    ESP_ERROR_CHECK(nvs_open_from_partition("storage","companion",NVS_READWRITE,&nvs));
    uint8_t saved_alert=0;
    if(nvs_get_u8(nvs,"news_alert",&saved_alert)==ESP_OK) news_arrival_pending=!!saved_alert;
    feedback_load();
    display_settings_t saved_display; size_t display_bytes=sizeof(saved_display);
    if(nvs_get_blob(nvs,"display_v1",&saved_display,&display_bytes)==ESP_OK && display_bytes==sizeof(saved_display) && display_settings_valid(&saved_display)) display=saved_display;
    if(nvs_get_u8(nvs,"note_limit",&note_limit)!=ESP_OK || note_limit<1 || note_limit>NOTE_MAX) note_limit=NOTE_MAX;
    size_t size=sizeof(*store); err=nvs_get_blob(nvs,"state",store,&size);
    if(err!=ESP_OK || size!=sizeof(*store) || store->magic!=STORE_MAGIC || store->version!=1 || store->count<0 || store->count>NOTE_MAX || store->news_count<0 || store->news_count>LEGACY_NEWS_MAX) {
        memset(store,0,sizeof(*store)); store->magic=STORE_MAGIC; store->version=1;
    }
    if(note_limit<store->count) note_limit=store->count;
    size=sizeof(*news); err=nvs_get_blob(nvs,"news_v2",news,&size);
    if(err!=ESP_OK || size!=sizeof(*news) || news->magic!=NEWS_MAGIC || news->version!=2 || news->count<0 || news->count>NEWS_MAX || news->limit<1 || news->limit>NEWS_MAX || news->count>news->limit || news->deleted_count<0 || news->deleted_count>NEWS_DELETED_MAX) {
        memset(news,0,sizeof(*news)); news->magic=NEWS_MAGIC; news->version=2; news->limit=20;
        news->count=store->news_count; news->updated=store->news_updated;
        for(int i=0;i<news->count;i++) news->items[i].article=store->news[i];
    }
    rx_queue=xQueueCreate(5,sizeof(packet_t)); tx_queue=xQueueCreate(8,sizeof(packet_t)); assert(rx_queue && tx_queue);
    news_read_load();
    news_arrival_load();
    uint8_t saved_orientation=0;
    if(nvs_get_u8(nvs,"lcd_flip",&saved_orientation)==ESP_OK) display_flipped=!!saved_orientation;
    touch_motion_restore(display_flipped);
    touch_board_init();
    touch_board_flipped(display_flipped);
    touch_key_input_init();
    battery_init();
    device_clock_init();
    ESP_ERROR_CHECK(nimble_port_init()); ble_hs_cfg.sync_cb=synced;
    ble_svc_gap_init(); ble_svc_gatt_init(); ble_svc_gap_device_name_set(DEVICE_NAME);
    ESP_ERROR_CHECK(ble_gatts_count_cfg(services)); ESP_ERROR_CHECK(ble_gatts_add_svcs(services));
    xTaskCreate(tx_task,"companion_tx",6144,NULL,5,NULL); nimble_port_freertos_init(host_task);
    ESP_LOGI(TAG,"COMPANION_READY notes=%d news=%d",store->count,news->count);
    int64_t battery_due=now_ms()+5000;
    int64_t shown_minute=-1;
    for(;;) {
        packet_t packet;
        if(xQueueReceive(rx_queue,&packet,0)==pdTRUE) { process_request(&packet); free(packet.text); }
        keys_tick();
        news_replay_tick();
        int64_t minute=device_clock_valid()?time(NULL)/60:0;
        if(minute!=shown_minute) { shown_minute=minute; dirty=true; }
        if(now_ms()>=battery_due) { battery_due=now_ms()+5000; if(battery_update()) dirty=true; }
        if(toast_until && now_ms()>=toast_until) { toast_until=0; dirty=true; }
        if(dirty) draw();
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}
