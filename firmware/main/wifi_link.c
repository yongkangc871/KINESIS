#include "sdkconfig.h"
#if CONFIG_WM_LINK_HAS_WIFI

#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/stream_buffer.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "nvs_flash.h"
#include "lwip/sockets.h"
#include "lwip/netdb.h"
#include "sdkconfig.h"
#include "wifi_link.h"
#include "host_link.h"

#define TX_BUFFER_BYTES 16384   // ~1.5 s of stream; a longer Wi-Fi stall drops packets instead
#define RX_LINE_MAX 32

static StreamBufferHandle_t s_tx;
static volatile int s_client = -1;
static char s_ip[16] = "0.0.0.0";

// ------------------------------------------------------------------ Wi-Fi bring-up

#if CONFIG_WM_WIFI_STA
static void on_wifi_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (base == WIFI_EVENT && (id == WIFI_EVENT_STA_START || id == WIFI_EVENT_STA_DISCONNECTED)) {
        strcpy(s_ip, "0.0.0.0");
        esp_wifi_connect();                     // keep retrying until the network is back
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        const ip_event_got_ip_t *ev = (const ip_event_got_ip_t *)data;
        snprintf(s_ip, sizeof(s_ip), IPSTR, IP2STR(&ev->ip_info.ip));
    }
}
#endif

static void wifi_init(void)
{
    esp_err_t err = nvs_flash_init();           // the Wi-Fi driver stores calibration in NVS
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ESP_ERROR_CHECK(nvs_flash_init());
    }
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    const wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init));

    wifi_config_t wc = {0};
#if CONFIG_WM_WIFI_AP
    esp_netif_create_default_wifi_ap();
    strlcpy((char *)wc.ap.ssid, CONFIG_WM_WIFI_SSID, sizeof(wc.ap.ssid));
    strlcpy((char *)wc.ap.password, CONFIG_WM_WIFI_PASSWORD, sizeof(wc.ap.password));
    wc.ap.ssid_len = strlen(CONFIG_WM_WIFI_SSID);
    wc.ap.channel = CONFIG_WM_WIFI_CHANNEL;
    wc.ap.max_connection = 2;
    wc.ap.authmode = strlen(CONFIG_WM_WIFI_PASSWORD) >= 8 ? WIFI_AUTH_WPA2_PSK : WIFI_AUTH_OPEN;
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_AP));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP, &wc));
    strcpy(s_ip, "192.168.4.1");               // the default soft-AP address
#else
    esp_netif_create_default_wifi_sta();
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, on_wifi_event, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, on_wifi_event, NULL));
    strlcpy((char *)wc.sta.ssid, CONFIG_WM_WIFI_SSID, sizeof(wc.sta.ssid));
    strlcpy((char *)wc.sta.password, CONFIG_WM_WIFI_PASSWORD, sizeof(wc.sta.password));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wc));
#endif
    ESP_ERROR_CHECK(esp_wifi_start());
    // Modem power saving adds 100+ ms of latency; the beep must follow a rep within 150 ms.
    esp_wifi_set_ps(WIFI_PS_NONE);
}

// ------------------------------------------------------------------ TCP server

static void close_client(void)
{
    int c = s_client;
    s_client = -1;
    if (c >= 0) {
        shutdown(c, SHUT_RDWR);
        close(c);
        host_link_client_lost();
    }
}

static void handle_rx(int sock, char *line, size_t *n)
{
    char buf[64];
    int got = recv(sock, buf, sizeof(buf), 0);
    if (got <= 0) {
        close_client();
        return;
    }
    for (int i = 0; i < got; i++) {
        char c = buf[i];
        if (c == '\n' || c == '\r') {
            if (*n > 0) {
                line[*n] = '\0';
                host_link_dispatch_line(line);
                *n = 0;
            }
        } else if (*n < RX_LINE_MAX - 1) {
            line[(*n)++] = c;
        } else {
            *n = 0;
        }
    }
}

// One task accepts connections and reads command lines, using select() so a new PC
// connection can replace a stale one (e.g. after the PC dropped off Wi-Fi).
static void server_task(void *arg)
{
    int listener = socket(AF_INET, SOCK_STREAM, IPPROTO_IP);
    int yes = 1;
    setsockopt(listener, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(yes));
    struct sockaddr_in addr = {
        .sin_family = AF_INET,
        .sin_port = htons(CONFIG_WM_TCP_PORT),
        .sin_addr.s_addr = htonl(INADDR_ANY),
    };
    bind(listener, (struct sockaddr *)&addr, sizeof(addr));
    listen(listener, 1);

    char line[RX_LINE_MAX];
    size_t n = 0;
    for (;;) {
        fd_set rd;
        FD_ZERO(&rd);
        FD_SET(listener, &rd);
        int maxfd = listener;
        int client = s_client;
        if (client >= 0) {
            FD_SET(client, &rd);
            if (client > maxfd) maxfd = client;
        }
        struct timeval tv = {.tv_sec = 1, .tv_usec = 0};
        if (select(maxfd + 1, &rd, NULL, NULL, &tv) <= 0) continue;

        if (FD_ISSET(listener, &rd)) {
            int sock = accept(listener, NULL, NULL);
            if (sock >= 0) {
                close_client();                     // newest PC connection wins
                setsockopt(sock, IPPROTO_TCP, TCP_NODELAY, &yes, sizeof(yes));
                setsockopt(sock, SOL_SOCKET, SO_KEEPALIVE, &yes, sizeof(yes));
                struct timeval snd = {.tv_sec = 0, .tv_usec = 200000};
                setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO, &snd, sizeof(snd));
                n = 0;
                s_client = sock;
            }
        }
        client = s_client;
        if (client >= 0 && FD_ISSET(client, &rd)) handle_rx(client, line, &n);
    }
}

// Drains the frame queue into the socket. Runs separately so that a slow or stalled Wi-Fi
// link can never block the sampling tasks that produce the frames.
static void sender_task(void *arg)
{
    static uint8_t buf[1460];
    for (;;) {
        size_t got = xStreamBufferReceive(s_tx, buf, sizeof(buf), portMAX_DELAY);
        int sock = s_client;
        if (sock < 0) continue;                    // nobody listening: discard
        size_t off = 0;
        while (off < got) {
            int sent = send(sock, buf + off, got - off, 0);
            if (sent <= 0) {
                close_client();
                break;
            }
            off += sent;
        }
    }
}

// ------------------------------------------------------------------ discovery beacon

static void beacon_task(void *arg)
{
    int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
    int yes = 1;
    setsockopt(sock, SOL_SOCKET, SO_BROADCAST, &yes, sizeof(yes));
    struct sockaddr_in to = {
        .sin_family = AF_INET,
        .sin_port = htons(CONFIG_WM_BEACON_PORT),
        .sin_addr.s_addr = htonl(INADDR_BROADCAST),
    };
    char msg[48];
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(1000));
        if (strcmp(s_ip, "0.0.0.0") == 0) continue;
        int len = snprintf(msg, sizeof(msg), "WRISTMON %s %d\n", s_ip, CONFIG_WM_TCP_PORT);
        sendto(sock, msg, len, 0, (struct sockaddr *)&to, sizeof(to));
    }
}

// ------------------------------------------------------------------ public

void wifi_link_start(void)
{
    s_tx = xStreamBufferCreate(TX_BUFFER_BYTES, 1);
    wifi_init();
    // Core 0 alongside the Wi-Fi stack; EMG sampling keeps core 1 to itself.
    xTaskCreatePinnedToCore(server_task, "tcp_srv", 4096, NULL, 6, NULL, 0);
    xTaskCreatePinnedToCore(sender_task, "tcp_tx", 4096, NULL, 7, NULL, 0);
    xTaskCreatePinnedToCore(beacon_task, "beacon", 3072, NULL, 2, NULL, 0);
}

void wifi_link_write(const uint8_t *frame, size_t len)
{
    if (s_client < 0) return;
    // Whole frames only: a partial frame would just be thrown away by the host's CRC check.
    if (xStreamBufferSpacesAvailable(s_tx) >= len) {
        xStreamBufferSend(s_tx, frame, len, 0);
    }
}

bool wifi_link_client_connected(void) { return s_client >= 0; }

const char *wifi_link_ip(void) { return s_ip; }

#endif  // CONFIG_WM_LINK_HAS_WIFI
