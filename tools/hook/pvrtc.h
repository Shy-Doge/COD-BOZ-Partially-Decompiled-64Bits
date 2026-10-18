/* Compact PVRTC1 (4bpp exact, 2bpp approximate) decoder, freestanding, no libc. */
typedef unsigned char u8; typedef unsigned short u16; typedef unsigned int u32; typedef unsigned long long u64; typedef int i32;

typedef struct { u8 ar, ag, ab, aa, br, bg, bb, ba; } pcol;   /* A/B colours: rgb 5-bit, alpha 4-bit */

static u32 pv_twiddle(u32 xs, u32 ys, u32 x, u32 y) {          /* sizes/positions in blocks */
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

static void pv_unpack(u32 c, pcol *o) {
    if (c & 0x8000) { o->ar = (c >> 10) & 31; o->ag = (c >> 5) & 31; u32 b = c & 0x1e; o->ab = b | (b >> 4); o->aa = 15; }
    else { u32 r = (c >> 8) & 15, g = (c >> 4) & 15, b = (c >> 1) & 7;
           o->ar = (r << 1) | (r >> 3); o->ag = (g << 1) | (g >> 3); o->ab = (b << 2) | (b >> 1); o->aa = ((c >> 12) & 7) << 1; }
    if (c & 0x80000000u) { o->br = (c >> 26) & 31; o->bg = (c >> 21) & 31; o->bb = (c >> 16) & 31; o->ba = 15; }
    else { u32 r = (c >> 24) & 15, g = (c >> 20) & 15, b = (c >> 16) & 15;
           o->br = (r << 1) | (r >> 3); o->bg = (g << 1) | (g >> 3); o->bb = (b << 1) | (b >> 3); o->ba = ((c >> 28) & 7) << 1; }
}

/* Decode one row of 4x4 (4bpp) blocks.  rows: caller scratch of 3*bw pcol (prev,cur,next).  out: 4 rows * bw*4 pixels RGBA8. */
#define PV_MAXBW 1024
static void pv_row(const u32 *d /* 2 u32 per block, twiddled */, u32 bw, u32 bh, u32 by, int hasAlpha, int bpp2, pcol *rows, u8 *out) {
    const u32 BW = bpp2 ? 8 : 4;
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
                const pcol *p00 = &rows[ty * bw + lx], *p01 = &rows[ty * bw + rx], *p10 = &rows[(ty + 1) * bw + lx], *p11 = &rows[(ty + 1) * bw + rx];
                int w00 = (BW - fx) * (4 - fy), w01 = fx * (4 - fy), w10 = (BW - fx) * fy, w11 = fx * fy;
                int m, mw;
                if (bpp2) { m = (mod >> (py * 8 + px)) & 1; mw = punch ? 4 : (m ? 8 : 0); m = m * 3; }   /* 2bpp: approximation */
                else { m = (mod >> (2 * (py * 4 + px))) & 3; mw = punch ? (m == 0 ? 0 : m == 3 ? 8 : 4) : (m == 0 ? 0 : m == 1 ? 3 : m == 2 ? 5 : 8); }
                int A[4], B[4];
                A[0] = p00->ar * w00 + p01->ar * w01 + p10->ar * w10 + p11->ar * w11;
                A[1] = p00->ag * w00 + p01->ag * w01 + p10->ag * w10 + p11->ag * w11;
                A[2] = p00->ab * w00 + p01->ab * w01 + p10->ab * w10 + p11->ab * w11;
                A[3] = p00->aa * w00 + p01->aa * w01 + p10->aa * w10 + p11->aa * w11;
                B[0] = p00->br * w00 + p01->br * w01 + p10->br * w10 + p11->br * w11;
                B[1] = p00->bg * w00 + p01->bg * w01 + p10->bg * w10 + p11->bg * w11;
                B[2] = p00->bb * w00 + p01->bb * w01 + p10->bb * w10 + p11->bb * w11;
                B[3] = p00->ba * w00 + p01->ba * w01 + p10->ba * w10 + p11->ba * w11;
                u8 *o = out + ((u32)py * W + bx * BW + px) * 4;
                for (int c = 0; c < 3; c++) { u32 v = (u32)(A[c] * (8 - mw) + B[c] * mw); o[c] = (u8)((v * 255 + 31 * BW * 16) / (31 * BW * 32)); }
                u32 av = (u32)(A[3] * (8 - mw) + B[3] * mw);
                o[3] = hasAlpha ? (u8)((punch && m == 2) ? 0 : (av * 255 + 15 * BW * 16) / (15 * BW * 32)) : 255;
            }
        }
    }
}
