// Renders the beacon's real screen code on a PC, for docs and for checking layouts without hardware.
//   cc -O1 -I../main render_screens.c ../main/gfx.c ../main/screens.c -lm -o render && ./render out/
// Writes one 128x64 PGM per state; tools/oled_png.py turns them into OLED-looking PNGs.
#include <math.h>
#include <stdio.h>
#include <string.h>
#include "gfx.h"
#include "screens.h"

static void save(const char *dir, const char *name)
{
    char path[256];
    snprintf(path, sizeof(path), "%s/%s.pgm", dir, name);
    FILE *f = fopen(path, "wb");
    if (!f) { perror(path); return; }
    fprintf(f, "P5\n%d %d\n255\n", GFX_W, GFX_H);
    const uint8_t *fb = fb_buffer();
    for (int y = 0; y < GFX_H; y++)
        for (int x = 0; x < GFX_W; x++) fputc((fb[(y >> 3) * GFX_W + x] >> (y & 7)) & 1 ? 255 : 0, f);
    fclose(f);
    printf("%s\n", path);
}

static beacon_status_t base(void)
{
    beacon_status_t s;
    memset(&s, 0, sizeof(s));
    s.net = NET_OK;
    s.acked = true;
    strcpy(s.host, "LAB-SERVER-01");
    float v[4] = {14, 48, 46, 2};
    memcpy(s.val, v, sizeof(v));
    strcpy(s.msg, "All checks OK");
    return s;
}

int main(int argc, char **argv)
{
    const char *dir = argc > 1 ? argv[1] : ".";
    const int64_t now = 100000000;
    beacon_status_t s;

    s = base();
    screen_render(&s, now, false, -1, "MyHotspot");                save(dir, "01-ok");

    s = base(); s.overall = ST_WARNING; s.val[0] = 81; s.lvl[0] = ST_WARNING;
    strcpy(s.hint, "top cpu_burn 72%");
    screen_render(&s, now, false, -1, "MyHotspot");                save(dir, "02-cpu-warning");

    s = base(); s.overall = ST_CRITICAL; s.acked = false; s.val[0] = 97; s.lvl[0] = ST_CRITICAL;
    strcpy(s.hint, "top cpu_burn 91%");
    screen_render(&s, now, false, -1, "MyHotspot");                save(dir, "03-cpu-critical");

    s = base(); s.overall = ST_CRITICAL; s.acked = false; s.fixable = true;
    strcpy(s.hint, "Spooler:Stopped");
    screen_render(&s, now, false, -1, "MyHotspot");                save(dir, "04-service-down");

    screen_render(&s, now, false, 0.6f, "MyHotspot");              save(dir, "05-hold-to-fix");

    s = base(); s.val[2] = 11; s.lvl[2] = ST_WARNING; s.overall = ST_WARNING;
    strcpy(s.hint, "DSK crit in 1m 40s");
    screen_render(&s, now, false, -1, "MyHotspot");                save(dir, "06-disk-prediction");

    s = base(); s.net = NET_DOWN; s.last_ok_us = now - 12000000;
    screen_render(&s, now, false, -1, "MyHotspot");                save(dir, "07-server-down");

    s = base(); s.net = NET_WIFI_DOWN;
    screen_render(&s, now, false, -1, "MyHotspot");                save(dir, "08-no-wifi");
    return 0;
}
