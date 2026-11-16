// Framebuffer drawing for the 128x64 OLED (pure C, also builds on a PC).
#pragma once
#include <stdbool.h>
#include <stdint.h>

#define GFX_W 128
#define GFX_H 64

void fb_clear(void);
void fb_fill(int x, int y, int w, int h, bool on);
void fb_rect(int x, int y, int w, int h);
void fb_hline(int x, int y, int w);
int  fb_text(int x, int y, const char *s, int scale, bool on);   // returns the x after the text
const uint8_t *fb_buffer(void);
