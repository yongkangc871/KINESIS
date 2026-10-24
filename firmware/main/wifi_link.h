// Wi-Fi transport: the same binary frames as the USB link, over one TCP connection.
//
// - Access-point mode (default): the ESP32 creates network WM_WIFI_SSID at 192.168.4.1.
// - Station mode: the ESP32 joins an existing network.
// In both modes a UDP beacon "WRISTMON <ip> <port>" is broadcast every second so the PC
// app can find the ESP32 without typing an IP address.
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

void wifi_link_start(void);

// Queues a frame for the TCP sender task. Never blocks: if the queue is full (Wi-Fi stalled)
// or no PC is connected, the frame is dropped and the host sees a sequence gap.
void wifi_link_write(const uint8_t *frame, size_t len);

bool wifi_link_client_connected(void);

// "192.168.4.1", or "0.0.0.0" until an address is assigned.
const char *wifi_link_ip(void);
