// Onboard WS2812 RGB LED driven by the RMT peripheral (no extra components needed).
#include "beacon.h"
#include "driver/rmt_tx.h"
#include "esp_log.h"
#include "sdkconfig.h"

static rmt_channel_handle_t s_chan;
static rmt_encoder_handle_t s_enc;
static uint32_t s_last = 0xFFFFFFFF;

void led_init(int gpio)
{
    rmt_tx_channel_config_t tx = {
        .gpio_num = gpio,
        .clk_src = RMT_CLK_SRC_DEFAULT,
        .resolution_hz = 10 * 1000 * 1000,   // 0.1 us per tick
        .mem_block_symbols = 64,
        .trans_queue_depth = 2,
    };
    // WS2812: "0" = 0.3 us high + 0.9 us low, "1" = 0.9 us high + 0.3 us low
    rmt_bytes_encoder_config_t bytes = {
        .bit0 = {.level0 = 1, .duration0 = 3, .level1 = 0, .duration1 = 9},
        .bit1 = {.level0 = 1, .duration0 = 9, .level1 = 0, .duration1 = 3},
        .flags.msb_first = 1,
    };
    if (rmt_new_tx_channel(&tx, &s_chan) != ESP_OK || rmt_new_bytes_encoder(&bytes, &s_enc) != ESP_OK
        || rmt_enable(s_chan) != ESP_OK) {
        ESP_LOGW("led", "RGB LED init failed on GPIO %d", gpio);
        s_chan = NULL;
    }
}

void led_rgb(uint8_t r, uint8_t g, uint8_t b)
{
    if (!s_chan) return;
    const int k = CONFIG_SW_LED_BRIGHTNESS;
    r = r * k / 255; g = g * k / 255; b = b * k / 255;
    uint32_t key = (r << 16) | (g << 8) | b;
    if (key == s_last) return;                // only talk to the LED when the colour changes
    s_last = key;
    uint8_t grb[3] = {g, r, b};               // WS2812 expects green first
    rmt_transmit_config_t cfg = {.loop_count = 0};
    rmt_transmit(s_chan, s_enc, grb, sizeof(grb), &cfg);
    rmt_tx_wait_all_done(s_chan, 20);
}
