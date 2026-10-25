#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "sdkconfig.h"
#include "display.h"
#include "oled.h"
#include "sensor_state.h"
#include "host_link.h"
#if CONFIG_WM_LINK_HAS_WIFI
#include "wifi_link.h"
#endif

#if CONFIG_WM_OLED_ENABLE

#define ANGLE_SCALE (180.0f / 32768.0f)
#define GYRO_SCALE (2000.0f / 32768.0f)

// Screen layout (21 x 8 characters):
//   0  BAT 9.1V      PC:ON    (WRIST MON if no battery sense; PC:ON = app streaming)
//   1  Roll   177.0   S3G3    (BNO055 calibration 0-3: system, gyro,
//   2  Pitch   -3.2   A3M0     accelerometer, magnetometer)
//   3  Yaw     40.1
//   4  Gyr   1  -30    0      (deg/s x y z)
//   5  EMG raw 2048
//   6  EMG rms  123
//   7  [#######    |      ]   (rms bar, | = beep threshold; on Wi-Fi shows "IP 192.168.4.1" until the PC connects)
static void draw(void)
{
    char line[OLED_COLS + 1];
    imu_state_t imu;
    emg_state_t emg;
    const bool streaming = host_link_streaming();

    oled_clear();
    const uint16_t mv = sensor_state_get_battery_mv();
    if (mv > 0) {
        // "LOW" below 7.0 V: a 9 V battery is nearly flat there and sags hard under load.
        snprintf(line, sizeof(line), "BAT %d.%dV%s  PC:%s", mv / 1000, (mv % 1000) / 100,
                 mv < 7000 ? " LOW" : "    ", streaming ? "ON" : "--");
    } else {
        snprintf(line, sizeof(line), "WRIST MON     PC:%s", streaming ? "ON" : "--");
    }
    oled_text(0, 0, line);

    if (sensor_state_get_imu(0, &imu)) {
        const uint8_t c = imu.calib;
        snprintf(line, sizeof(line), "Roll  %7.1f  S%dG%d", imu.angle[0] * ANGLE_SCALE,
                 (c >> 6) & 3, (c >> 4) & 3);
        oled_text(0, 1, line);
        snprintf(line, sizeof(line), "Pitch %7.1f  A%dM%d", imu.angle[1] * ANGLE_SCALE,
                 (c >> 2) & 3, c & 3);
        oled_text(0, 2, line);
        snprintf(line, sizeof(line), "Yaw   %7.1f", imu.angle[2] * ANGLE_SCALE);
        oled_text(0, 3, line);
        snprintf(line, sizeof(line), "Gyr %4d %4d %4d", (int)(imu.gyro[0] * GYRO_SCALE),
                 (int)(imu.gyro[1] * GYRO_SCALE), (int)(imu.gyro[2] * GYRO_SCALE));
        oled_text(0, 4, line);
    } else {
        oled_text(0, 2, "IMU: no data");
        oled_text(0, 3, "check BNO055 wiring");
    }

#if CONFIG_WM_IMU2_ENABLE
    // With a forearm IMU, show its pitch at the end of the yaw row.
    if (sensor_state_get_imu(1, &imu)) {
        snprintf(line, sizeof(line), "F%5.1f", imu.angle[1] * ANGLE_SCALE);
        oled_text(15, 3, line);
    }
#endif

    if (sensor_state_get_emg(&emg)) {
        snprintf(line, sizeof(line), "EMG raw %4u", emg.raw);
        oled_text(0, 5, line);
        snprintf(line, sizeof(line), "EMG rms %4d", (int)emg.rms);
        oled_text(0, 6, line);
        oled_bar(7, emg.rms / (float)CONFIG_WM_OLED_EMG_FULL_SCALE,
                 CONFIG_WM_EMG_BEEP_THRESHOLD > 0
                     ? CONFIG_WM_EMG_BEEP_THRESHOLD / (float)CONFIG_WM_OLED_EMG_FULL_SCALE : -1.0f);
    } else {
        oled_text(0, 6, "EMG: no data");
    }

#if CONFIG_WM_LINK_HAS_WIFI
    // Until the PC app connects, show where to connect instead of the EMG bar.
    if (!wifi_link_client_connected()) {
        snprintf(line, sizeof(line), "IP %s", wifi_link_ip());
        oled_text(0, 7, "                     ");
        oled_text(0, 7, line);
    }
#endif
}

static void display_task(void *arg)
{
    const TickType_t period = pdMS_TO_TICKS(1000 / CONFIG_WM_OLED_REFRESH_HZ);
    TickType_t last = xTaskGetTickCount();
    // A supply dip (e.g. Wi-Fi TX bursts) or I2C noise can scramble or freeze the SSD1306;
    // it only gets its setup at boot, so re-send it on any failed frame and every few seconds.
    const int refresh_every = 3 * CONFIG_WM_OLED_REFRESH_HZ;
    int frames = 0;
    bool ok = true;
    for (;;) {
        if (!ok || ++frames >= refresh_every) {
            frames = 0;
            oled_refresh_config();
        }
        draw();
        ok = oled_flush();
        vTaskDelayUntil(&last, period);
    }
}

void display_start(void)
{
    if (!oled_init(CONFIG_WM_OLED_SDA, CONFIG_WM_OLED_SCL, CONFIG_WM_OLED_ADDR)) {
        return;   // no panel fitted: acquisition carries on without it
    }
    oled_text(0, 3, "  Wrist Curl Monitor");
    oled_text(0, 4, "     starting...");
    oled_flush();
    // Low priority on core 0: never competes with EMG sampling (core 1) or the IMU readers.
    xTaskCreatePinnedToCore(display_task, "oled", 4096, NULL, 2, NULL, 0);
}

#else

void display_start(void) {}

#endif
