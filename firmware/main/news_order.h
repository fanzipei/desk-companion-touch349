// Copyright 2026 fanzipei. SPDX-License-Identifier: Apache-2.0
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#define NEWS_READ_MAGIC 0x4e525331U
#define NEWS_READ_MAX 512
#define NEWS_ARRIVAL_MAGIC 0x4e413031U
#define NEWS_ARRIVAL_MAX 2048
#define NEWS_READ_LABEL "【已读】"
#define NEWS_UNREAD_LABEL "【未读】"

// Both labels occupy the same bytes and width, so a status change can preserve
// the reader's line offsets and scroll position.
static inline bool news_status_only_changed(const char *before,const char *after) {
    size_t prefix=sizeof(NEWS_READ_LABEL)-1;
    bool old_label=!strncmp(before,NEWS_READ_LABEL,prefix) || !strncmp(before,NEWS_UNREAD_LABEL,prefix);
    bool new_label=!strncmp(after,NEWS_READ_LABEL,prefix) || !strncmp(after,NEWS_UNREAD_LABEL,prefix);
    return old_label && new_label && !strcmp(before+prefix,after+prefix);
}
typedef struct {
    uint32_t magic, count, next, total;
    int64_t ids[NEWS_ARRIVAL_MAX];
} news_arrival_t;
typedef struct {
    uint32_t magic, count, next;
    int64_t ids[NEWS_READ_MAX];
} news_read_history_t;
typedef struct { news_read_history_t history; bool replay_announced; } news_order_t;
typedef struct { int index; bool notify_replay; } news_order_choice_t;

static inline void news_order_init(news_order_t *order) {
    memset(order,0,sizeof(*order)); order->history.magic=NEWS_READ_MAGIC;
}
static inline bool news_order_read(const news_order_t *order,int64_t id) {
    for(uint32_t i=0;i<order->history.count;i++) if(order->history.ids[i]==id) return true;
    return false;
}
static inline bool news_order_mark(news_order_t *order,int64_t id) {
    if(id<=0 || news_order_read(order,id)) return false;
    order->history.ids[order->history.next++]=id;
    order->history.next%=NEWS_READ_MAX;
    if(order->history.count<NEWS_READ_MAX) order->history.count++;
    return true;
}
static inline int news_order_unread(const news_order_t *order,const int64_t *ids,int count) {
    int unread=0;
    for(int i=0;i<count;i++) if(!news_order_read(order,ids[i])) unread++;
    return unread;
}
static inline news_order_choice_t news_order_next(news_order_t *order,const int64_t *ids,int count,int current) {
    news_order_choice_t choice={.index=0};
    if(count<=0) return choice;
    for(int step=1;step<=count;step++) {
        int index=(current+step+count)%count;
        if(!news_order_read(order,ids[index])) {
            order->replay_announced=false; choice.index=index; return choice;
        }
    }
    choice.index=(current+1+count)%count;
    choice.notify_replay=!order->replay_announced;
    order->replay_announced=true;
    return choice;
}
static inline void news_order_refresh(news_order_t *order,const int64_t *ids,int count) {
    if(news_order_unread(order,ids,count)) order->replay_announced=false;
}
static inline bool news_order_alert(const news_order_t *order,const int64_t *old_ids,int old_count,
                                    const int64_t *new_ids,int new_count,bool pending) {
    if(pending) return true; // Only an explicit user acknowledgement clears it.
    if((!old_count && !order->history.count) || news_order_unread(order,old_ids,old_count)) return false;
    for(int i=0;i<new_count;i++) {
        if(news_order_read(order,new_ids[i])) continue;
        bool existed=false;
        for(int j=0;j<old_count;j++) if(old_ids[j]==new_ids[i]) { existed=true; break; }
        if(!existed) return true;
    }
    return false;
}
static inline void news_arrival_init(news_arrival_t *arrival) {
    memset(arrival,0,sizeof(*arrival)); arrival->magic=NEWS_ARRIVAL_MAGIC;
}
static inline bool news_arrival_record(news_arrival_t *arrival,int64_t id) {
    if(id<=0) return false;
    for(uint32_t i=0;i<arrival->count;i++) if(arrival->ids[i]==id) return false;
    arrival->ids[arrival->next++]=id; arrival->next%=NEWS_ARRIVAL_MAX;
    if(arrival->count<NEWS_ARRIVAL_MAX) arrival->count++;
    if(arrival->total<UINT32_MAX) arrival->total++;
    return true;
}
