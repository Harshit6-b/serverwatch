#include <string.h>
#include "matrix_icons.h"

static const uint8_t ICON_OK[8]   = {0x00, 0x01, 0x03, 0x06, 0x8C, 0xD8, 0x70, 0x20};  // check mark
static const uint8_t ICON_WARN[8] = {0x18, 0x18, 0x18, 0x18, 0x18, 0x00, 0x18, 0x18};  // !
static const uint8_t ICON_CRIT[8] = {0x81, 0x42, 0x24, 0x18, 0x18, 0x24, 0x42, 0x81};  // X
static const uint8_t ICON_DOWN[8] = {0x3C, 0x42, 0xA5, 0x81, 0x99, 0xA5, 0x42, 0x3C};  // sad face
static const uint8_t ICON_WIFI[8] = {0x00, 0x3C, 0x42, 0x99, 0x24, 0x00, 0x18, 0x18};  // WiFi arcs

static void put(uint8_t rows[8], const uint8_t icon[8], bool show)
{
    if (show) memcpy(rows, icon, 8);
    else memset(rows, 0, 8);
}

// A dot running clockwise around the edge with a short tail: "searching".
static void spinner(uint8_t rows[8], int64_t now_us)
{
    memset(rows, 0, 8);
    int head = (int)((now_us / 60000) % 28);
    for (int k = 0; k < 3; k++) {
        int p = (head - k + 28) % 28, x, y;
        if (p < 7)       { x = p;       y = 0; }
        else if (p < 14) { x = 7;       y = p - 7; }
        else if (p < 21) { x = 21 - p;  y = 7; }
        else             { x = 0;       y = 28 - p; }
        rows[y] |= (uint8_t)(0x80 >> x);
    }
}

void matrix_frame(const beacon_status_t *s, bool local_ack, int64_t now_us, uint8_t rows[8])
{
    bool fast = (now_us / 125000) & 1, slow = (now_us / 500000) & 1;
    switch (s->net) {
    case NET_WIFI_DOWN: put(rows, ICON_WIFI, slow); break;
    case NET_SEARCHING: spinner(rows, now_us); break;
    case NET_DOWN:      put(rows, ICON_DOWN, local_ack || fast); break;
    case NET_OK:
        if (s->overall == ST_OK) {
            put(rows, ICON_OK, true);
            if ((now_us / 1000000) & 1) rows[7] |= 0x01;    // heartbeat pixel: the beacon is alive
        } else if (s->overall == ST_WARNING) {
            put(rows, ICON_WARN, true);
        } else if (s->overall == ST_CRITICAL) {
            put(rows, ICON_CRIT, s->acked || fast);
        } else {
            put(rows, ICON_DOWN, s->acked || fast);
        }
        break;
    }
}
