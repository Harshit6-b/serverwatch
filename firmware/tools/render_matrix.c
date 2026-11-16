// Renders the Wall-E 8x8 matrix icons on a PC (same code as the firmware).
//   cc -I../main render_matrix.c ../main/matrix_icons.c -o rm && ./rm out/
#include <stdio.h>
#include <string.h>
#include "matrix_icons.h"

static void save(const char *dir, const char *name, const uint8_t rows[8])
{
    char path[256];
    snprintf(path, sizeof(path), "%s/%s.pgm", dir, name);
    FILE *f = fopen(path, "wb");
    if (!f) { perror(path); return; }
    fprintf(f, "P5\n8 8\n255\n");
    for (int y = 0; y < 8; y++)
        for (int x = 0; x < 8; x++) fputc(rows[y] & (0x80 >> x) ? 255 : 0, f);
    fclose(f);
}

int main(int argc, char **argv)
{
    const char *dir = argc > 1 ? argv[1] : ".";
    beacon_status_t s;
    uint8_t r[8];
    const int64_t lit = 0;                     // a moment when blinking icons are shown
    memset(&s, 0, sizeof(s));
    s.net = NET_OK; s.acked = true;
    s.overall = ST_OK;       matrix_frame(&s, false, lit, r); save(dir, "m1-ok", r);
    s.overall = ST_WARNING;  matrix_frame(&s, false, lit, r); save(dir, "m2-warning", r);
    s.overall = ST_CRITICAL; matrix_frame(&s, false, lit, r); save(dir, "m3-critical", r);
    s.net = NET_DOWN;        matrix_frame(&s, true, lit, r);  save(dir, "m4-server-down", r);
    s.net = NET_WIFI_DOWN;   matrix_frame(&s, false, 600000, r); save(dir, "m5-no-wifi", r);
    s.net = NET_SEARCHING;   matrix_frame(&s, false, 300000, r); save(dir, "m6-searching", r);
    return 0;
}
