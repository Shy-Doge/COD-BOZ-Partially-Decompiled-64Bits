#include "pvrtc.h"
/* addresses are image offsets, injected by the linker via --defsym */
extern void STUB_TexImage2D(u32, i32, i32, i32, i32, i32, u32, u32, const void *);
extern void STUB_TexSubImage2D(u32, i32, i32, i32, i32, i32, u32, u32, const void *);
extern u64 SLOT_CompTexImage2D[], SLOT_CompTexSubImage2D[], SLOT_GetString[];
extern u8 EXTBUF[];                                           /* 0x2000 bytes of extra BSS */
#define EXTBUF_SIZE 0x2000
#define GL_RGBA 0x1908
#define GL_UBYTE 0x1401

#ifndef PASSTHRU
#define PASSTHRU 0
#endif
/* 0x8C00 RGB 4bpp, 0x8C02 RGBA 4bpp. The 2bpp formats (0x8C01, 0x8C03) are not decoded and pass through. */
static int is_pvr(u32 f) { return !PASSTHRU && (f == 0x8C00 || f == 0x8C02); }

static void upload(u32 target, i32 level, u32 fmt, i32 w, i32 h, i32 xo, i32 yo, const u32 *d, int sub) {
    int alpha = (fmt == 0x8C02);
    u32 bw = (u32)w / 4; if (bw < 2) bw = 2;
    u32 bh = (u32)h / 4;    if (bh < 2) bh = 2;
    if (bw > 512 || w <= 0 || h <= 0) return;                  /* too wide for the stack buffers */
    pcol rows[3 * 512];
    u8 out[4 * 2048 * 4];
    if (!sub) STUB_TexImage2D(target, level, GL_RGBA, w, h, 0, GL_RGBA, GL_UBYTE, 0);
    u32 W = bw * 4;
    for (u32 by = 0; by < bh && (i32)(by * 4) < h; by++) {
        pv_row(d, bw, bh, by, alpha, rows, out);
        i32 nrows = h - (i32)(by * 4); if (nrows > 4) nrows = 4;
        if ((u32)w != W) {                                      /* compact rows when texture narrower than one block */
            for (i32 r = 1; r < nrows; r++) for (i32 x = 0; x < w * 4; x++) out[r * w * 4 + x] = out[r * (i32)W * 4 + x];
        }
        STUB_TexSubImage2D(target, level, xo, yo + (i32)(by * 4), w, nrows, GL_RGBA, GL_UBYTE, out);
    }
}

typedef void (*cti_t)(u32, i32, u32, i32, i32, i32, i32, const void *);
typedef void (*ctsi_t)(u32, i32, i32, i32, i32, i32, u32, i32, const void *);

__attribute__((used)) void hook_CompTexImage2D(u32 target, i32 level, u32 fmt, i32 w, i32 h, i32 border, i32 size, const void *data) {
    if (!is_pvr(fmt) || !data) { ((cti_t)SLOT_CompTexImage2D[0])(target, level, fmt, w, h, border, size, data); return; }
    upload(target, level, fmt, w, h, 0, 0, (const u32 *)data, 0);
}
__attribute__((used)) void hook_CompTexSubImage2D(u32 target, i32 level, i32 xo, i32 yo, i32 w, i32 h, u32 fmt, i32 size, const void *data) {
    if (!is_pvr(fmt) || !data) { ((ctsi_t)SLOT_CompTexSubImage2D[0])(target, level, xo, yo, w, h, fmt, size, data); return; }
    upload(target, level, fmt, w, h, xo, yo, (const u32 *)data, 1);
}

__attribute__((noinline)) static int has_compress(const u8 *t, u32 len) {
    static const char k[] = "compress";
    for (u32 i = 0; i + 8 <= len; i++) { u32 j = 0; while (j < 8 && t[i + j] == (u8)k[j]) j++; if (j == 8) return 1; }
    return 0;
}
/* Tell the game PVRTC is supported and nothing else compressed is, so it picks the PVRTC data set. */
__attribute__((used)) const u8 *hook_GetString(u32 name) {
    const u8 *s = ((const u8 *(*)(u32))SLOT_GetString[0])(name);
    if (name != 0x1F03 || !s) return s;
    if (EXTBUF[0]) return EXTBUF;
    u32 n = 0; const u8 *p = s;
    while (*p) {
        const u8 *t = p; while (*p && *p != ' ') p++;
        u32 len = (u32)(p - t);
        if (len && !has_compress(t, len) && n + len + 40 < EXTBUF_SIZE) { for (u32 i = 0; i < len; i++) EXTBUF[n + i] = t[i]; n += len; EXTBUF[n++] = ' '; }
        while (*p == ' ') p++;
    }
    static const char add[] = "GL_IMG_texture_compression_pvrtc";
    for (u32 i = 0; i < sizeof(add) - 1; i++) EXTBUF[n++] = (u8)add[i];
    EXTBUF[n++] = ' '; EXTBUF[n] = 0;
    return EXTBUF;
}

/* ===================== runtime config file =====================
 * config.txt (first found):  /sdcard/BlackOpsZombies/config.txt   (needs "all files access", which the app asks for)
 *                            /sdcard/Android/data/com.activision.boz/files/config.txt
 *   multiplayer_server=host[:port]     replaces LSGServer / AuthServer / STUNServer
 *   voice_chat=0|1                     replaces [GAME] VoiceChatEnabled
 *   player_name=Name                   up to 13 of: letters digits space _ . -
 * The values are applied by hooking the game's own s3eConfigGetString / s3eConfigGetInt import stubs and by pointing the
 * "Player-%d" default-name format string at a RAM buffer.
 */
extern void *STUB_FileOpen(const char *, const char *);
extern u32 STUB_FileRead(void *, u32, u32, void *);
extern i32 STUB_FileClose(void *);
extern u64 SLOT_ConfigGetString[], SLOT_ConfigGetInt[];
extern u8 CFG[];                                  /* second half of the extra BSS */

struct cfg { u32 loaded, voice; char name[16]; char lsg[72]; char stun[64]; u8 has_server, has_voice; char fbuf[1100]; };
#define C ((struct cfg *)CFG)

__attribute__((noinline)) static int streq(const char *a, const char *b) { while (*a && *a == *b) { a++; b++; } return *a == *b; }
__attribute__((noinline)) static char *trim(char *s) {
    while (*s == ' ' || *s == '\t' || *s == '\r') s++;
    char *e = s; while (*e) e++;
    while (e > s && (e[-1] == ' ' || e[-1] == '\t' || e[-1] == '\r')) *--e = 0;
    return s;
}
static int okc(char ch, int host) {
    return (ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') || (ch >= '0' && ch <= '9') || ch == '.' || ch == '-' ||
           (host ? ch == ':' : (ch == ' ' || ch == '_'));
}

static void cfg_apply(const char *k, char *v) {
    u32 n = 0; while (v[n]) n++;
    if (streq(k, "voice_chat")) { C->voice = (v[0] == '1'); C->has_voice = 1; return; }
    int host = streq(k, "multiplayer_server");
    if (!host && !streq(k, "player_name")) return;
    if (n < 1 || n > (host ? 50u : 13u)) return;
    for (u32 i = 0; i < n; i++) if (!okc(v[i], host)) return;
    if (!host) { for (u32 i = 0; i <= n; i++) C->name[i] = v[i]; return; }
    u32 h = 0; while (h < n && v[h] != ':') h++;
    if (!h) return;
    for (u32 i = 0; i < h; i++) C->stun[i] = v[i];
    C->stun[h] = 0;
    for (u32 i = 0; i < n; i++) C->lsg[i] = v[i];
    if (h == n) { C->lsg[n] = ':'; C->lsg[n + 1] = '3'; C->lsg[n + 2] = '0'; C->lsg[n + 3] = '7'; C->lsg[n + 4] = '4'; C->lsg[n + 5] = 0; } else C->lsg[n] = 0;
    C->has_server = 1;
}

static void cfg_load(void) {
    if (C->loaded) return;
    C->loaded = 1;
    static const char dflt[] = "Player-%d";
    for (u32 i = 0; i < sizeof(dflt); i++) C->name[i] = dflt[i];
    void *f = STUB_FileOpen("raw:///sdcard/BlackOpsZombies/config.txt", "rb");
    if (!f) f = STUB_FileOpen("raw:///sdcard/Android/data/com.activision.boz/files/config.txt", "rb");
    if (!f) return;
    u32 n = STUB_FileRead(C->fbuf, 1, sizeof(C->fbuf) - 1, f);
    STUB_FileClose(f);
    C->fbuf[n] = 0;
    char *p = C->fbuf;
    while (*p) {
        char *l = p; while (*p && *p != '\n') p++;
        if (*p) *p++ = 0;
        char *eq = l; while (*eq && *eq != '=') eq++;
        if (!*eq) continue;
        *eq = 0;
        char *k = trim(l);
        if (*k != '#') cfg_apply(k, trim(eq + 1));
    }
}

typedef i32 (*cgs_t)(const char *, const char *, char *);
typedef i32 (*cgi_t)(const char *, const char *, i32 *);

__attribute__((used)) i32 hook_ConfigGetString(const char *sec, const char *name, char *val) {
    cfg_load();
    i32 r = ((cgs_t)SLOT_ConfigGetString[0])(sec, name, val);
    if (C->has_server) {
        const char *src = (streq(name, "LSGServer") || streq(name, "AuthServer")) ? C->lsg : streq(name, "STUNServer") ? C->stun : 0;
        if (src) { u32 i = 0; do val[i] = src[i]; while (src[i++]); return 0; }
    }
    return r;
}
__attribute__((used)) i32 hook_ConfigGetInt(const char *sec, const char *name, i32 *val) {
    cfg_load();
    i32 r = ((cgi_t)SLOT_ConfigGetInt[0])(sec, name, val);
    if (C->has_voice && streq(name, "VoiceChatEnabled")) { *val = (i32)C->voice; return 0; }
    return r;
}
