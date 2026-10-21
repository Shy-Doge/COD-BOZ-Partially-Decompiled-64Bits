/* Compact PVRTC1 4bpp decoder (RGB and RGBA), freestanding, no libc. 2bpp is not supported. */
typedef unsigned char u8; typedef unsigned short u16; typedef unsigned int u32; typedef unsigned long long u64; typedef int i32;

typedef struct { u8 a[4], b[4]; } pcol;   /* A/B colours: rgb 5-bit, alpha 4-bit */

__attribute__((noinline)) static u32 pv_twiddle(u32 xs, u32 ys, u32 x, u32 y) {          /* sizes/positions in blocks */
    u32 minDim = xs, maxVal = y, tw = 0, src = 1, dst = 1; int sh = 0;
    if (ys < xs) { minDim = ys; maxVal = x; }
    while (src < minDim) {
        if (y & src) tw |= dst;
        if (x & src) tw |= dst << 1;
        src <<= 1; dst <<= 2; sh++;
    }
    tw |= (maxVal >> sh) << (2 * sh);
    return tw;
}

__attribute__((noinline)) static void pv_unpack(u32 c, pcol *o) {
    if (c & 0x8000) { u32 b = c & 0x1e; o->a[0] = (c >> 10) & 31; o->a[1] = (c >> 5) & 31; o->a[2] = b | (b >> 4); o->a[3] = 15; }
    else { u32 r = (c >> 8) & 15, g = (c >> 4) & 15, b = (c >> 1) & 7;
           o->a[0] = (r << 1) | (r >> 3); o->a[1] = (g << 1) | (g >> 3); o->a[2] = (b << 2) | (b >> 1); o->a[3] = ((c >> 12) & 7) << 1; }
    if (c & 0x80000000u) { o->b[0] = (c >> 26) & 31; o->b[1] = (c >> 21) & 31; o->b[2] = (c >> 16) & 31; o->b[3] = 15; }
    else { u32 r = (c >> 24) & 15, g = (c >> 20) & 15, b = (c >> 16) & 15;
           o->b[0] = (r << 1) | (r >> 3); o->b[1] = (g << 1) | (g >> 3); o->b[2] = (b << 1) | (b >> 3); o->b[3] = ((c >> 28) & 7) << 1; }
}

/* Decode one row of 4x4 (4bpp) blocks.  rows: caller scratch of 3*bw pcol (prev,cur,next).  out: 4 rows * bw*4 pixels RGBA8. */
#define PV_MAXBW 1024
static void pv_row(const u32 *d /* 2 u32 per block, twiddled */, u32 bw, u32 bh, u32 by, int hasAlpha, pcol *rows, u8 *out) {
    enum { BW = 4 };
    for (int k = 0; k < 3; k++) {
        u32 yy = (by + bh + k - 1) % bh;
        for (u32 bx = 0; bx < bw; bx++) {
            u32 i = pv_twiddle(bw, bh, bx, yy);
            pv_unpack(d[2 * i + 1], &rows[k * bw + bx]);
        }
    }
    u32 W = bw * BW;
    for (u32 bx = 0; bx < bw; bx++) {
        u32 mod = d[2 * pv_twiddle(bw, bh, bx, by)], col = d[2 * pv_twiddle(bw, bh, bx, by) + 1];
        int punch = col & 1;
        for (int py = 0; py < 4; py++) {
            int ty = py < 2 ? 0 : 1, fy = (py + 2) & 3;           /* rows (ty,ty+1) of {prev,cur,next} */
            for (u32 px = 0; px < BW; px++) {
                int fx = (px + BW / 2) % BW;
                u32 lx = px < BW / 2 ? (bx + bw - 1) % bw : bx, rx = (lx + 1) % bw;
                const pcol *q[4] = { &rows[ty * bw + lx], &rows[ty * bw + rx], &rows[(ty + 1) * bw + lx], &rows[(ty + 1) * bw + rx] };
                int w[4] = { (int)(BW - fx) * (4 - fy), fx * (4 - fy), (int)(BW - fx) * fy, fx * fy };
                int m = (mod >> (2 * (py * 4 + px))) & 3;
                int mw = punch ? (m == 0 ? 0 : m == 3 ? 8 : 4) : (m == 0 ? 0 : m == 1 ? 3 : m == 2 ? 5 : 8);
                u8 *o = out + ((u32)py * W + bx * BW + px) * 4;
                for (int c = 0; c < 4; c++) {
                    int A = 0, B = 0;
                    for (int k = 0; k < 4; k++) { A += q[k]->a[c] * w[k]; B += q[k]->b[c] * w[k]; }
                    u32 v = (u32)(A * (8 - mw) + B * mw), mx = c < 3 ? 31 : 15;
                    o[c] = (c == 3 && !hasAlpha) ? 255 : (c == 3 && punch && m == 2) ? 0 : (u8)((v * 255 + mx * BW * 16) / (mx * BW * 32));
                }
            }
        }
    }
}
