#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "driver/gpio.h"
#include "sdkconfig.h"
#include "buzzer.h"

#define GAP_MS 100   // silence after every pattern, so back-to-back patterns stay distinct

static QueueHandle_t s_queue;

static void set_on(int on)
{
#if CONFIG_WM_BUZZER_ACTIVE_HIGH
    int level = on;
#else
    int level = !on;
#endif
    gpio_set_level(CONFIG_WM_BUZZER_GPIO, level);
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
