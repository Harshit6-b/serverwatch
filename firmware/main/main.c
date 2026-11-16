/*
 * ServerWatch Beacon: a desk device that shows whether a server is healthy, using data the
 * ServerWatch agent reads over WBEM/WMI.
 *
 *   LED     green OK · amber WARNING · red CRITICAL (blinks until acknowledged)
 *           magenta blink = server not answering · blue = the beacon itself has no WiFi
 *   OLED    CPU / MEM / DISK / NET gauges, plus a hint line: the failed service, the process
 *           behind a CPU spike, or "DSK crit in 3m" from the agent's trend prediction
 *   Button  short press = acknowledge, hold 1.5 s = FIX (agent calls Win32_Service.StartService)
 */
#include <math.h>
#include <stdio.h>
#include <string.h>
#include "beacon.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/task.h"
#include "nvs_flash.h"
#include "sdkconfig.h"

#define LONG_PRESS_US (1500 * 1000)
static const char *TAG = "beacon";

beacon_status_t g_status;
SemaphoreHandle_t g_lock;
volatile beacon_cmd_t g_cmd = CMD_NONE;

static bool s_oled;
static bool s_local_ack;     // silences the beacon's own SERVER DOWN alarm

void beacon_lock(void) { xSemaphoreTake(g_lock, portMAX_DELAY); }
void beacon_unlock(void) { xSemaphoreGive(g_lock); }

void beacon_note(const char *text)
{
    beacon_lock();
    strlcpy(g_status.note, text, sizeof(g_status.note));
    g_status.note_until_us = esp_timer_get_time() + 4000000;
    beacon_unlock();
}

static int64_t now_us(void) { return esp_timer_get_time(); }

// ------------------------------------------------------------------ buttons
typedef struct { int pin; bool down, fired; int64_t since; } button_t;
static button_t s_main = {.pin = CONFIG_SW_PIN_BTN}, s_fix = {.pin = CONFIG_SW_PIN_FIX};

static void button_setup(int pin)
{
    if (pin < 0) return;
    gpio_config_t c = {.pin_bit_mask = 1ULL << pin, .mode = GPIO_MODE_INPUT, .pull_up_en = GPIO_PULLUP_ENABLE};
    gpio_config(&c);
}

static bool is_pressed(int pin) { return pin >= 0 && gpio_get_level(pin) == 0; }

static void queue_cmd(beacon_cmd_t c)
{
    g_cmd = c;
    beacon_note(c == CMD_ACK ? "ACK: sending..." : "FIX: sending...");
}

// Returns hold progress 0..1 while the main button is held, -1 otherwise.
static float buttons_poll(net_state_t net)
{
    float progress = -1;
    int64_t t = now_us();
    bool p = is_pressed(s_main.pin);
    if (p && !s_main.down) { s_main.down = true; s_main.since = t; s_main.fired = false; }
    if (p && s_main.down && !s_main.fired) {
        int64_t held = t - s_main.since;
        if (held > 250000) progress = fminf(1.0f, (float)held / LONG_PRESS_US);
        if (held >= LONG_PRESS_US) { s_main.fired = true; queue_cmd(CMD_FIX); }
    }
    if (!p && s_main.down) {
        s_main.down = false;
        if (!s_main.fired && t - s_main.since > 40000) {
            if (net == NET_DOWN) { s_local_ack = true; beacon_note("alarm silenced"); }
            else queue_cmd(CMD_ACK);
        }
    }
    bool f = is_pressed(s_fix.pin);
    if (f && !s_fix.down) { s_fix.down = true; s_fix.since = t; }
    if (!f && s_fix.down) {
        s_fix.down = false;
        if (t - s_fix.since > 40000) queue_cmd(CMD_FIX);
    }
    return progress;
}

// ------------------------------------------------------------------ UI task
static void ui_task(void *arg)
{
    int last_code = -1;
    for (;;) {
        beacon_status_t s;
        beacon_lock();
        s = g_status;
        beacon_unlock();
        int64_t t = now_us();
        if (s.net != NET_DOWN) s_local_ack = false;

        float hold = buttons_poll(s.net);
        bool fast = (t / 125000) & 1, slow = (t / 500000) & 1;
        bool alarm = (s.net == NET_OK && s.overall >= ST_CRITICAL && !s.acked) || (s.net == NET_DOWN && !s_local_ack);

        switch (s.net) {
        case NET_WIFI_DOWN: slow ? led_rgb(0, 0, 255) : led_rgb(0, 0, 0); break;
        case NET_SEARCHING: slow ? led_rgb(0, 160, 255) : led_rgb(0, 0, 0); break;
        case NET_DOWN:      (s_local_ack || fast) ? led_rgb(255, 0, 200) : led_rgb(0, 0, 0); break;
        case NET_OK:
            if (s.overall == ST_OK) led_rgb(0, 255, 0);
            else if (s.overall == ST_WARNING) led_rgb(255, 110, 0);
            else if (s.overall == ST_CRITICAL) (s.acked || fast) ? led_rgb(255, 0, 0) : led_rgb(0, 0, 0);
            else (s.acked || fast) ? led_rgb(255, 0, 200) : led_rgb(0, 0, 0);
            break;
        }

        int code = s.net == NET_OK ? s.overall : 10 + s.net;
        if (code != last_code) { ESP_LOGI(TAG, "state %d: %s", code, s.net == NET_OK ? s.msg : "(beacon)"); last_code = code; }

        if (s_oled) {
            screen_render(&s, t, s_local_ack, hold, CONFIG_SW_WIFI_SSID);
            display_invert(alarm && slow);                 // whole screen flashes on an unacked alarm
            fb_flush();
        }
        vTaskDelay(pdMS_TO_TICKS(40));
    }
}

void app_main(void)
{
    esp_err_t err = nvs_flash_init();                      // WiFi keeps calibration data in NVS
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        nvs_flash_init();
    }
    g_lock = xSemaphoreCreateMutex();
    for (int i = 0; i < 4; i++) g_status.val[i] = NAN;
    g_status.acked = true;

    led_init(CONFIG_SW_PIN_RGB);
    led_rgb(0, 0, 255);
    button_setup(CONFIG_SW_PIN_BTN);
    button_setup(CONFIG_SW_PIN_FIX);

    s_oled = display_init(CONFIG_SW_PIN_SDA, CONFIG_SW_PIN_SCL, CONFIG_SW_OLED_ADDR,
#ifdef CONFIG_SW_OLED_FLIP
                          true
#else
                          false
#endif
    );
    if (s_oled) {
        screen_big("Server", "Watch", "WBEM/WMI beacon", "starting WiFi...");
        fb_flush();
    }
    ESP_LOGI(TAG, "ServerWatch Beacon: SSID '%s', agent %s, OLED %s", CONFIG_SW_WIFI_SSID,
             strlen(CONFIG_SW_AGENT_HOST) ? CONFIG_SW_AGENT_HOST : "auto-discover", s_oled ? "ok" : "missing");
    if (!strlen(CONFIG_SW_API_KEY)) ESP_LOGW(TAG, "no agent key set: FIX will be refused (idf.py menuconfig)");

    net_start();
    xTaskCreatePinnedToCore(ui_task, "ui", 4096, NULL, 3, NULL, 1);
}
