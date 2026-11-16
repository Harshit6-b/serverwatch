// WiFi station, agent discovery (UDP broadcast), polling /api/beacon, sending ACK / FIX.
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include "beacon.h"
#include "cJSON.h"
#include "esp_event.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/task.h"
#include "lwip/sockets.h"
#include "sdkconfig.h"

#define DISCOVERY_PORT 47800
static const char *TAG = "net";

static volatile bool s_wifi_up;
static char s_agent[16];
static int s_port = CONFIG_SW_AGENT_PORT;

// ------------------------------------------------------------------ WiFi
static void on_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        if (s_wifi_up) ESP_LOGW(TAG, "WiFi lost, reconnecting");
        s_wifi_up = false;
        esp_wifi_connect();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *e = data;
        beacon_lock();
        snprintf(g_status.my_ip, sizeof(g_status.my_ip), IPSTR, IP2STR(&e->ip_info.ip));
        beacon_unlock();
        ESP_LOGI(TAG, "WiFi up, beacon IP " IPSTR, IP2STR(&e->ip_info.ip));
        s_wifi_up = true;
    }
}

static void wifi_start(void)
{
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_t *sta = esp_netif_create_default_wifi_sta();
    esp_netif_set_hostname(sta, "serverwatch-beacon");
    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, on_event, NULL, NULL));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP, on_event, NULL, NULL));
    wifi_config_t wc = {0};
    strlcpy((char *)wc.sta.ssid, CONFIG_SW_WIFI_SSID, sizeof(wc.sta.ssid));
    strlcpy((char *)wc.sta.password, CONFIG_SW_WIFI_PASSWORD, sizeof(wc.sta.password));
    wc.sta.threshold.authmode = strlen(CONFIG_SW_WIFI_PASSWORD) ? WIFI_AUTH_WPA2_PSK : WIFI_AUTH_OPEN;
    wc.sta.sae_pwe_h2e = WPA3_SAE_PWE_BOTH;     // phone hotspots often use WPA3
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wc));
    ESP_ERROR_CHECK(esp_wifi_start());
    esp_wifi_set_ps(WIFI_PS_NONE);              // lower latency; the beacon is USB powered
}

// ------------------------------------------------------------------ HTTP
// Returns the HTTP status (or -1) and the body in `out`.
static int http_call(const char *path, bool post, char *out, int outlen)
{
    char url[96];
    snprintf(url, sizeof(url), "http://%s:%d%s", s_agent, s_port, path);
    esp_http_client_config_t cfg = {
        .url = url,
        .method = post ? HTTP_METHOD_POST : HTTP_METHOD_GET,
        .timeout_ms = post ? 8000 : 2000,      // StartService can take a few seconds
        .disable_auto_redirect = true,
    };
    esp_http_client_handle_t c = esp_http_client_init(&cfg);
    if (!c) return -1;
    if (post) {
        esp_http_client_set_header(c, "Content-Type", "application/json");
        esp_http_client_set_header(c, "X-Key", CONFIG_SW_API_KEY);
    }
    int status = -1, n = 0;
    if (esp_http_client_open(c, post ? 2 : 0) == ESP_OK) {
        if (post) esp_http_client_write(c, "{}", 2);
        if (esp_http_client_fetch_headers(c) >= 0) {
            status = esp_http_client_get_status_code(c);
            int r;
            while (n < outlen - 1 && (r = esp_http_client_read(c, out + n, outlen - 1 - n)) > 0) n += r;
        }
    }
    out[n] = 0;
    esp_http_client_close(c);
    esp_http_client_cleanup(c);
    return status;
}

static const char *jstr(const cJSON *o, const char *k, const char *dflt)
{
    const cJSON *v = cJSON_GetObjectItemCaseSensitive(o, k);
    return cJSON_IsString(v) ? v->valuestring : dflt;
}

static bool poll_once(void)
{
    static char body[1024];
    if (http_call("/api/beacon", false, body, sizeof(body)) != 200) return false;
    cJSON *j = cJSON_Parse(body);
    if (!j) return false;

    beacon_lock();
    const cJSON *o = cJSON_GetObjectItem(j, "o");
    g_status.overall = cJSON_IsNumber(o) ? o->valueint : ST_UNREACHABLE;
    g_status.acked = !cJSON_IsFalse(cJSON_GetObjectItem(j, "ack"));
    g_status.fixable = cJSON_IsTrue(cJSON_GetObjectItem(j, "fix"));
    strlcpy(g_status.host, jstr(j, "host", "?"), sizeof(g_status.host));
    strlcpy(g_status.hint, jstr(j, "hint", ""), sizeof(g_status.hint));
    strlcpy(g_status.msg, jstr(j, "msg", ""), sizeof(g_status.msg));
    const cJSON *m = cJSON_GetObjectItem(j, "m");   // [["cpu", 12.3, 0], ...]
    for (int i = 0; i < 4; i++) {
        const cJSON *row = cJSON_GetArrayItem(m, i);
        const cJSON *v = cJSON_GetArrayItem(row, 1), *l = cJSON_GetArrayItem(row, 2);
        g_status.val[i] = cJSON_IsNumber(v) ? (float)v->valuedouble : NAN;
        g_status.lvl[i] = cJSON_IsNumber(l) ? l->valueint : 0;
    }
    g_status.last_ok_us = esp_timer_get_time();
    g_status.net = NET_OK;
    beacon_unlock();
    cJSON_Delete(j);
    return true;
}

static void send_cmd(beacon_cmd_t cmd)
{
    char body[256], note[40];
    int st = http_call(cmd == CMD_ACK ? "/api/ack?from=beacon" : "/api/fix?from=beacon", true, body, sizeof(body));
    cJSON *j = cJSON_Parse(body);
    const char *msg = j ? jstr(j, "msg", NULL) : NULL;
    if (st == 200 && msg) snprintf(note, sizeof(note), "%s", msg);
    else if (st == 403) snprintf(note, sizeof(note), "FIX: wrong key");
    else snprintf(note, sizeof(note), "%s failed (%d)", cmd == CMD_ACK ? "ACK" : "FIX", st);
    ESP_LOGI(TAG, "%s -> %d %s", cmd == CMD_ACK ? "ACK" : "FIX", st, body);
    beacon_note(note);
    cJSON_Delete(j);
}

static void set_net(net_state_t n)
{
    beacon_lock();
    g_status.net = n;
    beacon_unlock();
}

// ------------------------------------------------------------------ task
static void net_task(void *arg)
{
    int sock = -1, fails = 0;
    bool fixed_host = strlen(CONFIG_SW_AGENT_HOST) > 0;
    if (fixed_host) strlcpy(s_agent, CONFIG_SW_AGENT_HOST, sizeof(s_agent));

    for (;;) {
        if (!s_wifi_up) {
            set_net(NET_WIFI_DOWN);
            if (sock >= 0) { close(sock); sock = -1; }
            vTaskDelay(pdMS_TO_TICKS(300));
            continue;
        }
        if (sock < 0) {
            sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
            struct sockaddr_in a = {.sin_family = AF_INET, .sin_port = htons(DISCOVERY_PORT),
                                    .sin_addr.s_addr = htonl(INADDR_ANY)};
            if (sock >= 0 && bind(sock, (struct sockaddr *)&a, sizeof(a)) != 0) { close(sock); sock = -1; }
        }
        // Discovery: the agent broadcasts "SERVERWATCH <port>" every 2 s. Keep listening so
        // the beacon follows the agent if the laptop gets a new IP.
        char buf[48];
        struct sockaddr_in from;
        socklen_t fl = sizeof(from);
        int n;
        while (sock >= 0 && (n = recvfrom(sock, buf, sizeof(buf) - 1, MSG_DONTWAIT,
                                          (struct sockaddr *)&from, &fl)) > 0) {
            buf[n] = 0;
            if (!fixed_host && strncmp(buf, "SERVERWATCH ", 12) == 0) {
                char ip[16];
                inet_ntoa_r(from.sin_addr, ip, sizeof(ip));
                if (strcmp(ip, s_agent) != 0) ESP_LOGI(TAG, "agent found at %s", ip);
                strlcpy(s_agent, ip, sizeof(s_agent));
                s_port = atoi(buf + 12);
                beacon_lock();
                strlcpy(g_status.agent_ip, ip, sizeof(g_status.agent_ip));
                beacon_unlock();
            }
            fl = sizeof(from);
        }
        if (!s_agent[0]) { set_net(NET_SEARCHING); vTaskDelay(pdMS_TO_TICKS(200)); continue; }

        beacon_cmd_t cmd = g_cmd;
        if (cmd != CMD_NONE) { g_cmd = CMD_NONE; send_cmd(cmd); }

        if (poll_once()) {
            if (fails >= CONFIG_SW_FAIL_LIMIT) ESP_LOGI(TAG, "agent answering again");
            fails = 0;
        } else if (++fails == CONFIG_SW_FAIL_LIMIT) {
            ESP_LOGW(TAG, "no reply %d times in a row: SERVER DOWN", fails);
            set_net(NET_DOWN);
        }
        for (int t = 0; t < CONFIG_SW_POLL_MS && g_cmd == CMD_NONE; t += 20) vTaskDelay(pdMS_TO_TICKS(20));
    }
}

void net_start(void)
{
    wifi_start();
    xTaskCreatePinnedToCore(net_task, "net", 6144, NULL, 4, NULL, 0);
}
