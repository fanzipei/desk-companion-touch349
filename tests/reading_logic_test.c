#include <assert.h>
#include <stdio.h>
#include "../firmware/main/reading_logic.h"
#include "../firmware/main/touch_keys.h"
#include "../firmware/main/key_filter.h"
static int count; static button_action_t events[20];
static void emit(button_action_t action) { events[count++]=action; }
static void tick_until(reading_t *r,uint32_t end) {
    for(uint32_t now=r->tick_at+20;now<=end;now+=20) reading_tick(r,now,true);
}
int main(void) {
    reading_t r; reading_init(&r,100); reading_bounds(&r,1200,156);
    assert(!reading_tick(&r,10099,true) && !r.focused);
    assert(reading_tick(&r,10100,true) && r.focused);
    tick_until(&r,15080); assert(!reading_y(&r));
    tick_until(&r,17100); assert(reading_y(&r)>=19 && reading_y(&r)<=21);
    reading_press(&r,200,130,17120); int initial=reading_y(&r);
    reading_move(&r,203,80,17140); assert(reading_y(&r)==initial+50);
    assert(reading_release(&r,210,40,17160)==READING_VERTICAL);
    assert(reading_y(&r)==initial+90);
    int paused=reading_y(&r); tick_until(&r,22140); assert(reading_y(&r)==paused);
    tick_until(&r,23160); assert(reading_y(&r)>paused);
    reading_press(&r,300,80,23200); reading_move(&r,200,83,23220);
    assert(reading_release(&r,180,84,23240)==READING_NEXT);
    assert(reading_y(&r)>paused);
    reading_press(&r,200,80,23300); assert(reading_release(&r,204,82,23350)==READING_TAP);
    reading_press(&r,200,100,23400); reading_move(&r,201,80,23420);
    assert(reading_release(&r,200,100,23440)==READING_VERTICAL);
    reading_seek(&r,99999); assert(reading_y(&r)==r.maximum);
    r.direction=1; r.hold_until=0; r.input_at=0;
    reading_tick(&r,25000,true); assert(r.direction==1 && r.restart_pending && reading_y(&r)==r.maximum);
    tick_until(&r,36980); assert(reading_y(&r)==r.maximum); // Full 12 seconds at bottom.
    tick_until(&r,37000); assert(!reading_y(&r) && !r.restart_pending);
    tick_until(&r,41980); assert(!reading_y(&r)); // Full 5 seconds at every restart.
    tick_until(&r,43000); assert(reading_y(&r)>0 && r.direction==1);
    reading_press(&r,200,100,43100); reading_move(&r,200,-9000,43120);
    assert(reading_release(&r,200,-9000,43140)==READING_VERTICAL);
    tick_until(&r,55120); assert(reading_y(&r)==r.maximum);
    tick_until(&r,55140); assert(!reading_y(&r)); // Manual bottom uses the same dwell.
    reading_init(&r,200); reading_press(&r,20,20,250);
    assert(!reading_tick(&r,20000,true) && !r.focused);
    reading_release(&r,20,20,20010); assert(!reading_tick(&r,29000,true));
    assert(!reading_tick(&r,31000,false));
    reading_activity(&r,31010); assert(!reading_tick(&r,40000,true));
    reading_init(&r,UINT32_MAX-9000); assert(!reading_tick(&r,998,true));
    assert(reading_tick(&r,999,true)); // Ten-second timeout across tick rollover.
    reading_bounds(&r,30,156); tick_until(&r,7000); assert(!reading_y(&r));

    reading_init(&r,0); reading_bounds(&r,1000,156); r.drag_zoom=172;
    reading_press(&r,200,130,100); reading_move(&r,200,63,120);
    assert(reading_y(&r)>=99 && reading_y(&r)<=100); // A scaled page still follows the finger.
    touch_key_filter_t f; count=0;
    touch_key_filter_init(&f,1,1,0);
    touch_key_filter_raw(&f,0,0,100,emit,NULL);
    touch_key_filter_raw(&f,0,1,104,emit,NULL);
    touch_key_filter_tick(&f,1000,emit,NULL); assert(count==0); // Reject contact bounce.
    touch_key_filter_init(&f,1,1,0); count=0;
    // Captured edges remain a double click even if delivered after a long stall.
    touch_key_filter_raw(&f,0,0,100,emit,NULL);
    touch_key_filter_raw(&f,0,1,170,emit,NULL);
    touch_key_filter_raw(&f,0,0,545,emit,NULL);
    touch_key_filter_raw(&f,0,1,565,emit,NULL);
    touch_key_filter_tick(&f,1400,emit,NULL);
    assert(count==1 && events[0]==BUTTON_TOGGLE);
    touch_key_filter_init(&f,1,1,0); count=0;
    touch_key_filter_raw(&f,0,0,100,emit,NULL);
    touch_key_filter_raw(&f,0,1,1200,emit,NULL);
    touch_key_filter_tick(&f,2000,emit,NULL);
    assert(count==1 && events[0]==BUTTON_DELETE);
    touch_key_filter_init(&f,0,0,0); count=0;
    touch_key_filter_tick(&f,5000,emit,NULL); assert(count==0); // Consume power-on holds.
    touch_key_filter_raw(&f,0,1,5100,emit,NULL);
    touch_key_filter_raw(&f,1,1,5100,emit,NULL);
    touch_key_filter_tick(&f,6000,emit,NULL); assert(count==0);

    buttons_t b; buttons_reset(&b); count=0;
    touch_keys_tick(&b,true,false,100,emit); touch_keys_tick(&b,false,false,150,emit);
    touch_keys_tick(&b,false,false,650,emit); assert(count==0);
    touch_keys_tick(&b,false,false,651,emit); assert(count==1 && events[0]==BUTTON_OPEN);
    buttons_reset(&b); count=0;
    touch_keys_tick(&b,true,false,100,emit); touch_keys_tick(&b,false,false,150,emit);
    touch_keys_tick(&b,true,false,250,emit); touch_keys_tick(&b,false,false,300,emit);
    assert(count==1 && events[0]==BUTTON_TOGGLE);
    buttons_reset(&b); count=0;
    touch_keys_tick(&b,true,false,100,emit); touch_keys_tick(&b,false,false,190,emit);
    touch_keys_tick(&b,true,false,565,emit); touch_keys_tick(&b,false,false,665,emit);
    assert(count==1 && events[0]==BUTTON_TOGGLE); // Observed 375 ms between clicks.
    buttons_reset(&b); count=0;
    touch_keys_tick(&b,true,false,100,emit); touch_keys_tick(&b,false,false,150,emit);
    touch_keys_tick(&b,true,false,600,emit); touch_keys_tick(&b,false,false,900,emit);
    assert(count==1 && events[0]==BUTTON_TOGGLE); // Release can finish after 500 ms.
    buttons_reset(&b); count=0;
    touch_keys_tick(&b,true,false,100,emit); touch_keys_tick(&b,true,false,1100,emit);
    touch_keys_tick(&b,false,false,1200,emit); assert(count==1 && events[0]==BUTTON_DELETE);
    buttons_reset(&b); count=0;
    touch_keys_tick(&b,false,true,100,emit); touch_keys_tick(&b,false,false,150,emit);
    touch_keys_tick(&b,false,false,501,emit); assert(count==1 && events[0]==TOUCH_KEY_WAKE);
    buttons_reset(&b); count=0;
    touch_keys_tick(&b,false,true,100,emit); touch_keys_tick(&b,false,false,150,emit);
    touch_keys_tick(&b,false,true,250,emit); touch_keys_tick(&b,false,false,300,emit);
    assert(count==2 && events[0]==TOUCH_KEY_WAKE && events[1]==TOUCH_KEY_WAKE);
    buttons_reset(&b); count=0;
    touch_keys_tick(&b,false,true,100,emit); touch_keys_tick(&b,false,true,1200,emit);
    assert(count==0); touch_keys_tick(&b,false,false,1400,emit);
    assert(count==1 && events[0]==TOUCH_KEY_WAKE);
    buttons_reset(&b); count=0;
    touch_keys_tick(&b,false,true,100,emit); touch_keys_tick(&b,false,true,1200,emit);
    touch_keys_tick(&b,false,true,3100,emit); touch_keys_tick(&b,false,true,4100,emit);
    touch_keys_tick(&b,false,false,4200,emit); assert(count==1 && events[0]==TOUCH_KEY_POWER);
    puts("reading and keys: idle, continuous drag, swipe, tap, forward loop, pause, rollover and simplified BOOT passed");
    return 0;
}
