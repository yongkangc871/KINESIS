// Wrist Curl Rehab Monitor - ESP32 acquisition firmware.
//
// Streams sEMG (ADC, hardware-timed) and BNO055 IMU samples to the host over Wi-Fi (TCP)
// and/or USB serial, all timestamped on esp_timer, drives the buzzer on host command, and
// shows live sensor values on the optional SSD1306 OLED.
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_timer.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_adc/adc_cali.h"
#include "esp_adc/adc_cali_scheme.h"
#include "sdkconfig.h"
#include "protocol.h"
#include "host_link.h"
#include "bno055.h"
#include "emg_adc.h"
#include "buzzer.h"
#include "display.h"
#include "sensor_state.h"

#if CONFIG_WM_EMG_OUTPUT_RAW
#define EMG_HINT WM_EMG_HINT_RAW
#elif CONFIG_WM_EMG_OUTPUT_ENVELOPE
#define EMG_HINT WM_EMG_HINT_ENVELOPE
#else
#define EMG_HINT WM_EMG_HINT_UNKNOWN
#endif

static adc_oneshot_unit_handle_t s_adc;

static void on_command(wm_cmd_t cmd)
{
    switch (cmd) {
    case WM_CMD_START: host_link_set_streaming(true); break;
    case WM_CMD_STOP: host_link_set_streaming(false); buzzer_stop_all(); break;
    case WM_CMD_BEEP_OK: buzzer_play(BUZZ_OK); break;
    case WM_CMD_BEEP_BAD: buzzer_play(BUZZ_BAD); break;
    case WM_CMD_BEEP_LONG: buzzer_play(BUZZ_LONG); break;
    }
}

#if CONFIG_WM_BATTERY_ADC_CHANNEL >= 0
static adc_cali_handle_t s_bat_cali;   // NULL if calibration is unavailable

static void battery_init(void)
{
    const adc_oneshot_chan_cfg_t bat = {.atten = ADC_ATTEN_DB_12, .bitwidth = ADC_BITWIDTH_12};
    ESP_ERROR_CHECK(adc_oneshot_config_channel(s_adc, (adc_channel_t)CONFIG_WM_BATTERY_ADC_CHANNEL, &bat));
#if ADC_CALI_SCHEME_LINE_FITTING_SUPPORTED
    // Uses the factory eFuse calibration; the raw ESP32 ADC is quite non-linear near the top.
    adc_cali_line_fitting_config_t cal = {
        .unit_id = ADC_UNIT_1,
        .atten = ADC_ATTEN_DB_12,
        .bitwidth = ADC_BITWIDTH_12,
#if CONFIG_IDF_TARGET_ESP32
        .default_vref = 1100,
#endif
    };
    if (adc_cali_create_scheme_line_fitting(&cal, &s_bat_cali) != ESP_OK) s_bat_cali = NULL;
#endif
}
#endif

static uint16_t read_battery_mv(void)
{
#if CONFIG_WM_BATTERY_ADC_CHANNEL >= 0
    // Average 16 reads to smooth noise from the high-impedance divider.
    int sum = 0;
    for (int i = 0; i < 16; i++) {
        int raw = 0;
        if (adc_oneshot_read(s_adc, (adc_channel_t)CONFIG_WM_BATTERY_ADC_CHANNEL, &raw) != ESP_OK) return 0;
        sum += raw;
    }
    int raw = sum / 16;
    int pin_mv = 0;
    if (s_bat_cali == NULL || adc_cali_raw_to_voltage(s_bat_cali, raw, &pin_mv) != ESP_OK) {
        pin_mv = raw * 3100 / 4095;   // uncalibrated fallback: ~3.1 V full scale at 12 dB
    }
    return (uint16_t)((int64_t)pin_mv * CONFIG_WM_BATTERY_DIVIDER_X100 / 100);
#else
    return 0;
#endif
}

static void status_task(void *arg)
{
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(1000));
        uint8_t p[9];
        wm_put_u32(&p[0], (uint32_t)esp_timer_get_time());
        const uint16_t mv = read_battery_mv();
        sensor_state_set_battery_mv(mv);
        wm_put_u16(&p[4], mv);
        uint8_t flags = 0;
        if (host_link_streaming()) flags |= WM_STATUS_STREAMING;
        if (bno055_alive(0)) flags |= WM_STATUS_IMU_OK;
        if (bno055_alive(1)) flags |= WM_STATUS_IMU2_PRESENT;
        p[6] = flags;
        p[7] = EMG_HINT;
        p[8] = WM_FW_VERSION;
        host_link_send(WM_PKT_STATUS, p, sizeof(p));
    }
}

void app_main(void)
{
    buzzer_init();
    host_link_init(on_command);

    const adc_oneshot_unit_init_cfg_t unit = {.unit_id = ADC_UNIT_1};
    ESP_ERROR_CHECK(adc_oneshot_new_unit(&unit, &s_adc));
#if CONFIG_WM_BATTERY_ADC_CHANNEL >= 0
    battery_init();
#endif

    bno055_start(CONFIG_WM_IMU_SDA, CONFIG_WM_IMU_SCL, CONFIG_WM_IMU_ADDR, 0);
#if CONFIG_WM_IMU2_ENABLE
    bno055_start(CONFIG_WM_IMU_SDA, CONFIG_WM_IMU_SCL, CONFIG_WM_IMU2_ADDR, 1);
#endif
    emg_adc_start(s_adc);
    xTaskCreatePinnedToCore(status_task, "status", 3072, NULL, 3, NULL, 0);
    display_start();
}
