// Latest sensor values, shared between the acquisition tasks and the OLED display.
#pragma once

#include <stdbool.h>
#include <stdint.h>

typedef struct {
    bool valid;
    int64_t t_us;
    int16_t angle[3];   // JY901 raw units: deg = raw / 32768 * 180
    int16_t gyro[3];    // dps = raw / 32768 * 2000
} imu_state_t;

typedef struct {
    bool valid;
    uint16_t raw;       // last ADC sample (0..4095)
    float mean;         // running mean (DC level)
    float rms;          // running AC RMS over ~100 ms, ADC counts
} emg_state_t;

void sensor_state_set_imu(uint8_t imu_id, const int16_t angle[3], const int16_t gyro[3]);
void sensor_state_set_emg(uint16_t raw, float mean, float rms);

void sensor_state_set_battery_mv(uint16_t mv);
uint16_t sensor_state_get_battery_mv(void);   // 0 = not measured

// Copies the latest values (consistent snapshot).
bool sensor_state_get_imu(uint8_t imu_id, imu_state_t *out);
bool sensor_state_get_emg(emg_state_t *out);
