// Copyright 2026 fanzipei. SPDX-License-Identifier: Apache-2.0
#include "touch_ui.h"
#include "touch_font.h"
#include "reading_logic.h"
#include "news_order.h"
#include "lvgl.h"
#include "esp_log.h"
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <limits.h>

static lv_obj_t *tabs[2], *body, *text, *clock_text, *connection_text, *footer, *nav[2], *tools[3];
static lv_color_t *page_pixels;
static lv_obj_t *overlay, *panel, *question, *menu_buttons[8];
static lv_obj_t *gesture_hint;
static lv_obj_t *news_alert_layer, *news_alert_caption;
static bool news_alert_visible, news_alert_press;
static int news_alert_x, news_alert_y;
static uint32_t news_alert_pressed_at;
static bool news_alert_moved;
static uint32_t gesture_hint_color=UINT32_MAX;
static QueueHandle_t actions;
static bool menu_open, confirming, has_content;
static reading_t reader;
static bool theme_dark;
static char identity[48];
static char gesture_identity[48];
static char confirmation_identity[48];
static int view_mode;
static uint32_t foreground, background;
static display_settings_t content_settings;
static char content[3073], source_name[100], current_toast[96];
static uint16_t line_offsets[3074];
static int line_count=1, layout_width, layout_font, layout_spacing;
static int drawn_line=-1, drawn_y=-1, pending_page=INT_MIN;
static int current_index, current_count, current_unread;
static bool current_pinned;
static void apply_geometry(void);
static void reflow(const char *value,const display_settings_t *settings);
static void render_window(bool force);
static void update_footer(void);
static void update_gesture_hint(void);
// A single logical page is shown at 100% fullscreen or uniformly scaled to
// fit between the controls. Font metrics, wraps and reading offsets never change.
static int viewport_height(void) { return 156; }
static int text_width(void) { return 616; }
static int text_zoom(void) { return reader.focused?256:172; }
static int text_x(void) { return reader.focused?0:(480-(616*172+128)/256)/2; }
static int line_height(void) { return content_settings.font_size+content_settings.line_spacing; }
void touch_ui_activity(void) { reading_activity(&reader,lv_tick_get()); }
const char *touch_ui_delete_identity(void) { return confirming?confirmation_identity:""; }

static void emit_target(const char *action,const char *target) {
    touch_command_t command={.action=action};
    if(target) snprintf(command.identity,sizeof(command.identity),"%s",target);
    if(xQueueSend(actions,&command,0)!=pdTRUE) ESP_LOGW("touch_ui","action queue full");
}
static void emit(const char *action) { emit_target(action,NULL); }
bool touch_ui_action(touch_command_t *command) {
    return xQueueReceive(actions,command,0)==pdTRUE;
}
static lv_obj_t *label(lv_obj_t *parent,const char *value,int x,int y,int width,int size) {
    lv_obj_t *o=lv_label_create(parent);
    lv_obj_set_pos(o,x,y); lv_obj_set_width(o,width);
    lv_obj_set_style_text_font(o,touch_font(size),0);
    lv_obj_set_style_text_color(o,lv_color_hex(0xDFE8F5),0);
    lv_label_set_long_mode(o,LV_LABEL_LONG_CLIP); lv_label_set_text(o,value);
    return o;
}
static void clicked(lv_event_t *event) {
    if(news_alert_visible) return;
    reading_cancel(&reader,lv_tick_get()); emit((const char *)lv_event_get_user_data(event));
}
static lv_obj_t *button(lv_obj_t *parent,const char *value,int x,int y,int w,int h,const char *action) {
    lv_obj_t *o=lv_btn_create(parent); lv_obj_set_pos(o,x,y); lv_obj_set_size(o,w,h);
    lv_obj_clear_flag(o,LV_OBJ_FLAG_SCROLLABLE); lv_obj_set_style_radius(o,8,0);
    lv_obj_set_style_pad_all(o,0,0); lv_obj_set_style_shadow_width(o,0,0);
    lv_obj_set_style_bg_color(o,lv_color_hex(0x25354B),0);
    lv_obj_set_style_bg_color(o,lv_color_hex(0x367DE2),LV_STATE_PRESSED);
    lv_obj_add_event_cb(o,clicked,LV_EVENT_CLICKED,(void *)action);
    lv_obj_t *caption=label(o,value,0,0,w,16);
    lv_obj_set_style_text_align(caption,LV_TEXT_ALIGN_CENTER,0);
    lv_obj_center(caption); return o;
}
static void hide(lv_obj_t *o,bool hidden) {
    if(hidden) lv_obj_add_flag(o,LV_OBJ_FLAG_HIDDEN); else lv_obj_clear_flag(o,LV_OBJ_FLAG_HIDDEN);
}
static void modal(bool confirm) {
    reading_cancel(&reader,lv_tick_get());
    confirming=confirm; menu_open=true;
    snprintf(confirmation_identity,sizeof(confirmation_identity),"%s",confirm?identity:"");
    hide(overlay,false);
    lv_label_set_text(question,confirm?(view_mode?"删除这条新闻？":"删除这条便签？"):"阅读与操作");
    for(int i=0;i<8;i++) hide(menu_buttons[i],confirm?(i<6):(i>=6));
}
static void dismiss_modal(void) {
    confirming=menu_open=false; confirmation_identity[0]=0; hide(overlay,true);
}
static void body_event(lv_event_t *event) {
    lv_event_code_t code=lv_event_get_code(event);
    if(!touch_board_awake()) return;
    if(news_alert_visible) { reading_cancel(&reader,lv_tick_get()); return; }
    if(code==LV_EVENT_PRESSED) {
        lv_point_t p; lv_indev_get_point(lv_indev_get_act(),&p);
        reading_press(&reader,p.x,p.y,lv_tick_get());
        if(!has_content) reader.hold_eligible=false;
        snprintf(gesture_identity,sizeof(gesture_identity),"%s",identity);
    } else if(code==LV_EVENT_PRESSING) {
        lv_point_t p; lv_indev_get_point(lv_indev_get_act(),&p);
        reading_move(&reader,p.x,p.y,lv_tick_get()); render_window(false);
    } else if(code==LV_EVENT_RELEASED && !menu_open) {
        lv_point_t p; lv_indev_get_point(lv_indev_get_act(),&p);
        reading_gesture_t gesture=reading_release(&reader,p.x,p.y,lv_tick_get());
        if(gesture==READING_DELETE) emit_target("gesture_delete",gesture_identity);
        else if(gesture==READING_SEND) emit_target("gesture_open",gesture_identity);
        else if(gesture==READING_FOCUS) emit("focus");
        else if(gesture==READING_NEXT) emit("swipe_next");
        else if(gesture==READING_PREVIOUS) emit("swipe_previous");
        else if(gesture==READING_TAP) emit("controls");
        render_window(false);
    } else if(code==LV_EVENT_PRESS_LOST) {
        reading_pointer_lost(&reader,lv_tick_get()); update_gesture_hint();
    }
}
static void news_alert_event(lv_event_t *event) {
    if(!news_alert_visible || !touch_board_awake()) return;
    lv_event_code_t code=lv_event_get_code(event);
    if(code!=LV_EVENT_PRESSED && code!=LV_EVENT_PRESSING && code!=LV_EVENT_RELEASED && code!=LV_EVENT_PRESS_LOST) return;
    if(!lv_indev_get_act()) return;
    lv_point_t point; lv_indev_get_point(lv_indev_get_act(),&point);
    if(code==LV_EVENT_PRESSED) {
        news_alert_press=true; news_alert_moved=false;
        news_alert_x=point.x; news_alert_y=point.y; news_alert_pressed_at=lv_tick_get();
    } else if(code==LV_EVENT_PRESSING && news_alert_press) {
        if(abs(point.x-news_alert_x)>=READING_DRAG_THRESHOLD || abs(point.y-news_alert_y)>=READING_DRAG_THRESHOLD) news_alert_moved=true;
    } else if(code==LV_EVENT_RELEASED && news_alert_press) {
        news_alert_press=false;
        if(!news_alert_moved && abs(point.x-news_alert_x)<READING_DRAG_THRESHOLD &&
           abs(point.y-news_alert_y)<READING_DRAG_THRESHOLD && lv_tick_get()-news_alert_pressed_at<READING_HOLD_MS) emit("dismiss_news_alert");
    } else if(code==LV_EVENT_PRESS_LOST) news_alert_press=false;
}
static void update_news_alert(bool pending,uint32_t received) {
    bool visible=pending;
    if(visible!=news_alert_visible) {
        reading_cancel(&reader,lv_tick_get()); news_alert_press=false;
        news_alert_visible=visible;
    }
    hide(news_alert_layer,!visible);
    if(visible) {
        char message[128];
        if(received) snprintf(message,sizeof(message),"累计收到 %lu 条新新闻\n点击屏幕继续阅读",(unsigned long)received);
        else snprintf(message,sizeof(message),"新闻列表已更新\n点击屏幕继续阅读");
        if(strcmp(lv_label_get_text(news_alert_caption),message)) lv_label_set_text(news_alert_caption,message);
        lv_obj_move_foreground(news_alert_layer);
    }
}
static void set_focus(bool focused) {
    reading_focus(&reader,focused,lv_tick_get()); dismiss_modal(); apply_geometry();
    reflow(content,&content_settings);
}
bool touch_ui_local_action(const char *action) {
    touch_ui_activity();
    if(!strcmp(action,"menu")) { if(menu_open) dismiss_modal(); else modal(false); return true; }
    if(!strcmp(action,"cancel")) { dismiss_modal(); return true; }
    if(!strcmp(action,"delete")) { if(has_content) modal(true); return true; }
    if(!strcmp(action,"confirm_delete")) {
        if(!confirming) return true;
        dismiss_modal(); return false;
    }
    if(!strcmp(action,"focus")) {
        set_focus(!reader.focused); return true;
    }
    if(!strcmp(action,"controls")) { set_focus(false); return true; }
    if(!strcmp(action,"scroll_up") || !strcmp(action,"scroll_down")) {
        reading_seek(&reader,reading_y(&reader)+(!strcmp(action,"scroll_up")?-40:40));
        if(reader.maximum && reading_y(&reader)==reader.maximum) reading_bottom(&reader,lv_tick_get());
        render_window(false); return true;
    }
    if(!strcmp(action,"font_up") || !strcmp(action,"font_down") || !strcmp(action,"theme")) dismiss_modal();
    return false;
}
int touch_ui_lines(const display_settings_t *settings) {
    int height=viewport_height();
    int count=1+(height-settings->font_size)/(settings->font_size+settings->line_spacing);
    return count>0?count:1;
}
static uint32_t decode(const unsigned char *p,int *count) {
    uint32_t cp=*p; *count=1;
    if(cp<128) return cp;
    int size=(cp&0xE0)==0xC0?2:(cp&0xF0)==0xE0?3:(cp&0xF8)==0xF0?4:1;
    if(size==1) return '?';
    cp &= (1U<<(7-size))-1;
    for(int i=1;i<size;i++) { if(!p[i] || (p[i]&0xC0)!=0x80) return '?'; cp=(cp<<6)|(p[i]&63); }
    *count=size; return cp;
}
// Byte offsets keep even newline-heavy 3072-byte notes reachable without a
// label taller than LVGL's 16-bit coordinate range. Only visible lines render.
static int wrap_lines(const char *value,const display_settings_t *settings,int limit,uint16_t *offsets) {
    int lines=1,width=0;
    if(offsets) offsets[0]=0;
    const unsigned char *p=(const unsigned char *)value;
    while(*p) {
        int bytes; uint32_t cp=decode(p,&bytes);
        if(cp=='\r') {p+=bytes;continue;}
        if(cp=='\t') cp=' ';
        int advance=cp=='\n'?0:touch_glyph_width(cp,settings->font_size);
        if(cp=='\n' || (width && width+advance>limit)) {
            int offset=(const char *)p-value+(cp=='\n'?bytes:0);
            if(offsets) offsets[lines]=(uint16_t)offset;
            lines++; width=0;
            if(cp=='\n') { p+=bytes; continue; }
        }
        width+=advance; p+=bytes;
    }
    return lines;
}
static int page_step(void) {
    int step=touch_ui_lines(&content_settings)*line_height(); return step>0?step:1;
}
int touch_ui_page(void) {
    if(!reader.maximum) return 0;
    int step=page_step();
    return reading_y(&reader)>=reader.maximum?(reader.maximum+step-1)/step:reading_y(&reader)/step;
}
static void render_window(bool force) {
    if(!text || !layout_font) return;
    int height=line_height(), offset=reading_y(&reader);
    int first=offset/height, y=-(offset%height);
    static char output[8192];
    bool repaint=force || first!=drawn_line || y!=drawn_y;
    if(force || first!=drawn_line) {
        size_t used=0, bytes=strlen(content);
        int end=first+(viewport_height()+height-1)/height+2;
        if(end>line_count) end=line_count;
        for(int line=first;line<end;line++) {
            size_t from=line_offsets[line],to=line+1<line_count?line_offsets[line+1]:bytes;
            while(to>from && (content[to-1]=='\n' || content[to-1]=='\r')) to--;
            while(from<to && used+2<sizeof(output)) {
                char ch=content[from++]; if(ch!='\r') output[used++]=ch=='\t'?' ':ch;
            }
            if(line+1<end && used+2<sizeof(output)) output[used++]='\n';
        }
        output[used]=0; drawn_line=first;
    }
    if(repaint) {
        lv_canvas_fill_bg(text,lv_color_hex(background),LV_OPA_COVER);
        lv_draw_label_dsc_t style; lv_draw_label_dsc_init(&style);
        style.font=touch_font(content_settings.font_size); style.color=lv_color_hex(foreground);
        style.line_space=content_settings.line_spacing;
        lv_canvas_draw_text(text,0,y,616,&style,output);
        drawn_y=y;
    }
    update_footer();
    update_gesture_hint();
}
static void reflow(const char *value,const display_settings_t *settings) {
    bool changed=strcmp(content,value)!=0;
    bool status_only=changed && view_mode && news_status_only_changed(content,value);
    int width=text_width();
    bool rebuild=changed || layout_width!=width || layout_font!=settings->font_size || layout_spacing!=settings->line_spacing;
    if(!rebuild && pending_page==INT_MIN) { render_window(false); return; }
    int old_height=line_height(), old_line=old_height?reading_y(&reader)/old_height:0;
    if(old_line>=line_count) old_line=line_count-1;
    int anchor=changed && !status_only?0:line_offsets[old_line];
    int fraction=old_height?reading_y(&reader)%old_height:0;
    if(changed) {
        if(!status_only) { reading_cancel(&reader,lv_tick_get()); reader.direction=1; touch_ui_activity(); }
        snprintf(content,sizeof(content),"%s",value);
    }
    content_settings=*settings;
    line_count=wrap_lines(content,settings,width,line_offsets);
    layout_width=width; layout_font=settings->font_size; layout_spacing=settings->line_spacing;
    reading_bounds(&reader,(line_count-1)*line_height()+settings->font_size,viewport_height());
    if(pending_page!=INT_MIN) {
        reading_seek(&reader,pending_page<0?reader.maximum:pending_page*page_step()); pending_page=INT_MIN;
    } else {
        int row=0;
        while(row+1<line_count && line_offsets[row+1]<=anchor) row++;
        reading_seek(&reader,row*line_height()+fraction);
    }
    drawn_line=drawn_y=-1; render_window(true);
}
void touch_ui_reset_position(int page) {
    pending_page=page; reader.direction=1; touch_ui_activity();
}
bool touch_ui_step(int direction) {
    touch_ui_activity(); dismiss_modal();
    int before=reading_y(&reader);
    if((direction>0 && before>=reader.maximum) || (direction<0 && !before)) return false;
    reading_seek(&reader,before+direction*page_step()); render_window(false);
    return true;
}
int touch_ui_text(const char *value,int selected,const display_settings_t *settings,bool render) {
    int height=(wrap_lines(value,settings,text_width(),NULL)-1)*(settings->font_size+settings->line_spacing)+settings->font_size;
    int maximum=height>viewport_height()?height-viewport_height():0;
    int step=touch_ui_lines(settings)*(settings->font_size+settings->line_spacing);
    if(render) reflow(value,settings);
    return step>0?1+(maximum+step-1)/step:1;
}
void touch_ui_init(void) {
    reading_init(&reader,lv_tick_get());
    content_settings=(display_settings_t){.magic=DISPLAY_MAGIC,.font_size=16,.line_spacing=7,.hints=1};
    actions=xQueueCreate(12,sizeof(touch_command_t)); assert(actions);
    lv_obj_t *screen=lv_scr_act();
    lv_obj_clear_flag(screen,LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(screen,LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_PRESS_LOCK);
    lv_obj_add_event_cb(screen,body_event,LV_EVENT_ALL,NULL);
    tabs[0]=button(screen,"便签",6,0,72,29,"notes");
    tabs[1]=button(screen,"新闻",82,0,72,29,"news");
    clock_text=label(screen,"",174,7,320,16);
    connection_text=label(screen,"",491,7,143,12);
    body=lv_obj_create(screen);
    lv_obj_set_pos(body,48,34); lv_obj_set_size(body,480,105);
    lv_obj_set_style_pad_all(body,0,0); lv_obj_set_style_border_width(body,0,0);
    lv_obj_set_style_radius(body,0,0); lv_obj_clear_flag(body,LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(body,LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_PRESS_LOCK);
    lv_obj_add_event_cb(body,body_event,LV_EVENT_ALL,NULL);
    text=lv_canvas_create(body); lv_obj_clear_flag(text,LV_OBJ_FLAG_CLICKABLE);
    page_pixels=heap_caps_malloc(616*156*sizeof(lv_color_t),MALLOC_CAP_SPIRAM);
    assert(page_pixels);
    lv_canvas_set_buffer(text,page_pixels,616,156,LV_IMG_CF_TRUE_COLOR);
    lv_img_set_pivot(text,0,0); lv_img_set_antialias(text,true);
    lv_canvas_fill_bg(text,lv_color_hex(0x111C2D),LV_OPA_COVER);
    nav[0]=button(screen,"<",4,35,38,104,"item_previous");
    nav[1]=button(screen,">",532,35,38,104,"item_next");
    tools[0]=button(screen,"置顶",577,34,59,32,"pin");
    tools[1]=button(screen,"电脑",577,71,59,32,"open");
    tools[2]=button(screen,"更多",577,108,59,32,"menu");
    footer=label(screen,"",8,150,624,12);
    gesture_hint=label(screen,"",50,128,540,16);
    lv_obj_set_height(gesture_hint,34);
    lv_obj_set_style_bg_opa(gesture_hint,LV_OPA_COVER,0);
    lv_obj_set_style_text_color(gesture_hint,lv_color_white(),0);
    lv_obj_set_style_text_align(gesture_hint,LV_TEXT_ALIGN_CENTER,0);
    lv_obj_set_style_pad_all(gesture_hint,7,0); lv_obj_set_style_radius(gesture_hint,8,0);
    lv_obj_clear_flag(gesture_hint,LV_OBJ_FLAG_CLICKABLE); hide(gesture_hint,true);
    overlay=lv_obj_create(screen); lv_obj_set_size(overlay,640,172); lv_obj_set_pos(overlay,0,0);
    lv_obj_set_style_bg_color(overlay,lv_color_hex(0x080E18),0);
    lv_obj_set_style_bg_opa(overlay,220,0); lv_obj_set_style_border_width(overlay,0,0);
    lv_obj_set_style_pad_all(overlay,0,0); lv_obj_clear_flag(overlay,LV_OBJ_FLAG_SCROLLABLE);
    panel=lv_obj_create(overlay); lv_obj_set_pos(panel,82,9); lv_obj_set_size(panel,476,154);
    lv_obj_set_style_bg_color(panel,lv_color_hex(0x192A40),0);
    lv_obj_set_style_border_width(panel,1,0); lv_obj_set_style_border_color(panel,lv_color_hex(0x405876),0);
    lv_obj_set_style_pad_all(panel,0,0); lv_obj_set_style_radius(panel,12,0);
    lv_obj_clear_flag(panel,LV_OBJ_FLAG_SCROLLABLE);
    question=label(panel,"阅读与操作",18,11,440,16);
    const char *names[]={"字号 -","字号 +","切换主题","删除","阅读模式","关闭","确认删除","取消"};
    const char *commands[]={"font_down","font_up","theme","delete","focus","cancel","confirm_delete","cancel"};
    for(int i=0;i<6;i++) menu_buttons[i]=button(panel,names[i],15+(i%3)*151,42+(i/3)*49,143,42,commands[i]);
    menu_buttons[6]=button(panel,names[6],35,81,185,48,commands[6]);
    menu_buttons[7]=button(panel,names[7],252,81,185,48,commands[7]);
    lv_obj_set_style_bg_color(menu_buttons[6],lv_color_hex(0xB74252),0);
    dismiss_modal();
    news_alert_layer=lv_obj_create(screen);
    lv_obj_set_pos(news_alert_layer,0,0); lv_obj_set_size(news_alert_layer,640,172);
    lv_obj_set_style_bg_color(news_alert_layer,lv_color_hex(0x080E18),0);
    lv_obj_set_style_bg_opa(news_alert_layer,220,0); lv_obj_set_style_border_width(news_alert_layer,0,0);
    lv_obj_set_style_pad_all(news_alert_layer,0,0); lv_obj_clear_flag(news_alert_layer,LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(news_alert_layer,LV_OBJ_FLAG_CLICKABLE|LV_OBJ_FLAG_PRESS_LOCK);
    lv_obj_add_event_cb(news_alert_layer,news_alert_event,LV_EVENT_ALL,NULL);
    lv_obj_t *notice=lv_obj_create(news_alert_layer);
    lv_obj_set_pos(notice,94,20); lv_obj_set_size(notice,452,132);
    lv_obj_set_style_pad_all(notice,0,0); lv_obj_set_style_radius(notice,12,0);
    lv_obj_set_style_bg_color(notice,lv_color_hex(0x192A40),0);
    lv_obj_set_style_border_color(notice,lv_color_hex(0x408FD0),0); lv_obj_set_style_border_width(notice,1,0);
    lv_obj_clear_flag(notice,LV_OBJ_FLAG_CLICKABLE|LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_t *title=label(notice,"有新新闻",16,14,420,24);
    lv_obj_set_style_text_align(title,LV_TEXT_ALIGN_CENTER,0);
    news_alert_caption=label(notice,"",16,52,420,16);
    lv_obj_set_height(news_alert_caption,64); lv_obj_set_style_text_line_space(news_alert_caption,8,0);
    lv_obj_set_style_text_align(news_alert_caption,LV_TEXT_ALIGN_CENTER,0);
    lv_obj_clear_flag(title,LV_OBJ_FLAG_CLICKABLE); lv_obj_clear_flag(news_alert_caption,LV_OBJ_FLAG_CLICKABLE);
    hide(news_alert_layer,true);
}
static void apply_geometry(void) {
    bool full=reader.focused;
    for(int i=0;i<2;i++) { hide(tabs[i],full); hide(nav[i],full); }
    for(int i=0;i<3;i++) hide(tools[i],full);
    hide(clock_text,full); hide(connection_text,full); hide(footer,full);
    lv_obj_set_pos(body,full?12:48,full?8:34);
    lv_obj_set_size(body,full?616:480,full?156:105);
    lv_obj_set_pos(text,text_x(),0);
    lv_img_set_zoom(text,text_zoom());
    reader.drag_zoom=text_zoom();
    drawn_y=-1;
}
static void update_footer(void) {
    if(!footer) return;
    char buf[256];
    if(*current_toast) snprintf(buf,sizeof(buf),"%s",current_toast);
    else if(current_count) {
        int progress=reader.maximum?reading_y(&reader)*100/reader.maximum:100;
        char unread[32]="";
        if(view_mode) snprintf(unread,sizeof(unread),"未读%d   ",current_unread);
        snprintf(buf,sizeof(buf),"%s %02d/%02d   %s%d%%   %s%s%s",view_mode?"新闻":"便签",current_index+1,current_count,unread,progress,current_pinned?"已置顶  ":"",source_name,content_settings.hints?"   上下阅读 | 左右切条 | 10秒全屏":"");
    } else snprintf(buf,sizeof(buf),"等待 PC 同步 | Ctrl+Alt+J 发送文字 | Ctrl+Alt+空格 输入");
    // Avoid redrawing a static footer on every 10 ms loop.
    if(strcmp(lv_label_get_text(footer),buf)) lv_label_set_text(footer,buf);
}
static void update_gesture_hint(void) {
    if(!gesture_hint) return;
    const char *value=NULL; uint32_t color=0x25354B;
    if(reader.focused && !menu_open && !news_alert_visible) {
        if(reader.down && reader.hold_armed && reader.axis!=2) {
            if(reader.gesture_dx<=-READING_ACTION_SWIPE) { value="松手显示删除确认"; color=0xA13A44; }
            else if(reader.gesture_dx>=READING_ACTION_SWIPE) { value="松手发送到电脑"; color=0x137A66; }
            else value="左滑删除 | 右滑发送到电脑 | 松手取消";
        } else if(!reader.down && *current_toast) value=current_toast;
    }
    hide(gesture_hint,!value);
    if(value) {
        if(strcmp(lv_label_get_text(gesture_hint),value)) lv_label_set_text(gesture_hint,value);
        if(gesture_hint_color!=color) { lv_obj_set_style_bg_color(gesture_hint,lv_color_hex(color),0); gesture_hint_color=color; }
    }
}
void touch_ui_tick(void) {
    lv_timer_handler();
    if(touch_board_touched()) touch_ui_activity();
    bool full=reading_tick(&reader,lv_tick_get(),!menu_open && !news_alert_visible && touch_board_awake() && !touch_board_touched());
    if(full) { apply_geometry(); reflow(content,&content_settings); ESP_LOGI("touch_ui","AUTO_FULLSCREEN"); }
    render_window(false);
}
bool touch_ui_motion_allowed(void) {
    return !menu_open && !news_alert_visible && !reader.down && !touch_board_touched() && touch_board_awake();
}
bool touch_ui_fullscreen(void) { return reader.focused; }
void touch_ui_render(const touch_view_t *v) {
    if(strcmp(identity,v->identity?v->identity:"")) {
        reading_cancel(&reader,lv_tick_get());
        snprintf(identity,sizeof(identity),"%s",v->identity?v->identity:""); dismiss_modal();
        if(pending_page==INT_MIN) pending_page=0;
        reader.direction=1; touch_ui_activity();
    }
    view_mode=v->mode; has_content=v->count>0;
    current_index=v->index; current_count=v->count; current_unread=v->unread; current_pinned=v->pinned;
    snprintf(source_name,sizeof(source_name),"%s",v->source?v->source:"");
    snprintf(current_toast,sizeof(current_toast),"%s",v->toast?v->toast:"");
    theme_dark=!!v->display.dark;
    background=theme_dark?0x111C2D:0xF3F6FB; foreground=theme_dark?0xEFF4FC:0x172A40;
    lv_obj_set_style_bg_color(lv_scr_act(),lv_color_hex(background),0);
    lv_obj_set_style_bg_color(body,lv_color_hex(background),0);
    lv_obj_set_style_text_color(text,lv_color_hex(foreground),0);
    lv_obj_set_style_text_color(clock_text,lv_color_hex(foreground),0);
    lv_obj_set_style_text_color(footer,lv_color_hex(theme_dark?0xA8BEDA:0x49637E),0);
    for(int i=0;i<2;i++) {
        lv_obj_set_style_bg_color(tabs[i],lv_color_hex(i==v->mode?0x246BDD:0x33465E),0);
    }
    apply_geometry();
    lv_label_set_text(clock_text,v->time);
    char buf[256];
    if(v->battery>=0) snprintf(buf,sizeof(buf),"BLE %s   %d%%",v->connected?"ON":"OFF",v->battery);
    else snprintf(buf,sizeof(buf),"BLE %s   USB",v->connected?"ON":"OFF");
    lv_label_set_text(connection_text,buf);
    lv_obj_set_style_text_color(connection_text,lv_color_hex(v->connected?0x259D8D:0x8998AC),0);
    lv_obj_set_style_bg_color(tools[0],lv_color_hex(v->pinned?0x087F73:0x25354B),0);
    lv_label_set_text(lv_obj_get_child(tools[0],0),v->pinned?"已置顶":"置顶");
    touch_ui_text(v->text,v->page,&v->display,true);
    update_news_alert(v->news_alert,v->news_alert_count);
    update_gesture_hint();
    update_footer();
}
void touch_ui_state(cJSON *reply) {
    cJSON *ui=cJSON_AddObjectToObject(reply,"touch");
    cJSON_AddNumberToObject(ui,"font_fallback_glyphs",touch_font_fallback_glyphs());
    cJSON_AddNumberToObject(ui,"font_missing_renders",touch_font_missing_renders());
    cJSON_AddNumberToObject(ui,"font_missing_last",touch_font_missing_last());
    cJSON_AddBoolToObject(ui,"focus",reader.focused); cJSON_AddBoolToObject(ui,"menu",menu_open);
    cJSON_AddBoolToObject(ui,"delete_confirmation",confirming);
    cJSON_AddBoolToObject(ui,"news_alert_visible",news_alert_visible);
    cJSON_AddBoolToObject(ui,"screen_awake",touch_board_awake());
    cJSON_AddNumberToObject(ui,"scroll_y",reading_y(&reader));
    cJSON_AddNumberToObject(ui,"scroll_max",reader.maximum);
    cJSON_AddNumberToObject(ui,"viewport_height",viewport_height());
    cJSON_AddNumberToObject(ui,"layout_width",text_width());
    cJSON_AddNumberToObject(ui,"display_viewport_height",reader.focused?156:105);
    cJSON_AddNumberToObject(ui,"text_zoom",text_zoom());
    cJSON_AddNumberToObject(ui,"effective_font_size",content_settings.font_size*text_zoom()/256.0);
    cJSON_AddNumberToObject(ui,"line_count",line_count);
    uint32_t wraps=2166136261U;
    for(int i=0;i<line_count;i++) wraps=(wraps^line_offsets[i])*16777619U;
    cJSON_AddNumberToObject(ui,"line_breaks_hash",wraps);
    cJSON_AddNumberToObject(ui,"content_height",(line_count-1)*line_height()+content_settings.font_size);
    cJSON_AddBoolToObject(ui,"auto_scroll",reader.autoplay);
    cJSON_AddNumberToObject(ui,"auto_direction",reader.direction);
    cJSON_AddNumberToObject(ui,"idle_ms",lv_tick_get()-reader.input_at);
    cJSON_AddNumberToObject(ui,"auto_focus_after_ms",READING_IDLE_MS);
    cJSON_AddNumberToObject(ui,"auto_scroll_px_s",READING_SPEED_PX_S);
    cJSON_AddNumberToObject(ui,"start_hold_ms",READING_START_MS);
    cJSON_AddNumberToObject(ui,"bottom_hold_ms",READING_BOTTOM_MS);
    int32_t remaining=(int32_t)(reader.hold_until-lv_tick_get());
    cJSON_AddNumberToObject(ui,"hold_remaining_ms",remaining>0?remaining:0);
    cJSON_AddStringToObject(ui,"playback","forward-loop");
    cJSON_AddBoolToObject(ui,"restart_pending",reader.restart_pending);
    cJSON_AddBoolToObject(ui,"hold_armed",reader.down && reader.hold_armed);
    cJSON_AddNumberToObject(ui,"hold_action_ms",READING_HOLD_MS);
    cJSON_AddNumberToObject(ui,"action_swipe_px",READING_ACTION_SWIPE);
    cJSON_AddNumberToObject(ui,"double_tap_ms",READING_DOUBLE_MS);
    uint32_t focus_age=lv_tick_get()-reader.focus_at;
    cJSON_AddNumberToObject(ui,"full_guard_remaining_ms",reader.focused && focus_age<READING_FOCUS_GUARD_MS?READING_FOCUS_GUARD_MS-focus_age:0);
}
bool touch_ui_frame(cJSON *reply) {
    lv_refr_now(NULL);
    const uint16_t *pixels=touch_board_frame();
    if(!pixels) return false;
    // RLE: 4 hex digits run length, 4 hex digits native RGB565 color.
    // Cap the response below the PC decoder's 200 KB frame limit.
    char *encoded=malloc(185000); if(!encoded) return false;
    size_t used=0,total=640*172;
    for(size_t i=0;i<total;) {
        uint16_t raw=pixels[i]; size_t end=i+1;
        while(end<total && end-i<65535 && pixels[end]==raw) end++;
        if(used+9>=185000) { free(encoded); return false; }
        uint16_t color=(raw>>8)|(raw<<8);
        used+=snprintf(encoded+used,185000-used,"%04x%04x",(unsigned)(end-i),color); i=end;
    }
    cJSON_AddStringToObject(reply,"format","rgb565-rle");
    cJSON_AddNumberToObject(reply,"width",640); cJSON_AddNumberToObject(reply,"height",172);
    cJSON_AddStringToObject(reply,"pixels",encoded); free(encoded); return true;
}
