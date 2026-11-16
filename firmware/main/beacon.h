// ServerWatch Beacon: shared state between the network task and the UI task.
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

#include "status.h"
#include "gfx.h"
#include "screens.h"

extern beacon_status_t g_status;
extern SemaphoreHandle_t g_lock;
extern volatile beacon_cmd_t g_cmd;

void beacon_lock(void);
void beacon_unlock(void);
void beacon_note(const char *text);

// net.c
void net_start(void);

// led.c
void led_init(int gpio);
void led_rgb(uint8_t r, uint8_t g, uint8_t b);

// display.c (hardware side; drawing is in gfx.c / screens.c)
bool display_init(int sda, int scl, int addr, bool flip);
void fb_flush(void);
void display_invert(bool inv);
