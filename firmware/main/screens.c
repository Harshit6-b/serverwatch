#include <math.h>
#include <stdio.h>
#include <string.h>
#include "gfx.h"
#include "screens.h"

static void centred(int y, const char *t, int scale)
{
    int w = (int)strlen(t) * 6 * scale - scale;
    fb_text(w < GFX_W ? (GFX_W - w) / 2 : 0, y, t, scale, true);
}

void screen_big(const char *l1, const char *l2, const char *sub, const char *foot)
{
    fb_clear();
    centred(2, l1, 2);
    centred(20, l2, 2);
    fb_hline(16, 40, GFX_W - 32);
    centred(44, sub, 1);
    centred(55, foot, 1);
}

void screen_main(const beacon_status_t *s, const char *bottom, float hold)
{
    static const char *LBL[] = {"OK", "WARN", "CRIT", "DOWN"};
    static const char *NAMES[] = {"CPU", "MEM", "DSK", "NET"};
    fb_clear();
    fb_text(0, 1, s->host, 1, true);
    const char *lb = LBL[s->overall < 0 || s->overall > 3 ? 3 : s->overall];
    int w = (int)strlen(lb) * 6 + 5;
    fb_fill(GFX_W - w, 0, w, 9, true);
    fb_text(GFX_W - w + 3, 1, lb, 1, false);
    fb_hline(0, 10, GFX_W);

    for (int i = 0; i < 4; i++) {
        int y = 13 + i * 10;
        fb_text(0, y, NAMES[i], 1, true);
        float v = s->val[i];
        float used = (i == 2) ? 100 - v : v;              // disk arrives as % free
        fb_rect(22, y, 68, 7);
        if (!isnan(v)) {
            float u = used < 0 ? 0 : used > 100 ? 100 : used;
            fb_fill(24, y + 2, (int)(64 * u / 100), 3, true);
        }
        char t[8];
        if (isnan(v)) snprintf(t, sizeof(t), " --");
        else if (i == 2) snprintf(t, sizeof(t), "%2.0f%%fr", v);
        else snprintf(t, sizeof(t), "%3.0f%%", v);
        bool crit = s->lvl[i] >= ST_CRITICAL;
        if (crit) fb_fill(92, y - 1, 36, 9, true);
        fb_text(94, y, t, 1, !crit);
        if (s->lvl[i] == ST_WARNING) fb_text(123, y, "!", 1, true);
    }
    fb_hline(0, 53, GFX_W);
    if (hold >= 0) {                                       // hold-to-FIX progress bar
        fb_text(0, 56, "FIX", 1, true);
        fb_rect(22, 56, 104, 7);
        fb_fill(24, 58, (int)(100 * hold), 3, true);
    } else {
        fb_text(0, 56, bottom, 1, true);
    }
}

void screen_render(const beacon_status_t *s, int64_t now_us, bool local_ack, float hold, const char *ssid)
{
    char foot[32], sub[32];
    bool note = now_us < s->note_until_us;
    if (s->net == NET_WIFI_DOWN) {
        snprintf(foot, sizeof(foot), "%.21s", ssid);
        screen_big("BEACON", "NO WIFI", "joining network:", foot);
    } else if (s->net == NET_SEARCHING) {
        snprintf(foot, sizeof(foot), "me %.15s", s->my_ip);
        screen_big("SEARCHING", "agent...", "run serverwatch.py", foot);
    } else if (s->net == NET_DOWN) {
        if (s->last_ok_us) snprintf(sub, sizeof(sub), "no reply for %llds", (long long)((now_us - s->last_ok_us) / 1000000));
        else snprintf(sub, sizeof(sub), "never answered");
        snprintf(foot, sizeof(foot), "%.21s", note ? s->note : (local_ack ? "silenced" : "press = silence"));
        screen_big("SERVER", "DOWN", sub, foot);
    } else {
        char bottom[22];                                   // one line = 21 characters
        if (note) snprintf(bottom, sizeof(bottom), "%.21s", s->note);
        else if (s->hint[0] && s->fixable) snprintf(bottom, sizeof(bottom), "%.12s HOLD=FIX", s->hint);
        else if (s->hint[0]) snprintf(bottom, sizeof(bottom), "%.21s", s->hint);
        else snprintf(bottom, sizeof(bottom), "%.21s", s->msg);
        screen_main(s, bottom, hold);
    }
}
