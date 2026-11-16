// Status light: the onboard WS2812 RGB LED (DevKit) or the 8x8 MAX7219 matrix (Wall-E).
#include <string.h>
#include "beacon.h"
#include "board.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_rom_sys.h"
#include "matrix_icons.h"
#include "sdkconfig.h"

static const char *TAG = "indicator";

#if BOARD_LED_MATRIX
// ---------------------------------------------------------------- MAX7219, bit-banged
// SRA board: digit register (1 + x) holds column x, left to right; bit 7 is the top LED.
static uint8_t s_cols[8];
static bool s_valid;

static void mx_write(uint8_t reg, uint8_t val)
{
    uint16_t w = (uint16_t)(reg << 8) | val;
    gpio_set_level(MATRIX_PIN_LOAD, 0);
    for (int i = 15; i >= 0; i--) {               // MSB first, sampled on the rising edge
        gpio_set_level(MATRIX_PIN_CLK, 0);
        gpio_set_level(MATRIX_PIN_DIN, (w >> i) & 1);
        esp_rom_delay_us(1);
        gpio_set_level(MATRIX_PIN_CLK, 1);
        esp_rom_delay_us(1);
    }
    gpio_set_level(MATRIX_PIN_CLK, 0);
    gpio_set_level(MATRIX_PIN_LOAD, 1);            // latch
}

void indicator_init(void)
{
    gpio_config_t c = {.pin_bit_mask = (1ULL << MATRIX_PIN_CLK) | (1ULL << MATRIX_PIN_DIN) | (1ULL << MATRIX_PIN_LOAD),
                       .mode = GPIO_MODE_OUTPUT};
    gpio_config(&c);
    gpio_set_level(MATRIX_PIN_LOAD, 1);
    gpio_set_level(MATRIX_PIN_CLK, 0);
    mx_write(0x0F, 0x00);                          // display test off
    mx_write(0x0B, 0x07);                          // scan all 8 digits
    mx_write(0x09, 0x00);                          // no BCD decode
    mx_write(0x0A, CONFIG_SW_LED_BRIGHTNESS / 17); // intensity 0..15
    mx_write(0x0C, 0x01);                          // leave shutdown
    for (int x = 0; x < 8; x++) mx_write(0x01 + x, 0);
    memset(s_cols, 0, sizeof(s_cols));
    s_valid = true;
    ESP_LOGI(TAG, "LED matrix on CLK=%d DIN=%d LOAD=%d", MATRIX_PIN_CLK, MATRIX_PIN_DIN, MATRIX_PIN_LOAD);
}

void indicator_update(const beacon_status_t *s, bool local_ack, int64_t now_us)
{
    uint8_t rows[8];
    matrix_frame(s, local_ack, now_us, rows);
    for (int x = 0; x < 8; x++) {
        uint8_t col = 0;
        for (int y = 0; y < 8; y++)
            if (rows[y] & (0x80 >> x)) col |= (uint8_t)(0x80 >> y);
        if (!s_valid || col != s_cols[x]) {        // only send columns that changed
            mx_write(0x01 + x, col);
            s_cols[x] = col;
        }
    }
}

#else
// ---------------------------------------------------------------- WS2812 via RMT
#include "driver/rmt_tx.h"

static rmt_channel_handle_t s_chan;
static rmt_encoder_handle_t s_enc;
static uint32_t s_last = 0xFFFFFFFF;

void indicator_init(void)
{
    rmt_tx_channel_config_t tx = {
        .gpio_num = RGB_PIN, .clk_src = RMT_CLK_SRC_DEFAULT,
        .resolution_hz = 10 * 1000 * 1000, .mem_block_symbols = 64, .trans_queue_depth = 2,
    };
    // WS2812: "0" = 0.3 us high + 0.9 us low, "1" = 0.9 us high + 0.3 us low
    rmt_bytes_encoder_config_t bytes = {
        .bit0 = {.level0 = 1, .duration0 = 3, .level1 = 0, .duration1 = 9},
        .bit1 = {.level0 = 1, .duration0 = 9, .level1 = 0, .duration1 = 3},
        .flags.msb_first = 1,
    };
    if (rmt_new_tx_channel(&tx, &s_chan) != ESP_OK || rmt_new_bytes_encoder(&bytes, &s_enc) != ESP_OK
        || rmt_enable(s_chan) != ESP_OK) {
        ESP_LOGW(TAG, "RGB LED init failed on GPIO %d", RGB_PIN);
        s_chan = NULL;
    }
}

static void rgb(uint8_t r, uint8_t g, uint8_t b)
{
    if (!s_chan) return;
    const int k = CONFIG_SW_LED_BRIGHTNESS;
    r = r * k / 255; g = g * k / 255; b = b * k / 255;
    uint32_t key = ((uint32_t)r << 16) | (g << 8) | b;
    if (key == s_last) return;
    s_last = key;
    uint8_t grb[3] = {g, r, b};                    // WS2812 wants green first
    rmt_transmit_config_t cfg = {.loop_count = 0};
    rmt_transmit(s_chan, s_enc, grb, sizeof(grb), &cfg);
    rmt_tx_wait_all_done(s_chan, 20);
}

void indicator_update(const beacon_status_t *s, bool local_ack, int64_t now_us)
{
    bool fast = (now_us / 125000) & 1, slow = (now_us / 500000) & 1;
    switch (s->net) {
    case NET_WIFI_DOWN: slow ? rgb(0, 0, 255) : rgb(0, 0, 0); break;
    case NET_SEARCHING: slow ? rgb(0, 160, 255) : rgb(0, 0, 0); break;
    case NET_DOWN:      (local_ack || fast) ? rgb(255, 0, 200) : rgb(0, 0, 0); break;
    case NET_OK:
        if (s->overall == ST_OK) rgb(0, 255, 0);
        else if (s->overall == ST_WARNING) rgb(255, 110, 0);
        else if (s->overall == ST_CRITICAL) (s->acked || fast) ? rgb(255, 0, 0) : rgb(0, 0, 0);
        else (s->acked || fast) ? rgb(255, 0, 200) : rgb(0, 0, 0);
        break;
    }
}
#endif
