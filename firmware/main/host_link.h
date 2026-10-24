// Link to the host PC: Wi-Fi (TCP) and/or USB serial (UART0 through the board's USB bridge),
// selected in menuconfig. Both carry the same binary frames and the same command lines.
#pragma once

#include <stdbool.h>
#include <stdint.h>

typedef enum {
    WM_CMD_START,
    WM_CMD_STOP,
    WM_CMD_BEEP_OK,
    WM_CMD_BEEP_BAD,
    WM_CMD_BEEP_LONG,
} wm_cmd_t;

typedef void (*wm_cmd_handler_t)(wm_cmd_t cmd);

void host_link_init(wm_cmd_handler_t handler);

// Thread-safe and never blocks for long: assigns the next sequence number and hands the
// frame to every enabled link. Packets are dropped (not queued) while streaming is stopped.
void host_link_send(uint8_t type, const uint8_t *payload, uint8_t len);

bool host_link_streaming(void);
void host_link_set_streaming(bool on);

// Parses one ASCII command line ("BEEP_OK") received on any link.
void host_link_dispatch_line(const char *line);

// Called by the Wi-Fi link when the PC disconnects.
void host_link_client_lost(void);
