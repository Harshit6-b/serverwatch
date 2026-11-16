idf_component_register(
    SRCS "main.c" "net.c" "display.c" "led.c" "gfx.c" "screens.c"
    INCLUDE_DIRS "."
    PRIV_REQUIRES esp_wifi esp_netif esp_event nvs_flash esp_http_client json
                  esp_lcd esp_driver_i2c esp_driver_rmt esp_driver_gpio esp_timer lwip
)
