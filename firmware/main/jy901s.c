#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_timer.h"
#include "sdkconfig.h"
#include "jy901s.h"
#include "protocol.h"
#include "host_link.h"
#include "sensor_state.h"

#define JY_FRAME_LEN 11
#define JY_ACC 0x51
#define JY_GYRO 0x52
#define JY_ANGLE 0x53
#define JY_MAX_IMUS 2

typedef struct {
    uart_port_t uart;
    uint8_t imu_id;
    uint8_t frame[JY_FRAME_LEN];
    int idx;
    int16_t acc[3], gyro[3], angle[3];
    int64_t t_first_us;   // timestamp of the acc frame that opens a sample
    bool have_acc, have_gyro;
} jy_ctx_t;

static jy_ctx_t s_ctx[JY_MAX_IMUS];
static volatile int64_t s_last_sample_us[JY_MAX_IMUS];

// Microseconds per byte on the wire (10 bits per byte).
static int64_t byte_time_us(void) { return 10LL * 1000000LL / CONFIG_WM_IMU_BAUD; }

static void jy_write(uart_port_t uart, uint8_t reg, uint8_t lo, uint8_t hi)
{
    const uint8_t cmd[5] = {0xFF, 0xAA, reg, lo, hi};
    uart_write_bytes(uart, cmd, sizeof(cmd));
    vTaskDelay(pdMS_TO_TICKS(30));
}

static void jy_unlock(uart_port_t uart) { jy_write(uart, 0x69, 0x88, 0xB5); }

static uint8_t baud_code(int baud)
{
    switch (baud) {
    case 9600: return 0x02;
    case 19200: return 0x03;
    case 38400: return 0x04;
    case 57600: return 0x05;
    case 230400: return 0x07;
    default: return 0x06;  // 115200
    }
}

// Sets output content (acc|gyro|angle), output rate and baud, trying both the factory
// 9600 baud and the target baud so it works whether or not the module was configured before.
static void jy_configure(uart_port_t uart)
{
    const int bauds[2] = {9600, CONFIG_WM_IMU_BAUD};
    for (int i = 0; i < 2; i++) {
        uart_set_baudrate(uart, bauds[i]);
        vTaskDelay(pdMS_TO_TICKS(50));
        jy_unlock(uart);
        jy_write(uart, 0x02, 0x0E, 0x00);                        // RSW: ACC | GYRO | ANGLE
        jy_write(uart, 0x03, CONFIG_WM_IMU_RATE_CODE, 0x00);     // RRATE
        jy_write(uart, 0x04, baud_code(CONFIG_WM_IMU_BAUD), 0x00);
    }
    uart_set_baudrate(uart, CONFIG_WM_IMU_BAUD);
    vTaskDelay(pdMS_TO_TICKS(100));
    jy_unlock(uart);
    jy_write(uart, 0x00, 0x00, 0x00);                            // SAVE
}

static void emit_sample(jy_ctx_t *c)
{
    uint8_t p[4 + 1 + 18];
    wm_put_u32(&p[0], (uint32_t)c->t_first_us);
    p[4] = c->imu_id;
    const int16_t v[9] = {c->angle[0], c->angle[1], c->angle[2],
                          c->gyro[0], c->gyro[1], c->gyro[2],
                          c->acc[0], c->acc[1], c->acc[2]};
    for (int i = 0; i < 9; i++) wm_put_u16(&p[5 + 2 * i], (uint16_t)v[i]);
    host_link_send(WM_PKT_IMU, p, sizeof(p));
    sensor_state_set_imu(c->imu_id, c->angle, c->gyro);
    s_last_sample_us[c->imu_id] = esp_timer_get_time();
}

static void handle_frame(jy_ctx_t *c, int64_t t_frame_us)
{
    const uint8_t *f = c->frame;
    uint8_t sum = 0;
    for (int i = 0; i < JY_FRAME_LEN - 1; i++) sum += f[i];
    if (sum != f[JY_FRAME_LEN - 1]) return;

    int16_t v[3];
    for (int i = 0; i < 3; i++) v[i] = (int16_t)(f[2 + 2 * i] | (f[3 + 2 * i] << 8));

    switch (f[1]) {
    case JY_ACC:
        memcpy(c->acc, v, sizeof(v));
        c->t_first_us = t_frame_us;
        c->have_acc = true;
        break;
    case JY_GYRO:
        memcpy(c->gyro, v, sizeof(v));
        if (!c->have_acc) c->t_first_us = t_frame_us;
        c->have_gyro = true;
        break;
    case JY_ANGLE:
        memcpy(c->angle, v, sizeof(v));
        if (!c->have_acc && !c->have_gyro) c->t_first_us = t_frame_us;
        emit_sample(c);
        c->have_acc = c->have_gyro = false;
        break;
    default:
        break;
    }
}

static void jy_task(void *arg)
{
    jy_ctx_t *c = (jy_ctx_t *)arg;
#if CONFIG_WM_IMU_CONFIGURE
    jy_configure(c->uart);
#endif
    uint8_t buf[128];
    const int64_t bt = byte_time_us();
    for (;;) {
        int got = uart_read_bytes(c->uart, buf, sizeof(buf), pdMS_TO_TICKS(5));
        if (got <= 0) continue;
        const int64_t t_read = esp_timer_get_time();
        for (int i = 0; i < got; i++) {
            uint8_t b = buf[i];
            if (c->idx == 0 && b != 0x55) continue;
            c->frame[c->idx++] = b;
            if (c->idx == JY_FRAME_LEN) {
                // Back-date to when this frame's first byte arrived.
                int64_t t_frame = t_read - (int64_t)(got - i + JY_FRAME_LEN - 1) * bt;
                handle_frame(c, t_frame);
                c->idx = 0;
            }
        }
    }
}

void jy901s_start(uart_port_t uart, int rx_gpio, int tx_gpio, uint8_t imu_id)
{
    jy_ctx_t *c = &s_ctx[imu_id];
    memset(c, 0, sizeof(*c));
    c->uart = uart;
    c->imu_id = imu_id;

    const uart_config_t cfg = {
        .baud_rate = CONFIG_WM_IMU_BAUD,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    ESP_ERROR_CHECK(uart_driver_install(uart, 1024, 256, 0, NULL, 0));
    ESP_ERROR_CHECK(uart_param_config(uart, &cfg));
    ESP_ERROR_CHECK(uart_set_pin(uart, tx_gpio, rx_gpio, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE));
    xTaskCreatePinnedToCore(jy_task, imu_id ? "jy901s_fa" : "jy901s", 4096, c, 8, NULL, 0);
}

bool jy901s_alive(uint8_t imu_id)
{
    return imu_id < JY_MAX_IMUS && (esp_timer_get_time() - s_last_sample_us[imu_id]) < 100000;
}
