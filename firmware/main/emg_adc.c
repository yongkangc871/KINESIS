#include <math.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/gptimer.h"
#include "esp_timer.h"
#include "esp_attr.h"
#include "esp_rom_sys.h"
#include "sdkconfig.h"
#include "emg_adc.h"
#include "protocol.h"
#include "host_link.h"
#include "sensor_state.h"
#include "buzzer.h"

#define TICK_RING 16
// Display-only running statistics (the host does the real signal processing).
#define DC_ALPHA (1.0f / 256.0f)   // ~0.25 s running mean
#define RMS_ALPHA (1.0f / 100.0f)  // ~100 ms running power
#define PERIOD_US (1000000 / CONFIG_WM_EMG_RATE_HZ)
#define BEEP_REARM 0.7f            // RMS must fall below 70 % of the threshold before the next beep
#define ADC_MAX_TRIES 50           // x 20 us: up to ~1 ms waiting for the battery read to release ADC1

static adc_oneshot_unit_handle_t s_adc;
static TaskHandle_t s_task;

// The timer ISR stamps each tick on the shared esp_timer clock; the task reads the ADC.
// adc_oneshot_read() is not ISR-safe, so the read happens in a high-priority task.
static volatile int64_t s_tick_us[TICK_RING];
static volatile uint32_t s_tick_head;

static bool IRAM_ATTR on_tick(gptimer_handle_t timer, const gptimer_alarm_event_data_t *ev, void *ctx)
{
    s_tick_us[s_tick_head % TICK_RING] = esp_timer_get_time();
    s_tick_head++;
    BaseType_t woken = pdFALSE;
    vTaskNotifyGiveFromISR(s_task, &woken);
    return woken == pdTRUE;
}

static void emg_task(void *arg)
{
    uint8_t p[7 + 2 * CONFIG_WM_EMG_BLOCK];
    uint32_t tail = 0;
    int n = 0;
    float mean = -1.0f, power = 0.0f;
    int last_raw = 0;
#if CONFIG_WM_EMG_BEEP_THRESHOLD > 0
    bool above = false;
#endif
    for (;;) {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        while (tail != s_tick_head) {
            // The battery monitor (status task, core 0) shares ADC1: a read that collides with
            // it fails instead of waiting, which used to send a 0 sample once a second - a fake
            // 1700-count spike that the host flags as electrode lift-off (SIGNAL_POOR).
            int raw = 0;
            esp_err_t err;
            int tries = 0;
            while ((err = adc_oneshot_read(s_adc, (adc_channel_t)CONFIG_WM_EMG_ADC_CHANNEL, &raw)) != ESP_OK &&
                   ++tries < ADC_MAX_TRIES) {
                esp_rom_delay_us(20);
            }
            if (err == ESP_OK) last_raw = raw;
            else raw = last_raw;               // hold the previous sample rather than send a 0
            if (mean < 0) mean = (float)raw;
            mean += DC_ALPHA * ((float)raw - mean);
            const float d = (float)raw - mean;
            power += RMS_ALPHA * (d * d - power);
            if (n == 0) {
                wm_put_u32(&p[0], (uint32_t)s_tick_us[tail % TICK_RING]);
                wm_put_u16(&p[4], PERIOD_US);
                p[6] = CONFIG_WM_EMG_BLOCK;
            }
            wm_put_u16(&p[7 + 2 * n], (uint16_t)raw);
            tail++;
            if (++n == CONFIG_WM_EMG_BLOCK) {
                host_link_send(WM_PKT_EMG, p, sizeof(p));
                const float rms = sqrtf(power);
                sensor_state_set_emg((uint16_t)raw, mean, rms);
#if CONFIG_WM_EMG_BEEP_THRESHOLD > 0
                // Standalone electrode test: one beep per contraction, only without the PC app.
                if (!above && rms > CONFIG_WM_EMG_BEEP_THRESHOLD) {
                    above = true;
                    if (!host_link_streaming()) buzzer_play(BUZZ_OK);
                } else if (above && rms < BEEP_REARM * CONFIG_WM_EMG_BEEP_THRESHOLD) {
                    above = false;
                }
#endif
                n = 0;
            }
        }
    }
}

void emg_adc_start(adc_oneshot_unit_handle_t adc)
{
    s_adc = adc;
    const adc_oneshot_chan_cfg_t ch = {
        .atten = ADC_ATTEN_DB_12,
        .bitwidth = ADC_BITWIDTH_12,
    };
    ESP_ERROR_CHECK(adc_oneshot_config_channel(s_adc, (adc_channel_t)CONFIG_WM_EMG_ADC_CHANNEL, &ch));

    // Core 1 is reserved for sampling; UART readers run on core 0.
    xTaskCreatePinnedToCore(emg_task, "emg", 4096, NULL, configMAX_PRIORITIES - 2, &s_task, 1);

    gptimer_handle_t timer;
    const gptimer_config_t tcfg = {
        .clk_src = GPTIMER_CLK_SRC_DEFAULT,
        .direction = GPTIMER_COUNT_UP,
        .resolution_hz = 1000000,
    };
    ESP_ERROR_CHECK(gptimer_new_timer(&tcfg, &timer));
    const gptimer_event_callbacks_t cbs = {.on_alarm = on_tick};
    ESP_ERROR_CHECK(gptimer_register_event_callbacks(timer, &cbs, NULL));
    const gptimer_alarm_config_t alarm = {
        .alarm_count = PERIOD_US,
        .reload_count = 0,
        .flags.auto_reload_on_alarm = true,
    };
    ESP_ERROR_CHECK(gptimer_set_alarm_action(timer, &alarm));
    ESP_ERROR_CHECK(gptimer_enable(timer));
    ESP_ERROR_CHECK(gptimer_start(timer));
}
