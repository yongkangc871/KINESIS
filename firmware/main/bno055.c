#include <math.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/i2c_master.h"
#include "esp_timer.h"
#include "sdkconfig.h"
#include "bno055.h"
#include "protocol.h"
#include "host_link.h"
#include "sensor_state.h"

#define BNO_PORT I2C_NUM_1          // the OLED has I2C_NUM_0 on its own pins
#define BNO_TIMEOUT_MS 20
#define BNO_MAX_IMUS 2
#define BNO_CHIP_ID 0xA0
#define BNO_FAILS_BEFORE_RESET 10   // consecutive failed reads before re-initialising

// Page 0 registers.
#define REG_CHIP_ID 0x00
#define REG_PAGE_ID 0x07
#define REG_DATA 0x08               // burst start: acc, mag, gyro, euler, quaternion (to 0x27)
#define REG_CALIB_STAT 0x35
#define REG_UNIT_SEL 0x3B
#define REG_OPR_MODE 0x3D
#define REG_PWR_MODE 0x3E
#define REG_SYS_TRIGGER 0x3F
#define DATA_LEN (0x28 - REG_DATA)
#define OFF_ACC 0
#define OFF_GYRO 12
#define OFF_QUAT 24

#define MODE_CONFIG 0x00
#if CONFIG_WM_IMU_EXT_CRYSTAL
#define SYS_TRIGGER_CLK 0x80        // use the board's 32.768 kHz crystal
#else
#define SYS_TRIGGER_CLK 0x00        // internal oscillator
#endif
#if CONFIG_WM_IMU_FUSION_NDOF
#define MODE_FUSION 0x0C
#else
#define MODE_FUSION 0x08            // IMU: acc + gyro, no magnetometer
#endif

// BNO055 units (UNIT_SEL = 0x01: acc mg, gyro dps x16, Windows orientation) -> JY901 raw units.
#define DEG_TO_JY (32768.0f / 180.0f)
#define GYRO_TO_JY ((32768.0f / 2000.0f) / 16.0f)
#define MG_TO_JY (32768.0f / 16000.0f)
#define RAD_TO_DEG (180.0f / (float)M_PI)

typedef struct {
    i2c_master_dev_handle_t dev;
    uint8_t imu_id;
} bno_ctx_t;

static i2c_master_bus_handle_t s_bus;
static bno_ctx_t s_ctx[BNO_MAX_IMUS];
static volatile int64_t s_last_sample_us[BNO_MAX_IMUS];

static bool write_reg(bno_ctx_t *c, uint8_t reg, uint8_t val)
{
    const uint8_t b[2] = {reg, val};
    return i2c_master_transmit(c->dev, b, sizeof(b), BNO_TIMEOUT_MS) == ESP_OK;
}

static bool read_regs(bno_ctx_t *c, uint8_t reg, uint8_t *out, size_t n)
{
    return i2c_master_transmit_receive(c->dev, &reg, 1, out, n, BNO_TIMEOUT_MS) == ESP_OK;
}

static int16_t le16(const uint8_t *p) { return (int16_t)(p[0] | (p[1] << 8)); }

static int16_t to_i16(float v)
{
    if (v > 32767.0f) return 32767;
    if (v < -32768.0f) return -32768;
    return (int16_t)lrintf(v);
}

// Puts the chip into fusion mode. Returns false if it does not answer.
static bool configure(bno_ctx_t *c)
{
    uint8_t id = 0;
    // The chip needs up to ~850 ms after power-on before it answers.
    for (int i = 0; i < 20; i++) {
        if (read_regs(c, REG_CHIP_ID, &id, 1) && id == BNO_CHIP_ID) break;
        vTaskDelay(pdMS_TO_TICKS(50));
    }
    if (id != BNO_CHIP_ID) return false;

    if (!write_reg(c, REG_OPR_MODE, MODE_CONFIG)) return false;
    vTaskDelay(pdMS_TO_TICKS(25));                                 // any -> config: 19 ms
    bool ok = write_reg(c, REG_PAGE_ID, 0x00)
           && write_reg(c, REG_PWR_MODE, 0x00)                     // normal power
           && write_reg(c, REG_UNIT_SEL, 0x01)                     // acc mg, gyro dps, Windows
           && write_reg(c, REG_SYS_TRIGGER, SYS_TRIGGER_CLK);
    vTaskDelay(pdMS_TO_TICKS(10));
    ok = ok && write_reg(c, REG_OPR_MODE, MODE_FUSION);
    vTaskDelay(pdMS_TO_TICKS(20));                                 // config -> fusion: 7 ms
    return ok;
}

// Converts one burst and sends it. Returns false while fusion has not started yet.
static bool emit_sample(bno_ctx_t *c, const uint8_t *d, int64_t t_us, uint8_t calib)
{
    const float w = le16(&d[OFF_QUAT]) / 16384.0f, x = le16(&d[OFF_QUAT + 2]) / 16384.0f,
                y = le16(&d[OFF_QUAT + 4]) / 16384.0f, z = le16(&d[OFF_QUAT + 6]) / 16384.0f;
    if (w * w + x * x + y * y + z * z < 0.5f) return false;       // all zero until fusion runs

    // Euler angles from the quaternion in the JY901 convention (roll about X +-180,
    // pitch about Y +-90, yaw about Z +-180), so pitch is wrist flexion with X along the hand.
    // The BNO055's own Euler registers swap the roll/pitch names and ranges.
    float sp = 2.0f * (w * y - z * x);
    if (sp > 1.0f) sp = 1.0f;
    if (sp < -1.0f) sp = -1.0f;
    const float roll = atan2f(2.0f * (w * x + y * z), 1.0f - 2.0f * (x * x + y * y)) * RAD_TO_DEG;
    const float pitch = asinf(sp) * RAD_TO_DEG;
    const float yaw = atan2f(2.0f * (w * z + x * y), 1.0f - 2.0f * (y * y + z * z)) * RAD_TO_DEG;

    int16_t angle[3] = {to_i16(roll * DEG_TO_JY), to_i16(pitch * DEG_TO_JY), to_i16(yaw * DEG_TO_JY)};
    int16_t gyro[3], acc[3];
    for (int i = 0; i < 3; i++) {
        gyro[i] = to_i16(le16(&d[OFF_GYRO + 2 * i]) * GYRO_TO_JY);
        acc[i] = to_i16(le16(&d[OFF_ACC + 2 * i]) * MG_TO_JY);
    }

    uint8_t p[4 + 1 + 18];
    wm_put_u32(&p[0], (uint32_t)t_us);
    p[4] = c->imu_id;
    const int16_t v[9] = {angle[0], angle[1], angle[2], gyro[0], gyro[1], gyro[2], acc[0], acc[1], acc[2]};
    for (int i = 0; i < 9; i++) wm_put_u16(&p[5 + 2 * i], (uint16_t)v[i]);
    host_link_send(WM_PKT_IMU, p, sizeof(p));
    sensor_state_set_imu(c->imu_id, angle, gyro, calib);
    s_last_sample_us[c->imu_id] = esp_timer_get_time();
    return true;
}

static void bno_task(void *arg)
{
    bno_ctx_t *c = (bno_ctx_t *)arg;
    const TickType_t period = pdMS_TO_TICKS(CONFIG_WM_IMU_PERIOD_MS);
    uint8_t d[DATA_LEN];
    uint8_t calib = 0;
    int fails = 0, n = 0;

    while (!configure(c)) vTaskDelay(pdMS_TO_TICKS(500));   // not fitted yet: keep trying
    TickType_t last = xTaskGetTickCount();
    for (;;) {
        vTaskDelayUntil(&last, period);
        const int64_t t_us = esp_timer_get_time();
        if (!read_regs(c, REG_DATA, d, sizeof(d))) {
            if (++fails >= BNO_FAILS_BEFORE_RESET) {
                // Loose wire or brown-out: the chip may have reset to config mode.
                i2c_master_bus_reset(s_bus);
                while (!configure(c)) vTaskDelay(pdMS_TO_TICKS(500));
                fails = 0;
                last = xTaskGetTickCount();
            }
            continue;
        }
        fails = 0;
        // Calibration status (sys|gyro|acc|mag, 2 bits each) changes slowly: read it ~2 x per s.
        if (n++ % (500 / CONFIG_WM_IMU_PERIOD_MS) == 0) read_regs(c, REG_CALIB_STAT, &calib, 1);
        emit_sample(c, d, t_us, calib);
    }
}

void bno055_start(int sda_gpio, int scl_gpio, uint8_t addr, uint8_t imu_id)
{
    if (imu_id >= BNO_MAX_IMUS) return;
    if (s_bus == NULL) {
        const i2c_master_bus_config_t bus = {
            .i2c_port = BNO_PORT,
            .sda_io_num = sda_gpio,
            .scl_io_num = scl_gpio,
            .clk_source = I2C_CLK_SRC_DEFAULT,
            .glitch_ignore_cnt = 7,
            .flags.enable_internal_pullup = true,   // breakout boards usually have pull-ups too
        };
        ESP_ERROR_CHECK(i2c_new_master_bus(&bus, &s_bus));
    }
    bno_ctx_t *c = &s_ctx[imu_id];
    c->imu_id = imu_id;
    const i2c_device_config_t dev = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = addr,
        .scl_speed_hz = CONFIG_WM_IMU_I2C_HZ,
        .scl_wait_us = 10000,   // the BNO055 stretches SCL for up to a few ms
    };
    ESP_ERROR_CHECK(i2c_master_bus_add_device(s_bus, &dev, &c->dev));
    xTaskCreatePinnedToCore(bno_task, imu_id ? "bno055_fa" : "bno055", 4096, c, 8, NULL, 0);
}

bool bno055_alive(uint8_t imu_id)
{
    return imu_id < BNO_MAX_IMUS && (esp_timer_get_time() - s_last_sample_us[imu_id]) < 100000;
}
