idf_component_register(
    SRCS "main.c" "protocol.c" "host_link.c" "wifi_link.c" "bno055.c" "emg_adc.c" "buzzer.c"
         "sensor_state.c" "oled.c" "display.c"
    INCLUDE_DIRS "."
    # Since ESP-IDF 5.3 each peripheral driver is its own component (the old "driver" component
    # no longer pulls them in), so every one used here is listed.
    REQUIRES esp_driver_gpio      # buzzer pin
             esp_driver_ledc      # passive buzzer tone (PWM)
             esp_driver_gptimer   # 1 kHz sEMG sample clock
             esp_driver_i2c       # BNO055 and OLED (i2c_master API)
             esp_driver_uart      # USB serial data link
             esp_adc              # sEMG and battery ADC
             esp_timer            # shared microsecond timestamps
             esp_wifi esp_netif esp_event nvs_flash lwip   # Wi-Fi data link
)

# Never build an open hotspot by accident: access-point mode needs a real password, set in
# sdkconfig.secrets (copy sdkconfig.secrets.example) or with idf.py menuconfig.
string(LENGTH "${CONFIG_WM_WIFI_PASSWORD}" _wm_pw_len)
if(CONFIG_WM_WIFI_AP AND _wm_pw_len LESS 8)
    message(FATAL_ERROR "Wi-Fi hotspot password is not set (needs 8+ characters). "
                        "Copy firmware/sdkconfig.secrets.example to firmware/sdkconfig.secrets, "
                        "put your password in it and delete firmware/sdkconfig, "
                        "or set it with idf.py menuconfig.")
endif()
