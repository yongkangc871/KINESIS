idf_component_register(
    SRCS "main.c" "protocol.c" "host_link.c" "wifi_link.c" "jy901s.c" "emg_adc.c" "buzzer.c"
         "sensor_state.c" "oled.c" "display.c"
    INCLUDE_DIRS "."
    REQUIRES driver esp_timer esp_adc esp_wifi esp_netif esp_event nvs_flash lwip
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
