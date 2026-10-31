#include <assert.h>
#include <stdio.h>
#include "../firmware/main/reading_logic.h"
static void full(reading_t *r) { reading_init(r,0); reading_bounds(r,1200,156); r->focused=true; r->focus_at=UINT32_MAX-3000; }
static void hold(reading_t *r,int x,int y,uint32_t at) {
    reading_press(r,x,y,at); reading_move(r,x+2,y+1,at+READING_HOLD_MS);
    assert(r->hold_armed);
}
int main(void) {
    reading_t r; full(&r);
    reading_press(&r,300,80,100);
    assert(reading_release(&r,301,81,200)==READING_TAP);
    full(&r); hold(&r,300,80,100); reading_move(&r,190,82,740);
    assert(reading_release(&r,180,83,760)==READING_DELETE && !r.down);
    full(&r); hold(&r,300,80,100); reading_move(&r,400,82,740);
    assert(reading_release(&r,410,83,760)==READING_SEND);
    full(&r); hold(&r,300,80,100); reading_move(&r,190,82,740); reading_move(&r,280,80,760);
    assert(reading_release(&r,280,80,780)==READING_NONE); // Slide back to cancel.
    full(&r); hold(&r,300,80,100);
    assert(reading_release(&r,302,81,900)==READING_NONE); // A hold alone does not exit fullscreen.
    full(&r); reading_press(&r,300,80,100); reading_move(&r,311,89,400); reading_move(&r,311,89,700);
    assert(r.hold_armed); // Small finger drift is still a hold.
    assert(reading_release(&r,190,85,800)==READING_DELETE);
    full(&r); reading_press(&r,300,80,100); reading_move(&r,318,98,300);
    assert(reading_release(&r,301,81,900)!=READING_TAP); // A rejected long hold is never a short tap.
    full(&r); reading_press(&r,300,80,100); reading_move(&r,270,81,200); reading_move(&r,260,81,900);
    assert(!r.hold_armed && reading_release(&r,200,81,950)==READING_NEXT); // Early movement cannot arm deletion.
    full(&r); hold(&r,300,130,100); reading_move(&r,302,80,740);
    assert(reading_release(&r,301,30,760)==READING_VERTICAL && reading_y(&r)==100);
    full(&r); hold(&r,300,80,100); reading_cancel(&r,740);
    assert(reading_release(&r,180,80,760)==READING_NONE); // Changed content cancels an armed action.
    full(&r); r.focused=false; reading_press(&r,300,80,100); reading_move(&r,300,80,800);
    assert(!r.hold_armed && reading_release(&r,180,80,900)==READING_NEXT);
    full(&r); reading_press(&r,300,80,UINT32_MAX-400);
    reading_move(&r,301,81,199); assert(r.hold_armed); // Hold clock wraps safely.
    assert(reading_release(&r,410,81,250)==READING_SEND);
    reading_init(&r,0); // Double tap in the controls view immediately enters fullscreen.
    reading_press(&r,250,70,100); assert(reading_release(&r,251,71,180)==READING_TAP);
    reading_activity(&r,190); // A normal tap's controls redraw must keep the tap history.
    reading_pointer_lost(&r,200); // LVGL target changes after release must not clear a completed tap.
    reading_press(&r,253,71,290); assert(reading_release(&r,254,71,360)==READING_FOCUS);
    reading_init(&r,0);
    reading_press(&r,300,70,100); assert(reading_release(&r,310,70,180)==READING_TAP);
    reading_press(&r,305,70,290); assert(reading_release(&r,314,70,360)==READING_FOCUS); // Small tap drift is tolerated.
    reading_init(&r,0);
    reading_press(&r,250,70,100); assert(reading_release(&r,250,70,180)==READING_TAP);
    reading_press(&r,250,70,1000); assert(reading_release(&r,250,70,1100)==READING_TAP); // Too late.
    reading_init(&r,0);
    reading_press(&r,250,70,100); assert(reading_release(&r,250,70,180)==READING_TAP);
    reading_press(&r,260,71,800); assert(reading_release(&r,261,71,1150)==READING_FOCUS); // Second press counts; its release can be later.
    reading_init(&r,0);
    reading_press(&r,250,70,100); assert(reading_release(&r,250,70,180)==READING_TAP);
    reading_press(&r,350,70,250); assert(reading_release(&r,350,70,300)==READING_TAP); // Different location.
    reading_init(&r,0);
    reading_press(&r,250,70,100); reading_release(&r,250,70,180); reading_cancel(&r,200);
    reading_press(&r,250,70,250); assert(reading_release(&r,250,70,300)==READING_TAP); // Changed content cancels the first tap.
    reading_init(&r,0); reading_focus(&r,true,1000);
    reading_press(&r,250,70,1100); assert(reading_release(&r,250,70,1200)==READING_NONE);
    reading_press(&r,250,70,2900); assert(reading_release(&r,250,70,3100)==READING_NONE); // Started inside the two-second guard.
    reading_press(&r,250,70,3200); assert(reading_release(&r,250,70,3300)==READING_TAP);
    reading_init(&r,0); reading_focus(&r,true,1000); hold(&r,300,70,1100);
    assert(reading_release(&r,410,70,1800)==READING_SEND); // Deliberate actions stay available during the guard.
    puts("fullscreen hold/swipe: tap, arm, delete, send, cancellation, early movement, vertical reading, identity change and rollover passed");
    return 0;
}
