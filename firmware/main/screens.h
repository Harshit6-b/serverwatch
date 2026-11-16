// The beacon's screens, drawn into the framebuffer (pure C, also builds on a PC).
#pragma once
#include "status.h"

void screen_big(const char *l1, const char *l2, const char *sub, const char *foot);
// bottom = the 21-character hint line; hold = FIX hold progress 0..1, or -1
void screen_main(const beacon_status_t *s, const char *bottom, float hold);
// Pick and draw the right screen for the current state.
void screen_render(const beacon_status_t *s, int64_t now_us, bool local_ack, float hold, const char *ssid);
