// Active buzzer driven by a GPIO. Patterns are queued and played one at a time,
// so beeps never overlap.
#pragma once

typedef enum {
    BUZZ_OK,     // 1 x 80 ms
    BUZZ_BAD,    // 3 x 80 ms
    BUZZ_LONG,   // 1 x 1200 ms
} buzz_pattern_t;

void buzzer_init(void);
void buzzer_play(buzz_pattern_t pattern);
void buzzer_stop_all(void);
