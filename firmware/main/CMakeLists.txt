idf_component_register(SRCS "main.c" "battery.c"
    "device_clock.c" "board.cpp" "touch_ui.c" "touch_font.c" "key_input.c" "motion.c"
    INCLUDE_DIRS "."
    REQUIRES u8g2 lvgl__lvgl i2c_bsp lcd_bl_pwm_bsp espressif__esp_io_expander_tca9554
    esp_timer esp_adc driver bt nvs_flash esp_event json
    EMBED_FILES "touch_fallback.bin")
