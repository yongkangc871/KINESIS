// WitMotion JY901S reader (UART, standard 0x55 protocol).
#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "driver/uart.h"

// Starts a reader task. Each complete sample (acc + gyro + angle) is sent to the
// host as an IMU packet tagged with imu_id (0 = hand, 1 = forearm) and the
// esp_timer timestamp of the sample's first byte.
void jy901s_start(uart_port_t uart, int rx_gpio, int tx_gpio, uint8_t imu_id);

// True if a sample from this imu_id arrived in the last 100 ms.
bool jy901s_alive(uint8_t imu_id);
