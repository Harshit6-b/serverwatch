// What the beacon knows about the server (pure C, also builds on a PC).
#pragma once
#include <stdbool.h>
#include <stdint.h>

typedef enum { NET_WIFI_DOWN, NET_SEARCHING, NET_OK, NET_DOWN } net_state_t;
typedef enum { CMD_NONE, CMD_ACK, CMD_FIX } beacon_cmd_t;
enum { ST_OK, ST_WARNING, ST_CRITICAL, ST_UNREACHABLE };

typedef struct {
    net_state_t net;
    int overall;              // 0..3 from the agent
    bool acked;
    bool fixable;             // a watched service is down, FIX can help
    char host[16];
    char hint[24];            // "Spooler:Stopped", "top chrome 82%", "DSK crit in 3m 10s"
    char msg[64];
    float val[4];             // cpu %, mem %, disk free %, net %  (NAN = no data)
    int lvl[4];
    int64_t last_ok_us;       // time of the last good reply (0 = never)
    char my_ip[16];
    char agent_ip[16];
    char note[40];            // short feedback after a button press
    int64_t note_until_us;
} beacon_status_t;
