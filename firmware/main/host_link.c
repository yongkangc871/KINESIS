#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "driver/uart.h"
#include "sdkconfig.h"
#include "host_link.h"
#include "protocol.h"
#if CONFIG_WM_LINK_HAS_WIFI
#include "wifi_link.h"
#endif

#define HOST_UART UART_NUM_0
#define RX_LINE_MAX 32

static SemaphoreHandle_t s_tx_lock;
static uint16_t s_seq;
static volatile bool s_streaming;
static wm_cmd_handler_t s_handler;

void host_link_dispatch_line(const char *line)
{
    if (!s_handler) return;
    if (strcmp(line, "START") == 0) s_handler(WM_CMD_START);
    else if (strcmp(line, "STOP") == 0) s_handler(WM_CMD_STOP);
    else if (strcmp(line, "BEEP_OK") == 0) s_handler(WM_CMD_BEEP_OK);
    else if (strcmp(line, "BEEP_BAD") == 0) s_handler(WM_CMD_BEEP_BAD);
    else if (strcmp(line, "BEEP_LONG") == 0) s_handler(WM_CMD_BEEP_LONG);
}

void host_link_client_lost(void)
{
#if !CONFIG_WM_LINK_HAS_USB
    // Nobody is listening any more; the PC sends START again when it reconnects.
    s_streaming = false;
#endif
}

#if CONFIG_WM_LINK_HAS_USB
// Host commands are ASCII lines ("BEEP_OK\n").
static void uart_rx_task(void *arg)
{
    char line[RX_LINE_MAX];
    size_t n = 0;
    uint8_t buf[64];
    for (;;) {
        int got = uart_read_bytes(HOST_UART, buf, sizeof(buf), pdMS_TO_TICKS(20));
        for (int i = 0; i < got; i++) {
            char c = (char)buf[i];
            if (c == '\n' || c == '\r') {
                if (n > 0) {
                    line[n] = '\0';
                    host_link_dispatch_line(line);
                    n = 0;
                }
            } else if (n < RX_LINE_MAX - 1) {
                line[n++] = c;
            } else {
                n = 0;  // overlong garbage, resynchronise on the next newline
            }
        }
    }
}

static void uart_link_init(void)
{
    const uart_config_t cfg = {
        .baud_rate = CONFIG_WM_HOST_BAUD,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    ESP_ERROR_CHECK(uart_driver_install(HOST_UART, 1024, 8192, 0, NULL, 0));
    ESP_ERROR_CHECK(uart_param_config(HOST_UART, &cfg));
    ESP_ERROR_CHECK(uart_set_pin(HOST_UART, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE,
                                 UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE));
    xTaskCreatePinnedToCore(uart_rx_task, "host_rx", 3072, NULL, 5, NULL, 0);
}
#endif

void host_link_init(wm_cmd_handler_t handler)
{
    s_handler = handler;
    s_tx_lock = xSemaphoreCreateMutex();
#if CONFIG_WM_LINK_HAS_USB
    uart_link_init();
#endif
#if CONFIG_WM_LINK_HAS_WIFI
    wifi_link_start();
#endif
}

void host_link_send(uint8_t type, const uint8_t *payload, uint8_t len)
{
    if (!s_streaming) return;
    uint8_t frame[WM_MAX_FRAME];
    xSemaphoreTake(s_tx_lock, portMAX_DELAY);
    size_t n = wm_build_frame(frame, type, s_seq++, payload, len);
#if CONFIG_WM_LINK_HAS_USB
    uart_write_bytes(HOST_UART, frame, n);
#endif
#if CONFIG_WM_LINK_HAS_WIFI
    wifi_link_write(frame, n);   // non-blocking: queued for the TCP sender task
#endif
    xSemaphoreGive(s_tx_lock);
}

bool host_link_streaming(void) { return s_streaming; }

void host_link_set_streaming(bool on) { s_streaming = on; }
