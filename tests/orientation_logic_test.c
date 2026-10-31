#include <assert.h>
#include <stdio.h>
#include "../firmware/main/orientation_logic.h"
static uint32_t now;
static int events;
static void tick(orientation_t *o,float angle,float speed,bool flat) {
    now+=20; float radians=angle*0.01745329252f;
    if(orientation_tick(o,flat?0:-sinf(radians),flat?0:-cosf(radians),flat?1:0,0,0,speed,0,true,now)) events++;
}
static void reset(orientation_t *o,bool saved) { now=0;events=0;orientation_init(o,saved,now); }
static void settle(orientation_t *o,float angle,bool flat) { for(int i=0;i<60;i++) tick(o,angle,0,flat); }
int main(void) {
    orientation_t o;
    reset(&o,false);settle(&o,180,false);
    assert(o.flipped && events==1); // Inverted boot corrects without a gesture.
    settle(&o,180,false);assert(events==1);
    settle(&o,0,false);assert(!o.flipped && events==2);
    reset(&o,true);settle(&o,0,false);assert(!o.flipped && events==1); // Saved pose cannot override gravity.
    reset(&o,false);settle(&o,0,false);
    for(int i=0;i<50;i++) tick(&o,180,0,false);
    assert(o.flipped); // Corrects even without the gesture's required gyro turn.
    // There is deliberately no mode/menu/popup permission in orientation_tick.
    reset(&o,true);settle(&o,0,true);assert(o.flipped && events==0); // Flat boot keeps the saved direction.
    for(int i=0;i<50;i++) tick(&o,0,-180,true);
    settle(&o,0,true);assert(!o.flipped && events==1); // Gyro follows a flat in-plane turn.
    reset(&o,false);settle(&o,0,false);
    for(int i=0;i<150;i++) tick(&o,(i%2)?85:95,0,false);
    assert(!o.flipped && events==0); // Around vertical does not chatter.
    reset(&o,false);
    for(int i=0;i<50;i++) {
        now+=20;assert(!orientation_tick(&o,NAN,0,1,0,0,0,0,true,now));
    }
    assert(!o.flipped);
    puts("orientation: inverted boot, gravity recovery, saved/flat pose, gyro, hysteresis and invalid samples passed");
}
