// Binary stream protocol shared with host/wristmon/protocol.py.
//
// Frame:  A5 5A | type u8 | len u8 | seq u16 | payload[len] | crc16 u16
// All multi-byte fields little-endian. CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF)
// over type..payload. seq is one global counter across all packet types, so any gap
// on the host means lost packets.
#pragma once

#include <stdint.h>
#include <stddef.h>

#define WM_SYNC0 0xA5
#define WM_SYNC1 0x5A

#define WM_PKT_EMG 0x01     // t0_us u32 | dt_us u16 | n u8 | n x u16 adc
#define WM_PKT_IMU 0x02     // t_us u32 | imu_id u8 | roll pitch yaw gx gy gz ax ay az (i16, JY901 raw units)
#define WM_PKT_STATUS 0x03  // t_us u32 | battery_mv u16 | flags u8 | emg_hint u8 | fw_version u8

#define WM_FW_VERSION 1

#define WM_STATUS_STREAMING 0x01
#define WM_STATUS_IMU_OK 0x02
#define WM_STATUS_IMU2_PRESENT 0x04

#define WM_EMG_HINT_UNKNOWN 0
#define WM_EMG_HINT_RAW 1
#define WM_EMG_HINT_ENVELOPE 2

#define WM_MAX_PAYLOAD 255
#define WM_MAX_FRAME (6 + WM_MAX_PAYLOAD + 2)

uint16_t wm_crc16(const uint8_t *data, size_t len);

// Builds a complete frame into out (>= WM_MAX_FRAME bytes); returns its length.
size_t wm_build_frame(uint8_t *out, uint8_t type, uint16_t seq, const uint8_t *payload, uint8_t len);

static inline void wm_put_u16(uint8_t *p, uint16_t v) { p[0] = v & 0xFF; p[1] = v >> 8; }
static inline void wm_put_u32(uint8_t *p, uint32_t v)
{
    p[0] = v & 0xFF; p[1] = (v >> 8) & 0xFF; p[2] = (v >> 16) & 0xFF; p[3] = v >> 24;
}
