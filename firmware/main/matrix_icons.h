// 8x8 status icons for the Wall-E LED matrix (pure C, also builds on a PC).
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "status.h"

// rows[y], y = 0 top .. 7 bottom; bit 7 = leftmost pixel.
void matrix_frame(const beacon_status_t *s, bool local_ack, int64_t now_us, uint8_t rows[8]);
