// Pin map per board. The Wall-E values come from SRA-VJTI/sra-board-component (main, 2026):
// include/pin_defs.h, include/lvgl_helpers.h and src/led_matrix.c.
#pragma once
#include "sdkconfig.h"

#if CONFIG_SW_BOARD_SRA_WALLE
#define BOARD_NAME        "SRA Wall-E 2026"
#define BOARD_PIN_SDA     14          // OLED_SDA (shared with the MPU6050)
#define BOARD_PIN_SCL     21          // OLED_SCL
#define BOARD_PIN_BTN     0           // BOOT button: the board has no other user buttons
#define BOARD_PIN_FIX     (-1)
#define BOARD_LED_MATRIX  1           // 8x8 MAX7219
#define MATRIX_PIN_CLK    18          // LED_MATRIX_CLK
#define MATRIX_PIN_DIN    16          // LED_MATRIX_DIN
#define MATRIX_PIN_LOAD   17          // LED_MATRIX_LOAD
#else
#define BOARD_NAME        "ESP32-S3 DevKit"
#define BOARD_PIN_SDA     CONFIG_SW_PIN_SDA
#define BOARD_PIN_SCL     CONFIG_SW_PIN_SCL
#define BOARD_PIN_BTN     CONFIG_SW_PIN_BTN
#define BOARD_PIN_FIX     CONFIG_SW_PIN_FIX
#define BOARD_RGB_LED     1
#define RGB_PIN           CONFIG_SW_PIN_RGB
#endif
