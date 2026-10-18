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
static int is_pvr(u32 f) { return !PASSTHRU && f >= 0x8C00 && f <= 0x8C03; }

static void upload(u32 target, i32 level, u32 fmt, i32 w, i32 h, i32 xo, i32 yo, const u32 *d, int sub) {
    int bpp2 = (fmt == 0x8C01 || fmt == 0x8C03), alpha = (fmt == 0x8C02 || fmt == 0x8C03);
    u32 bwpx = bpp2 ? 8 : 4;
    u32 bw = (u32)w / bwpx; if (bw < 2) bw = 2;
    u32 bh = (u32)h / 4;    if (bh < 2) bh = 2;
    if (bw > 512 || w <= 0 || h <= 0) return;                  /* too wide for the stack buffers */
    pcol rows[3 * 512];
    u8 out[4 * 2048 * 4];
    if (!sub) STUB_TexImage2D(target, level, GL_RGBA, w, h, 0, GL_RGBA, GL_UBYTE, 0);
    u32 W = bw * bwpx;
    for (u32 by = 0; by < bh && (i32)(by * 4) < h; by++) {
        pv_row(d, bw, bh, by, alpha, bpp2, rows, out);
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

static int has_compress(const u8 *t, u32 len) {
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
