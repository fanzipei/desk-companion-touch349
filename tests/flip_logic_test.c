#include <assert.h>
#include <stdio.h>
#include "../firmware/main/flip_logic.h"
static uint32_t now; static int events;
static void tick(flip_t *f,float degrees,float speed,bool allow,bool flat) {
    now+=20; float radians=degrees*0.01745329252f;
    if(flip_tick(f,flat?0:sinf(radians),flat?0:cosf(radians),flat?1:0,0,0,speed,now,allow)) events++;
}
static void calibrate(flip_t *f,bool flat) {
    now=0;events=0;flip_init(f,0);
    for(int i=0;i<50;i++) tick(f,0,0,true,flat);
    assert(f->ready && !f->flipped && !events);
}
static void rotate(flip_t *f,float from,float to,bool allow,bool flat) {
    float speed=to-from;
    for(int i=1;i<=50;i++) tick(f,from+(to-from)*i/50,speed,allow,flat);
}
int main(void) {
    flip_t f; calibrate(&f,false);
    rotate(&f,0,180,true,false);
    for(int i=0;i<45;i++) tick(&f,180,0,true,false);
    assert(events==1 && f.flipped);
    for(int i=0;i<100;i++) tick(&f,180,0,true,false);
    assert(events==1); // Staying inverted never repeats a mode change.
    rotate(&f,180,0,true,false);
    for(int i=0;i<45;i++) tick(&f,0,0,true,false);
    assert(events==2 && !f.flipped);
    calibrate(&f,false);
    rotate(&f,0,80,true,false); rotate(&f,80,0,true,false);
    for(int i=0;i<80;i++) tick(&f,0,0,true,false);
    assert(!events); // Small rotations and desk movement do not switch.
    for(int i=0;i<100;i++) tick(&f,180,0,true,false);
    assert(!events); // Gravity reversal without an in-plane turn is rejected.
    calibrate(&f,false); rotate(&f,0,180,false,false);
    for(int i=0;i<70;i++) tick(&f,180,0,false,false);
    assert(!events);
    for(int i=0;i<40;i++) tick(&f,180,0,true,false);
    assert(events==1); // A popup/touch defers the stable turn until released.
    calibrate(&f,true); rotate(&f,0,-180,true,true);
    for(int i=0;i<50;i++) tick(&f,-180,0,true,true);
    assert(events==1 && f.flipped); // Gyro also handles a flat screen.
    puts("180-degree turn: stable/one-shot/return/shake/face-flip/pause/flat-screen passed");
    return 0;
}
