#include <string.h>
#include "freertos/FreeRTOS.h"
#include "esp_timer.h"
#include "sensor_state.h"

#define MAX_IMUS 2

static portMUX_TYPE s_lock = portMUX_INITIALIZER_UNLOCKED;
static imu_state_t s_imu[MAX_IMUS];
static emg_state_t s_emg;
static volatile uint16_t s_battery_mv;

void sensor_state_set_battery_mv(uint16_t mv) { s_battery_mv = mv; }

uint16_t sensor_state_get_battery_mv(void) { return s_battery_mv; }

void sensor_state_set_imu(uint8_t imu_id, const int16_t angle[3], const int16_t gyro[3])
{
    if (imu_id >= MAX_IMUS) return;
    int64_t now = esp_timer_get_time();
    portENTER_CRITICAL(&s_lock);
    s_imu[imu_id].valid = true;
    s_imu[imu_id].t_us = now;
    memcpy(s_imu[imu_id].angle, angle, sizeof(s_imu[imu_id].angle));
    memcpy(s_imu[imu_id].gyro, gyro, sizeof(s_imu[imu_id].gyro));
    portEXIT_CRITICAL(&s_lock);
}

void sensor_state_set_emg(uint16_t raw, float mean, float rms)
{
    portENTER_CRITICAL(&s_lock);
    s_emg.valid = true;
    s_emg.raw = raw;
    s_emg.mean = mean;
    s_emg.rms = rms;
    portEXIT_CRITICAL(&s_lock);
}

bool sensor_state_get_imu(uint8_t imu_id, imu_state_t *out)
{
    if (imu_id >= MAX_IMUS) return false;
    portENTER_CRITICAL(&s_lock);
    *out = s_imu[imu_id];
    portEXIT_CRITICAL(&s_lock);
    // Stale after 200 ms: sensor unplugged or not configured.
    return out->valid && (esp_timer_get_time() - out->t_us) < 200000;
}

bool sensor_state_get_emg(emg_state_t *out)
{
    portENTER_CRITICAL(&s_lock);
    *out = s_emg;
    portEXIT_CRITICAL(&s_lock);
    return out->valid;
}
