// Minimal SSD1306 128x64 I2C OLED driver: framebuffer, 5x7 text (21 x 8 characters), bars.
#pragma once

#include <stdbool.h>
#include <stdint.h>

#define OLED_W 128
#define OLED_H 64
#define OLED_COLS 21   // 6 px per character
#define OLED_ROWS 8    // 8 px per row (one SSD1306 page)

// Returns false if no display answers at the configured address.
bool oled_init(int sda_gpio, int scl_gpio, uint8_t addr);

void oled_clear(void);
void oled_text(int col, int row, const char *s);
// Horizontal bar on a text row, filled to frac (0..1), with a tick mark at marker (0..1, <0 = none).
void oled_bar(int row, float frac, float marker);
// Sends the framebuffer to the panel (~25 ms at 400 kHz).
bool oled_flush(void);
// Re-sends the panel setup (no blanking), recovering a panel that glitched or reset on its own.
bool oled_refresh_config(void);
