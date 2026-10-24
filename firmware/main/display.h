// OLED status screen: live IMU angles/gyro and sEMG level, refreshed a few times per second.
#pragma once

// Starts the display task if CONFIG_WM_OLED_ENABLE; does nothing if no panel is found.
void display_start(void);
