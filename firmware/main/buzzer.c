#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "driver/gpio.h"
#include "driver/ledc.h"
#include "esp_err.h"
#include "sdkconfig.h"
#include "buzzer.h"

#define GAP_MS 100   // silence after every pattern, so back-to-back patterns stay distinct

#if CONFIG_WM_BUZZER_ACTIVE_HIGH
#define IDLE_LEVEL 0                // pin level while silent
#else
#define IDLE_LEVEL 1
#endif

#if CONFIG_WM_BUZZER_PASSIVE
#define BUZ_MODE LEDC_LOW_SPEED_MODE
#define BUZ_CHANNEL LEDC_CHANNEL_0
#define BUZ_RES LEDC_TIMER_10_BIT
#define BUZ_HALF_DUTY (1 << (10 - 1))   // 50 % square wave = loudest tone
#endif

static QueueHandle_t s_queue;

static void set_on(int on)
{
#if CONFIG_WM_BUZZER_PASSIVE
    // A passive buzzer only clicks on DC, so it gets a square wave at CONFIG_WM_BUZZER_FREQ_HZ.
    if (on) {
        ledc_set_duty(BUZ_MODE, BUZ_CHANNEL, BUZ_HALF_DUTY);
        ledc_update_duty(BUZ_MODE, BUZ_CHANNEL);
    } else {
        ledc_stop(BUZ_MODE, BUZ_CHANNEL, IDLE_LEVEL);
    }
#else
    gpio_set_level(CONFIG_WM_BUZZER_GPIO, on ? !IDLE_LEVEL : IDLE_LEVEL);
#endif
}

static void tone(int on_ms, int off_ms)
{
    set_on(1);
    vTaskDelay(pdMS_TO_TICKS(on_ms));
    set_on(0);
    vTaskDelay(pdMS_TO_TICKS(off_ms));
}

static void buzzer_task(void *arg)
{
    buzz_pattern_t p;
    for (;;) {
        if (xQueueReceive(s_queue, &p, portMAX_DELAY) != pdTRUE) continue;
        switch (p) {
        case BUZZ_OK:
            tone(80, 0);
            break;
        case BUZZ_BAD:          // two short beeps
            tone(80, 100);
            tone(80, 0);
            break;
        case BUZZ_LONG:
            tone(1200, 0);
            break;
        }
        vTaskDelay(pdMS_TO_TICKS(GAP_MS));
    }
}

void buzzer_init(void)
{
    const gpio_config_t io = {
        .pin_bit_mask = 1ULL << CONFIG_WM_BUZZER_GPIO,
        .mode = GPIO_MODE_OUTPUT,
    };
    gpio_config(&io);
    gpio_set_level(CONFIG_WM_BUZZER_GPIO, IDLE_LEVEL);
#if CONFIG_WM_BUZZER_PASSIVE
    const ledc_timer_config_t timer = {
        .speed_mode = BUZ_MODE,
        .duty_resolution = BUZ_RES,
        .timer_num = LEDC_TIMER_0,
        .freq_hz = CONFIG_WM_BUZZER_FREQ_HZ,
        .clk_cfg = LEDC_AUTO_CLK,
    };
    ESP_ERROR_CHECK(ledc_timer_config(&timer));
    const ledc_channel_config_t ch = {
        .gpio_num = CONFIG_WM_BUZZER_GPIO,
        .speed_mode = BUZ_MODE,
        .channel = BUZ_CHANNEL,
        .timer_sel = LEDC_TIMER_0,
        .duty = 0,
    };
    ESP_ERROR_CHECK(ledc_channel_config(&ch));
#endif
    set_on(0);
    s_queue = xQueueCreate(4, sizeof(buzz_pattern_t));
    xTaskCreatePinnedToCore(buzzer_task, "buzzer", 2048, NULL, 4, NULL, 0);
}

void buzzer_play(buzz_pattern_t pattern)
{
    // A full queue means the host is flooding us; dropping is better than lagging.
    xQueueSend(s_queue, &pattern, 0);
}

void buzzer_stop_all(void)
{
    xQueueReset(s_queue);
    set_on(0);
}
