// Bosch BNO055 IMU (on-chip sensor fusion) read over I2C.
#pragma once

#include <stdbool.h>
#include <stdint.h>

// Starts a polling task for the BNO055 at addr on the IMU I2C bus (opened on first call, so a
// second IMU on another address shares it). Each sample (orientation, gyro, acc) is sent to the
// host as an IMU packet tagged with imu_id (0 = hand, 1 = forearm), converted to the JY901 raw
// units the protocol uses, so the host needs no changes.
void bno055_start(int sda_gpio, int scl_gpio, uint8_t addr, uint8_t imu_id);

// True if a sample from this imu_id arrived in the last 100 ms.
bool bno055_alive(uint8_t imu_id);
