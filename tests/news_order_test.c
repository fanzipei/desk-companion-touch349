#include <assert.h>
#include <stdio.h>
#include "../firmware/main/news_order.h"

int main(void) {
    news_order_t order; news_order_init(&order);
    int64_t ids[]={10,20,30,40,50};
    news_order_mark(&order,20);news_order_mark(&order,40);
    assert(news_order_unread(&order,ids,5)==3);
    news_order_mark(&order,10);
    news_order_choice_t choice=news_order_next(&order,ids,5,0);
    assert(choice.index==2 && !choice.notify_replay); // Skip read/pinned 20.
    news_order_mark(&order,30);
    choice=news_order_next(&order,ids,5,2);
    assert(choice.index==4 && !choice.notify_replay);
    news_order_mark(&order,50);
    choice=news_order_next(&order,ids,5,4);
    assert(choice.index==0 && choice.notify_replay);
    for(int i=0;i<8;i++) assert(!news_order_next(&order,ids,5,i%5).notify_replay);
    int64_t reordered[]={40,10,50,20,30};
    news_order_refresh(&order,reordered,5);
    assert(news_order_unread(&order,reordered,5)==0 && order.replay_announced);
    int64_t updated[]={40,60,30};
    news_order_refresh(&order,updated,3);
    assert(!order.replay_announced && news_order_unread(&order,updated,3)==1);
    choice=news_order_next(&order,updated,3,2);
    assert(choice.index==1 && !choice.notify_replay);
    news_order_mark(&order,60);
    assert(news_order_next(&order,updated,3,1).notify_replay);
    assert(!news_order_next(&order,updated,0,0).notify_replay);
    news_order_init(&order);
    for(int i=1;i<=NEWS_READ_MAX+20;i++) assert(news_order_mark(&order,i));
    assert(!news_order_read(&order,1) && news_order_read(&order,NEWS_READ_MAX+20));
    assert(!news_order_mark(&order,NEWS_READ_MAX+20));
    news_order_t restored={.history=order.history};
    assert(news_order_read(&restored,NEWS_READ_MAX+20));
    // Exhaust all read masks/start positions: unread must win even across wrap.
    for(unsigned mask=0;mask<32;mask++) for(int start=0;start<5;start++) {
        news_order_init(&order);
        for(int i=0;i<5;i++) if(mask&(1U<<i)) news_order_mark(&order,ids[i]);
        choice=news_order_next(&order,ids,5,start);
        if(mask!=31) assert(!news_order_read(&order,ids[choice.index]) && !choice.notify_replay);
        else assert(choice.notify_replay && choice.index==(start+1)%5);
    }
    news_order_init(&order);
    int64_t old_ids[]={10,20}, fresh[]={10,20,30}, newer[]={40};
    assert(!news_order_alert(&order,old_ids,2,fresh,3,false)); // Existing unread remains: no interruption.
    news_order_mark(&order,10);news_order_mark(&order,20);
    assert(news_order_alert(&order,old_ids,2,fresh,3,false));
    assert(!news_order_alert(&order,old_ids,2,old_ids,2,false)); // Repeated snapshot is not new news.
    news_order_mark(&order,30);
    assert(!news_order_alert(&order,old_ids,2,fresh,3,false)); // Returning read article stays read.
    assert(news_order_alert(&order,old_ids,2,newer,1,false));
    assert(news_order_alert(&order,old_ids,2,old_ids,2,true)); // Refresh never dismisses an outstanding alert.
    assert(news_order_alert(&order,old_ids,2,newer,0,true));
    news_order_init(&order);
    assert(!news_order_alert(&order,old_ids,0,fresh,3,false)); // First installation is not a completed reading cycle.
    news_order_mark(&order,99);
    assert(news_order_alert(&order,old_ids,0,fresh,3,false));
    news_arrival_t arrival;news_arrival_init(&arrival);
    assert(news_arrival_record(&arrival,30) && arrival.total==1);
    assert(!news_arrival_record(&arrival,30) && arrival.total==1);
    assert(news_arrival_record(&arrival,40) && arrival.total==2);
    assert(news_arrival_record(&arrival,50) && arrival.total==3);
    assert(!news_arrival_record(&arrival,30) && arrival.total==3); // A removed/reappearing article is not counted twice.
    news_arrival_t recovered=arrival;
    assert(!news_arrival_record(&recovered,40) && recovered.total==3);
    news_arrival_init(&arrival);
    assert(news_arrival_record(&arrival,40) && arrival.total==1); // Explicit acknowledgement starts a new count.
    assert(news_status_only_changed("【未读】标题\n正文","【已读】标题\n正文"));
    assert(news_status_only_changed("【已读】标题\n正文","【未读】标题\n正文"));
    assert(!news_status_only_changed("【未读】标题\n正文","【已读】标题\n新正文"));
    assert(!news_status_only_changed("便签","【已读】便签"));
    assert(!news_status_only_changed("","【已读】"));
    puts("news order: unread priority, replay, persistent notifications and read markers passed");
    return 0;
}
