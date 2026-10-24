// sEMG acquisition: ADC1 one-shot reads paced by a hardware timer (gptimer).
#pragma once

#include "esp_adc/adc_oneshot.h"

// Starts the sampling timer and the reader task. The ADC unit handle is shared
// so the status task can read the battery channel on the same unit.
void emg_adc_start(adc_oneshot_unit_handle_t adc);
