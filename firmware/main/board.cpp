// Board port adapted from Waveshare V2 09_LVGL_V8_Test; Apache-2.0.
// Adapted from Waveshare's V2 09_LVGL_V8_Test (Apache-2.0).
#include <stdio.h>
#include "touch_ui.h"
#include "motion.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/spi_master.h"
#include "esp_timer.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_vendor.h"
#include "esp_lcd_panel_ops.h"
#include "esp_err.h"
#include "esp_log.h"
#include "esp_heap_caps.h"
#include "lvgl.h"
#include "user_config.h"
#include "esp_lcd_axs15231b.h"
#include "i2c_bsp.h"
#include "driver/gpio.h"
#include "esp_io_expander_tca9554.h"

#include "lcd_bl_pwm_bsp.h"


static const char *TAG="touch349_board";
static uint16_t *lvgl_dma_buf, *last_frame;
static SemaphoreHandle_t lvgl_flush_semap;
static esp_io_expander_handle_t io_expander;
static bool screen_awake=true;
static bool finger_down;
static bool release_pending;
static uint32_t release_at;
static lv_point_t last_touch_point;
static uint32_t touch_errors,touch_gaps,contacts,releases;
static bool flipped;
static bool imu_available;
static uint8_t imu_identity[2];
#if (Rotated == USER_DISP_ROT_90)
uint16_t* rotat_ptr = NULL;
#endif


#define LCD_BIT_PER_PIXEL (16)
   


static void example_lvgl_touch_cb(lv_indev_drv_t *drv, lv_indev_data_t *data);



static void example_lvgl_flush_cb(lv_disp_drv_t *drv, const lv_area_t *area, lv_color_t *color_map);

static void example_lcd_pwm_off_early(void);
static void example_lcd_exio_init(void);
static void example_lcd_reset(void);
static void example_lcd_backlight_set(bool enable);


static const axs15231b_lcd_init_cmd_t lcd_init_cmds[] = 
{
    {0x11, (uint8_t []){0x00}, 0, 100},
    {0x29, (uint8_t []){0x00}, 0, 100},
};

static bool example_notify_lvgl_flush_ready(esp_lcd_panel_io_handle_t panel_io, esp_lcd_panel_io_event_data_t *edata, void *user_ctx)
{
    BaseType_t TaskWoken = pdFALSE;
    xSemaphoreGiveFromISR(lvgl_flush_semap,&TaskWoken);
    return TaskWoken == pdTRUE;
}
static void example_increase_lvgl_tick(void *arg)
{
    lv_tick_inc(EXAMPLE_LVGL_TICK_PERIOD_MS);
}

static void example_lcd_pwm_off_early(void)
{
    gpio_config_t gpio_conf = {};
    gpio_conf.intr_type = GPIO_INTR_DISABLE;
    gpio_conf.mode = GPIO_MODE_OUTPUT;
    gpio_conf.pin_bit_mask = ((uint64_t)1 << EXAMPLE_PIN_NUM_BK_LIGHT);
    gpio_conf.pull_down_en = GPIO_PULLDOWN_ENABLE;
    gpio_conf.pull_up_en = GPIO_PULLUP_DISABLE;
    ESP_ERROR_CHECK(gpio_config(&gpio_conf));
    ESP_ERROR_CHECK(gpio_set_level(EXAMPLE_PIN_NUM_BK_LIGHT, 0));
}

static void example_lcd_exio_init(void)
{
    i2c_master_bus_handle_t tca9554_i2c_bus = NULL;
    ESP_ERROR_CHECK(i2c_master_get_bus_handle(0, &tca9554_i2c_bus));
    ESP_ERROR_CHECK(esp_io_expander_new_i2c_tca9554(tca9554_i2c_bus, ESP_IO_EXPANDER_I2C_TCA9554_ADDRESS_000, &io_expander));
    ESP_ERROR_CHECK(esp_io_expander_set_dir(io_expander, EXAMPLE_EXIO_PIN_TOUCH_INT, IO_EXPANDER_INPUT));
    ESP_ERROR_CHECK(esp_io_expander_set_dir(io_expander, EXAMPLE_EXIO_PIN_BL_EN | EXAMPLE_EXIO_PIN_LCD_RST | EXAMPLE_EXIO_PIN_SYS_EN, IO_EXPANDER_OUTPUT));
    ESP_ERROR_CHECK(esp_io_expander_set_level(io_expander, EXAMPLE_EXIO_PIN_SYS_EN, 1));
    ESP_ERROR_CHECK(esp_io_expander_set_level(io_expander, EXAMPLE_EXIO_PIN_BL_EN, 0));
    ESP_ERROR_CHECK(esp_io_expander_set_level(io_expander, EXAMPLE_EXIO_PIN_LCD_RST, 1));
}

static void example_lcd_reset(void)
{
    ESP_ERROR_CHECK(esp_io_expander_set_level(io_expander, EXAMPLE_EXIO_PIN_LCD_RST, 1));
    vTaskDelay(pdMS_TO_TICKS(30));
    ESP_ERROR_CHECK(esp_io_expander_set_level(io_expander, EXAMPLE_EXIO_PIN_LCD_RST, 0));
    vTaskDelay(pdMS_TO_TICKS(250));
    ESP_ERROR_CHECK(esp_io_expander_set_level(io_expander, EXAMPLE_EXIO_PIN_LCD_RST, 1));
    vTaskDelay(pdMS_TO_TICKS(30));
}

static void example_lcd_backlight_set(bool enable)
{
    ESP_ERROR_CHECK(esp_io_expander_set_level(io_expander, EXAMPLE_EXIO_PIN_BL_EN, enable ? 1 : 0));
}

extern "C" void touch_board_init(void)
{
    example_lcd_pwm_off_early();
#if (Rotated == USER_DISP_ROT_90)
    rotat_ptr = (uint16_t*)heap_caps_malloc(EXAMPLE_LCD_H_RES * EXAMPLE_LCD_V_RES * sizeof(uint16_t), MALLOC_CAP_SPIRAM);
    assert(rotat_ptr);
#endif
    lvgl_flush_semap = xSemaphoreCreateBinary();
    i2c_master_Init();
    example_lcd_exio_init();
    uint8_t identity_reg=0;
    imu_available=i2c_master_transmit_receive(imu_dev_handle,&identity_reg,1,&imu_identity[0],1,50)==ESP_OK && imu_identity[0]==0x05;
    identity_reg=1;
    if(imu_available) i2c_master_transmit_receive(imu_dev_handle,&identity_reg,1,&imu_identity[1],1,50);
    ESP_LOGI(TAG,"IMU available=%d WHO_AM_I=0x%02x revision=0x%02x",imu_available,imu_identity[0],imu_identity[1]);
    if(imu_available) touch_motion_init();
    gpio_config_t power_pin={};
    power_pin.pin_bit_mask=1ULL<<16; power_pin.mode=GPIO_MODE_INPUT;
    power_pin.pull_up_en=GPIO_PULLUP_ENABLE;
    ESP_ERROR_CHECK(gpio_config(&power_pin));
    static lv_disp_draw_buf_t disp_buf; // contains internal graphic buffer(s) called draw buffer(s)
    static lv_disp_drv_t disp_drv;      // contains callback functions

    ESP_LOGI(TAG, "Initialize QSPI bus");
    spi_bus_config_t buscfg = {};
        buscfg.data0_io_num = EXAMPLE_PIN_NUM_LCD_DATA0;
        buscfg.data1_io_num = EXAMPLE_PIN_NUM_LCD_DATA1;
        buscfg.sclk_io_num = EXAMPLE_PIN_NUM_LCD_PCLK;
        buscfg.data2_io_num = EXAMPLE_PIN_NUM_LCD_DATA2;
        buscfg.data3_io_num = EXAMPLE_PIN_NUM_LCD_DATA3;
        buscfg.max_transfer_sz = LVGL_DMA_BUFF_LEN;
    ESP_ERROR_CHECK(spi_bus_initialize(LCD_HOST, &buscfg, SPI_DMA_CH_AUTO));

    ESP_LOGI(TAG, "Install panel IO");
	    esp_lcd_panel_io_handle_t panel_io = NULL;
        esp_lcd_panel_handle_t panel = NULL;
    
    esp_lcd_panel_io_spi_config_t io_config = {};
		io_config.cs_gpio_num = EXAMPLE_PIN_NUM_LCD_CS;                 
        io_config.dc_gpio_num = -1;          
        io_config.spi_mode = 3;              
        io_config.pclk_hz = 40 * 1000 * 1000;
        io_config.trans_queue_depth = 10;    
        io_config.on_color_trans_done = example_notify_lvgl_flush_ready; 
        //io_config.user_ctx = &disp_drv,         
        io_config.lcd_cmd_bits = 32;         
        io_config.lcd_param_bits = 8;        
        io_config.flags.quad_mode = true;                         
	ESP_ERROR_CHECK(esp_lcd_new_panel_io_spi(LCD_HOST, &io_config, &panel_io));
    
	axs15231b_vendor_config_t vendor_config = {};
        vendor_config.flags.use_qspi_interface = 1;
        vendor_config.init_cmds = lcd_init_cmds;
        vendor_config.init_cmds_size = sizeof(lcd_init_cmds) / sizeof(lcd_init_cmds[0]);
    
    esp_lcd_panel_dev_config_t panel_config = {};
        panel_config.reset_gpio_num = -1;
        panel_config.rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB;
        panel_config.bits_per_pixel = LCD_BIT_PER_PIXEL;
        panel_config.vendor_config = &vendor_config;
    
    ESP_LOGI(TAG, "Install panel driver");
    ESP_ERROR_CHECK(esp_lcd_new_panel_axs15231b(panel_io, &panel_config, &panel));
    
	example_lcd_reset();
    ESP_ERROR_CHECK(esp_lcd_panel_init(panel));
    lcd_bl_pwm_bsp_init(LCD_PWM_MODE_255);
    example_lcd_backlight_set(true);

  	lv_init();
  	lvgl_dma_buf = (uint16_t *)heap_caps_malloc(LVGL_DMA_BUFF_LEN , MALLOC_CAP_DMA);
  	assert(lvgl_dma_buf);
  	lv_color_t *buffer_1 = (lv_color_t *)heap_caps_malloc(LVGL_SPIRAM_BUFF_LEN , MALLOC_CAP_SPIRAM);
    lv_color_t *buffer_2 = (lv_color_t *)heap_caps_malloc(LVGL_SPIRAM_BUFF_LEN , MALLOC_CAP_SPIRAM);
  	assert(buffer_1);
    assert(buffer_2);
  	lv_disp_draw_buf_init(&disp_buf, buffer_1, buffer_2, EXAMPLE_LCD_H_RES * EXAMPLE_LCD_V_RES);

  	ESP_LOGI(TAG, "Register display driver to LVGL");
  	lv_disp_drv_init(&disp_drv);
  	disp_drv.hor_res = EXAMPLE_LCD_H_RES;
  	disp_drv.ver_res = EXAMPLE_LCD_V_RES;
  	disp_drv.flush_cb = example_lvgl_flush_cb;
  	disp_drv.draw_buf = &disp_buf;
  	disp_drv.full_refresh = 1;          //full_refresh must be 1
  	disp_drv.user_data = panel;
  	lv_disp_drv_register(&disp_drv);
	
  	ESP_LOGI(TAG, "Install LVGL tick timer");
  	esp_timer_create_args_t lvgl_tick_timer_args = {};
  	    lvgl_tick_timer_args.callback = &example_increase_lvgl_tick;
  	    lvgl_tick_timer_args.name = "lvgl_tick";
  	esp_timer_handle_t lvgl_tick_timer = NULL;
  	ESP_ERROR_CHECK(esp_timer_create(&lvgl_tick_timer_args, &lvgl_tick_timer));
  	ESP_ERROR_CHECK(esp_timer_start_periodic(lvgl_tick_timer,EXAMPLE_LVGL_TICK_PERIOD_MS * 1000));

  	static lv_indev_drv_t indev_drv;    // Input device driver (Touch)
  	lv_indev_drv_init(&indev_drv);
  	indev_drv.type = LV_INDEV_TYPE_POINTER;
  	indev_drv.read_cb = example_lvgl_touch_cb;
  	lv_indev_drv_register(&indev_drv);

    touch_ui_init();
    last_frame=(uint16_t*)heap_caps_calloc(EXAMPLE_LCD_H_RES*EXAMPLE_LCD_V_RES,2,MALLOC_CAP_SPIRAM);
    assert(last_frame);
}
static void example_lvgl_flush_cb(lv_disp_drv_t *drv, const lv_area_t *area, lv_color_t *color_map)
{
    memcpy(last_frame,color_map,EXAMPLE_LCD_H_RES*EXAMPLE_LCD_V_RES*2);
#if (Rotated == USER_DISP_ROT_90)
    uint32_t index = 0;
    uint16_t *data_ptr = (uint16_t *)color_map;
    for (uint16_t j = 0; j < EXAMPLE_LCD_H_RES; j++)
    {
        for (uint16_t i = 0; i < EXAMPLE_LCD_V_RES; i++)
        {
            int source=EXAMPLE_LCD_H_RES * (EXAMPLE_LCD_V_RES - i - 1) + j;
            rotat_ptr[index++] = data_ptr[flipped?EXAMPLE_LCD_H_RES*EXAMPLE_LCD_V_RES-1-source:source];
        }
    }
#endif
    esp_lcd_panel_handle_t panel_handle = (esp_lcd_panel_handle_t) drv->user_data;
    const int flush_coun = (LVGL_SPIRAM_BUFF_LEN / LVGL_DMA_BUFF_LEN);
    const int offgap = (LCD_NOROT_VRES / flush_coun);
    const int dmalen = (LVGL_DMA_BUFF_LEN / 2);
    int offsetx1 = 0;
    int offsety1 = 0;
    int offsetx2 = LCD_NOROT_HRES;
    int offsety2 = offgap;

#if (Rotated == USER_DISP_ROT_90)
    uint16_t *map = (uint16_t *)rotat_ptr;
#else
    uint16_t *map = (uint16_t *)color_map;
#endif

    xSemaphoreGive(lvgl_flush_semap);
    for(int i = 0; i<flush_coun; i++)
    {
        xSemaphoreTake(lvgl_flush_semap,portMAX_DELAY);
        memcpy(lvgl_dma_buf,map,LVGL_DMA_BUFF_LEN);
        ESP_ERROR_CHECK(esp_lcd_panel_draw_bitmap(panel_handle, offsetx1, offsety1, offsetx2, offsety2, lvgl_dma_buf));
        offsety1 += offgap;
        offsety2 += offgap;
        map += dmalen;
    }
    xSemaphoreTake(lvgl_flush_semap,portMAX_DELAY);
    lv_disp_flush_ready(drv);
}
static void example_lvgl_touch_cb(lv_indev_drv_t *drv, lv_indev_data_t *data)
{
    if(!screen_awake) { finger_down=release_pending=false; data->state=LV_INDEV_STATE_REL; return; }
    //static uint8_t read_touchpad_cmd[8] = {0xb5, 0xab, 0xa5, 0x5a, 0x0, 0x0, 0x0, 0x8};
    uint8_t read_touchpad_cmd[11] = {0xb5, 0xab, 0xa5, 0x5a, 0x0, 0x0, 0x0, 0x0e,0x0, 0x0, 0x0};
    uint8_t buff[32] = {0};
    memset(buff,0,32);
    esp_err_t result=i2c_master_transmit_receive(disp_touch_dev_handle, read_touchpad_cmd, 11, buff, 32, 30);
    if(result!=ESP_OK) touch_errors++;
    uint16_t pointX;
    uint16_t pointY;
    pointX = (((uint16_t)buff[2] & 0x0f) << 8) | (uint16_t)buff[3];
    pointY = (((uint16_t)buff[4] & 0x0f) << 8) | (uint16_t)buff[5];
    //ESP_LOGI("Touch","%d,%d",buff[0],buff[1]);
    if (result==ESP_OK && buff[1]>0 && buff[1]<5)
    {
        if(!finger_down) contacts++;
        if(release_pending) touch_gaps++;
        release_pending=false;
        finger_down=true;
        data->state = LV_INDEV_STATE_PR;
#if (Rotated == USER_DISP_ROT_NONO)
        if(pointX > EXAMPLE_LCD_V_RES) pointX = EXAMPLE_LCD_V_RES;
        if(pointY > EXAMPLE_LCD_H_RES) pointY = EXAMPLE_LCD_H_RES;
        data->point.x = pointY;
        data->point.y = (EXAMPLE_LCD_V_RES-pointX);
#else
        if(pointX >= EXAMPLE_LCD_H_RES) pointX = EXAMPLE_LCD_H_RES-1;
        if(pointY >= EXAMPLE_LCD_V_RES) pointY = EXAMPLE_LCD_V_RES-1;
        data->point.x = (EXAMPLE_LCD_H_RES - 1 - pointX);
        data->point.y = (EXAMPLE_LCD_V_RES - 1 - pointY);
#endif
        if(flipped) { data->point.x=EXAMPLE_LCD_H_RES-1-data->point.x; data->point.y=EXAMPLE_LCD_V_RES-1-data->point.y; }
        last_touch_point=data->point;
    }
    else 
    {
        uint32_t now=(uint32_t)(esp_timer_get_time()/1000);
        if(finger_down) {
            if(!release_pending) { release_pending=true; release_at=now; }
            // Bridge brief missing-contact/error packets while a finger is
            // held; use a shorter confirmation outside fullscreen for taps.
            uint32_t confirm=touch_ui_fullscreen()?70:20;
            if(now-release_at<confirm) {
                data->state=LV_INDEV_STATE_PR; data->point=last_touch_point; return;
            }
            releases++;
        }
        finger_down=release_pending=false; data->point=last_touch_point;
        data->state=LV_INDEV_STATE_REL;
    }
}


extern "C" const uint16_t *touch_board_frame(void) { return last_frame; }
extern "C" void touch_board_power(bool enabled) {
    if(screen_awake==enabled) return;
    if(enabled) ESP_ERROR_CHECK(esp_io_expander_set_level(io_expander,EXAMPLE_EXIO_PIN_SYS_EN,1));
    example_lcd_backlight_set(enabled);
    if(!enabled) ESP_ERROR_CHECK(esp_io_expander_set_level(io_expander,EXAMPLE_EXIO_PIN_SYS_EN,0));
    screen_awake=enabled;
    finger_down=release_pending=false;
    ESP_LOGI(TAG,"POWER %s; SYS_EN=%d BL_EN=%d",enabled?"awake":"off",enabled,enabled);
}
extern "C" bool touch_board_awake(void) { return screen_awake; }
extern "C" bool touch_board_touched(void) { return finger_down; }
extern "C" void touch_board_flipped(bool value) {
    if(flipped==value) return;
    flipped=value; finger_down=release_pending=false; lv_obj_invalidate(lv_scr_act());
}
extern "C" void touch_board_state(cJSON *reply) {
    cJSON *touch=cJSON_AddObjectToObject(reply,"touch_input");
    cJSON_AddNumberToObject(touch,"errors",touch_errors); cJSON_AddNumberToObject(touch,"gaps_filtered",touch_gaps);
    cJSON_AddNumberToObject(touch,"contacts",contacts); cJSON_AddNumberToObject(touch,"releases",releases);
    cJSON_AddBoolToObject(touch,"display_flipped",flipped);
    cJSON *imu=cJSON_AddObjectToObject(reply,"imu");
    cJSON_AddBoolToObject(imu,"available",imu_available);
    cJSON_AddNumberToObject(imu,"who_am_i",imu_identity[0]);
    cJSON_AddNumberToObject(imu,"revision",imu_identity[1]);
    cJSON_AddStringToObject(imu,"model","QMI8658");
    touch_motion_state(imu);
}
