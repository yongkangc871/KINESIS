#include <string.h>
#include "protocol.h"

uint16_t wm_crc16(const uint8_t *data, size_t len)
{
    uint16_t crc = 0xFFFF;
    for (size_t i = 0; i < len; i++) {
        crc ^= (uint16_t)data[i] << 8;
        for (int b = 0; b < 8; b++) {
            crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
        }
    }
    return crc;
}

size_t wm_build_frame(uint8_t *out, uint8_t type, uint16_t seq, const uint8_t *payload, uint8_t len)
{
    out[0] = WM_SYNC0;
    out[1] = WM_SYNC1;
    out[2] = type;
    out[3] = len;
    wm_put_u16(&out[4], seq);
    memcpy(&out[6], payload, len);
    uint16_t crc = wm_crc16(&out[2], 4 + (size_t)len);
    wm_put_u16(&out[6 + len], crc);
    return 6 + (size_t)len + 2;
}
