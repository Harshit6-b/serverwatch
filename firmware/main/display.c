// 128x64 SSD1306 over I2C using ESP-IDF's esp_lcd driver. Drawing lives in gfx.c.
#include <string.h>
#include "beacon.h"
#include "gfx.h"
#include "driver/i2c_master.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_ssd1306.h"
#include "esp_log.h"

#define W GFX_W
#define H GFX_H

static const char *TAG = "display";
static esp_lcd_panel_handle_t s_panel;
static bool s_inverted;

bool display_init(int sda, int scl, int addr, bool flip)
{
    i2c_master_bus_handle_t bus = NULL;
    i2c_master_bus_config_t bus_cfg = {
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .i2c_port = 0,
        .sda_io_num = sda,
        .scl_io_num = scl,
        .flags.enable_internal_pullup = true,
    };
    if (i2c_new_master_bus(&bus_cfg, &bus) != ESP_OK) return false;
    if (i2c_master_probe(bus, addr, 50) != ESP_OK) {
        ESP_LOGW(TAG, "no OLED answering at 0x%02X on SDA=%d SCL=%d; running LED-only", addr, sda, scl);
        return false;
    }
    esp_lcd_panel_io_handle_t io = NULL;
    esp_lcd_panel_io_i2c_config_t io_cfg = {
        .dev_addr = addr,
        .scl_speed_hz = 400 * 1000,
        .control_phase_bytes = 1,
        .lcd_cmd_bits = 8,
        .lcd_param_bits = 8,
        .dc_bit_offset = 6,
    };
    if (esp_lcd_new_panel_io_i2c(bus, &io_cfg, &io) != ESP_OK) return false;
    esp_lcd_panel_ssd1306_config_t ssd = {.height = H};
    esp_lcd_panel_dev_config_t dev = {.bits_per_pixel = 1, .reset_gpio_num = -1, .vendor_config = &ssd};
    if (esp_lcd_new_panel_ssd1306(io, &dev, &s_panel) != ESP_OK) return false;
    esp_lcd_panel_reset(s_panel);
    if (esp_lcd_panel_init(s_panel) != ESP_OK) { s_panel = NULL; return false; }
    if (flip) esp_lcd_panel_mirror(s_panel, true, true);
    esp_lcd_panel_disp_on_off(s_panel, true);
    fb_clear();
    fb_flush();
    return true;
}

void fb_flush(void)
{
    if (s_panel) esp_lcd_panel_draw_bitmap(s_panel, 0, 0, W, H, fb_buffer());
}

void display_invert(bool inv)
{
    if (s_panel && inv != s_inverted) {
        esp_lcd_panel_invert_color(s_panel, inv);
        s_inverted = inv;
    }
}
