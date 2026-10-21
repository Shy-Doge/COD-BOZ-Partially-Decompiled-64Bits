#!/usr/bin/env python3
"""Runs the injected hook (exactly as patched into the image) on an emulated ARM64 CPU.
   pip install unicorn numpy texture2ddecoder
   python3 tools/patch_ipa.py <ipa> --assets /tmp/x --skip-dz --keep-raw patched.raw
   python3 tools/hook/test_hook_emulated.py patched.raw
Tests: PVRTC 4bpp decode vs an independent decoder, 2bpp pass-through, GL extension filter, config.txt parsing and overrides."""
import json, os, struct, sys
import numpy as np, texture2ddecoder as t
from unicorn import *
from unicorn.arm64_const import *

if len(sys.argv) < 2: sys.exit(__doc__)
raw = open(sys.argv[1], 'rb').read(); f = struct.unpack_from('<18I', raw, 4); cs = f[4]; code = raw[cs:cs + f[16]]
SYM = {k: int(v, 0) for k, v in json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'hook.json')))['symbols'].items()}
BASE = 0x10000000; ok_all = True
STUB = dict(TexImage2D=0x5dc, TexSubImage2D=0xd3c, FileOpen=0x57c, FileRead=0x9bc, FileClose=0x51c)
SLOT = dict(CompTexImage2D=0x527388, GetString=0x5273b0, ConfigGetString=0x527778, ConfigGetInt=0x5273b8)
CFG, EXTBUF = 0x5e7df0, 0x5e5df0
FAKE = BASE + 0x680000          # fake "original" implementation target

def run(entry, args, files=None, orig=None, stub_hooks=()):
    mu = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    mu.mem_map(BASE, 0x700000 + 0x10000); mu.mem_write(BASE, code)
    mu.mem_map(0x70000000, 0x400000); sp = 0x70000000 + 0x3f0000
    for i, a in enumerate(args): mu.reg_write(UC_ARM64_REG_X0 + i, a)
    mu.reg_write(UC_ARM64_REG_SP, sp); LR = BASE + 0x6f0000; mu.reg_write(UC_ARM64_REG_LR, LR)
    log = []; handles = {}
    def cstr(uc, p): return bytes(uc.mem_read(p, 200)).split(b'\0')[0].decode('latin1')
    def hk(uc, addr, size, ud):
        off = addr - BASE; x = [uc.reg_read(UC_ARM64_REG_X0 + i) for i in range(5)]; ret = 0
        if off == STUB['FileOpen']:
            path = cstr(uc, x[0]); log.append(('open', path)); ret = 0
            for p, data in (files or {}).items():
                if path == 'raw://' + p: handles[len(handles) + 1] = data; ret = len(handles)
        elif off == STUB['FileRead']:
            data = handles[x[3]]; n = min(len(data), x[1] * x[2]); uc.mem_write(x[0], data[:n]); ret = n
        elif off == STUB['FileClose']: ret = 0
        elif off in (STUB['TexImage2D'], STUB['TexSubImage2D']):
            name = 'TexImage2D' if off == STUB['TexImage2D'] else 'TexSubImage2D'
            if name == 'TexSubImage2D':
                sp_ = uc.reg_read(UC_ARM64_REG_SP); px = struct.unpack('<Q', uc.mem_read(sp_, 8))[0]
                log.append((name, x, bytes(uc.mem_read(px, x[4] * x[5] * 4))))
            else: log.append((name, x, None))
        else:   # fake original function
            log.append(('orig', off, x)); 
            if orig: ret = orig(uc, x)
        uc.reg_write(UC_ARM64_REG_X0, ret); uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_LR))
    for k in ('FileOpen', 'FileRead', 'FileClose', 'TexImage2D', 'TexSubImage2D'): mu.hook_add(UC_HOOK_CODE, hk, None, BASE + STUB[k], BASE + STUB[k])
    mu.hook_add(UC_HOOK_CODE, hk, None, FAKE, FAKE)
    for s in SLOT.values(): mu.mem_write(BASE + s, struct.pack('<Q', FAKE))        # every slot -> the fake original
    mu.emu_start(BASE + entry, LR)
    return log, mu

def check(name, cond, extra=''):
    global ok_all; ok_all &= bool(cond); print(('PASS ' if cond else 'FAIL ') + name + (('  ' + extra) if extra else ''))

# ---------- PVRTC ----------
def run_decode(w, h, fmt, seed):
    np.random.seed(seed); bw = max(w // 4, 2); bh = max(h // 4, 2); d = np.random.bytes(bw * bh * 8)
    mu = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    mu.mem_map(BASE, 0x710000); mu.mem_write(BASE, code); mu.mem_map(0x70000000, 0x400000)
    mu.mem_write(BASE + 0x600000, d)
    for i, a in enumerate([0x0de1, 0, fmt, w, h, 0, len(d), BASE + 0x600000]): mu.reg_write(UC_ARM64_REG_X0 + i, a)
    mu.reg_write(UC_ARM64_REG_SP, 0x70000000 + 0x3f0000); LR = BASE + 0x6f0000; mu.reg_write(UC_ARM64_REG_LR, LR)
    calls = []
    def hk(uc, addr, size, ud):
        off = addr - BASE; x = [uc.reg_read(UC_ARM64_REG_X0 + i) for i in range(8)]
        if off == STUB['TexSubImage2D']:
            sp_ = uc.reg_read(UC_ARM64_REG_SP); px = struct.unpack('<Q', uc.mem_read(sp_, 8))[0]; calls.append(('sub', x, bytes(uc.mem_read(px, x[4] * x[5] * 4))))
        else: calls.append(('img' if off == STUB['TexImage2D'] else 'orig', x, None))
        uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_LR))
    for off in (STUB['TexImage2D'], STUB['TexSubImage2D']): mu.hook_add(UC_HOOK_CODE, hk, None, BASE + off, BASE + off)
    mu.mem_write(BASE + SLOT['CompTexImage2D'], struct.pack('<Q', BASE + 0x680000)); mu.hook_add(UC_HOOK_CODE, hk, None, BASE + 0x680000, BASE + 0x680000)
    mu.emu_start(BASE + SYM['hook_CompTexImage2D'], LR)
    return d, bw, bh, calls

for w, h, fmt, seed in ((64, 64, 0x8c02, 1), (128, 32, 0x8c02, 2), (8, 8, 0x8c00, 3), (4, 4, 0x8c00, 4), (2, 2, 0x8c00, 5), (512, 256, 0x8c02, 6), (1024, 1024, 0x8c00, 7)):
    d, bw, bh, calls = run_decode(w, h, fmt, seed)
    img = np.zeros((h, w, 4), np.uint8)
    for kind, x, buf in calls[1:]: img[x[3]:x[3] + x[5], :x[4], :] = np.frombuffer(buf, np.uint8).reshape(x[5], x[4], 4)
    ref = np.frombuffer(t.decode_pvrtc(d, bw * 4, bh * 4, False), np.uint8).reshape(bh * 4, bw * 4, 4)[:h, :w, [2, 1, 0, 3]]
    df = np.abs(img.astype(int) - ref.astype(int))
    check('pvrtc %dx%d fmt %#x' % (w, h, fmt), calls[0][0] == 'img' and df[..., :3].max() <= 2 and (fmt == 0x8c00 or df[..., 3].max() <= 2), 'max diff %d' % df[..., :3].max())
d, bw, bh, calls = run_decode(64, 64, 0x8c03, 1); check('2bpp passes through to the original', calls[0][0] == 'orig' and len(calls) == 1)
d, bw, bh, calls = run_decode(16, 16, 0x8D64, 1); check('ETC1 passes through to the original', calls[0][0] == 'orig' and len(calls) == 1)

# ---------- GL extension string ----------
EXT = b"GL_OES_blend_subtract GL_AMD_compressed_ATC_texture GL_OES_compressed_ETC1_RGB8_texture GL_OES_depth24 GL_EXT_texture_compression_s3tc GL_OES_packed_depth_stencil \0"
mu = Uc(UC_ARCH_ARM64, UC_MODE_ARM); mu.mem_map(BASE, 0x710000); mu.mem_write(BASE, code); mu.mem_map(0x70000000, 0x400000)
mu.mem_write(BASE + 0x600000, EXT); mu.mem_write(BASE + SLOT['GetString'], struct.pack('<Q', FAKE))
def hk_gs(uc, a, s, u): uc.reg_write(UC_ARM64_REG_X0, BASE + 0x600000); uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_LR))
mu.hook_add(UC_HOOK_CODE, hk_gs, None, FAKE, FAKE); mu.reg_write(UC_ARM64_REG_X0, 0x1F03); mu.reg_write(UC_ARM64_REG_SP, 0x703f0000); mu.reg_write(UC_ARM64_REG_LR, BASE + 0x6f0000)
mu.emu_start(BASE + SYM['hook_GetString'], BASE + 0x6f0000)
s = bytes(mu.mem_read(mu.reg_read(UC_ARM64_REG_X0), 300)).split(b'\0')[0].decode()
check('extension string filtered + pvrtc added', s == 'GL_OES_blend_subtract GL_OES_depth24 GL_OES_packed_depth_stencil GL_IMG_texture_compression_pvrtc ', s)

# ---------- config.txt ----------
P1 = '/sdcard/BlackOpsZombies/config.txt'; P2 = '/sdcard/Android/data/com.activision.boz/files/config.txt'
def orig_str(uc, x):
    uc.mem_write(x[2], b'ORIGINAL\0'); return 0
def orig_int(uc, x):
    uc.mem_write(x[2], struct.pack('<i', 0)); return 0
def cfg_case(text, path=P1, query=None):
    files = {path: text} if text is not None else {}
    res = {}
    for key, name, kind in (('lsg', 'LSGServer', 's'), ('auth', 'AuthServer', 's'), ('stun', 'STUNServer', 's'), ('other', 'SomethingElse', 's'), ('voice', 'VoiceChatEnabled', 'i'), ('otherint', 'MemSize', 'i')):
        # each call is a separate emulation, but they share nothing: that also proves config loads lazily in any entry hook
        mu_log = None
        sec = BASE + 0x610000; nm = BASE + 0x610100; out = BASE + 0x610200
        def prep(mu):
            mu.mem_write(sec, b'Demonware\0'); mu.mem_write(nm, name.encode() + b'\0'); mu.mem_write(out, b'\0' * 64)
        entry = SYM['hook_ConfigGetString'] if kind == 's' else SYM['hook_ConfigGetInt']
        mu = Uc(UC_ARCH_ARM64, UC_MODE_ARM); mu.mem_map(BASE, 0x710000); mu.mem_write(BASE, code); mu.mem_map(0x70000000, 0x400000); prep(mu)
        handles = {}
        def hk(uc, addr, size, ud, kind=kind):
            off = addr - BASE; x = [uc.reg_read(UC_ARM64_REG_X0 + i) for i in range(4)]; ret = 0
            if off == STUB['FileOpen']:
                p = bytes(uc.mem_read(x[0], 120)).split(b'\0')[0].decode()
                for fp, data in files.items():
                    if p == 'raw://' + fp: handles[len(handles) + 1] = data; ret = len(handles)
            elif off == STUB['FileRead']:
                data = handles[x[3]]; n = min(len(data), x[1] * x[2]); uc.mem_write(x[0], data[:n]); ret = n
            elif off == FAKE - BASE:
                ret = orig_str(uc, x) if kind == 's' else orig_int(uc, x)
            uc.reg_write(UC_ARM64_REG_X0, ret); uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_LR))
        for off in (STUB['FileOpen'], STUB['FileRead'], STUB['FileClose'], FAKE - BASE): mu.hook_add(UC_HOOK_CODE, hk, None, BASE + off, BASE + off)
        for sl in (SLOT['ConfigGetString'], SLOT['ConfigGetInt']): mu.mem_write(BASE + sl, struct.pack('<Q', FAKE))
        mu.reg_write(UC_ARM64_REG_X0, sec); mu.reg_write(UC_ARM64_REG_X1, nm); mu.reg_write(UC_ARM64_REG_X2, out); mu.reg_write(UC_ARM64_REG_SP, 0x703f0000); mu.reg_write(UC_ARM64_REG_LR, BASE + 0x6f0000)
        mu.emu_start(BASE + entry, BASE + 0x6f0000)
        res[key] = bytes(mu.mem_read(out, 64)).split(b'\0')[0].decode() if kind == 's' else struct.unpack('<i', mu.mem_read(out, 4))[0]
        if key == 'lsg': res['name'] = bytes(mu.mem_read(BASE + 0x5e7df8, 32)).split(b'\0')[0].decode()
    return res

full = b"# my settings\r\n  multiplayer_server = my.server.net:4000  \r\nvoice_chat=1\r\n   # player_name=Ignored\r\nplayer_name = Cool Name-1\r\n"
r = cfg_case(full)
check('config: server host:port -> LSG/Auth', r['lsg'] == 'my.server.net:4000' and r['auth'] == 'my.server.net:4000', r['lsg'])
check('config: server host -> STUN', r['stun'] == 'my.server.net', r['stun'])
check('config: voice_chat=1', r['voice'] == 1)
check('config: player_name applied to the Player-%d buffer', r['name'] == 'Cool Name-1', r['name'])
check('config: other keys pass through', r['other'] == 'ORIGINAL' and r['otherint'] == 0)
r = cfg_case(b"multiplayer_server=boz-online.xubi.org\nvoice_chat=0\n")
check('config: default port 3074 added', r['lsg'] == 'boz-online.xubi.org:3074' and r['stun'] == 'boz-online.xubi.org', r['lsg'])
check('config: voice_chat=0 forces 0 (and default name kept)', r['voice'] == 0 and r['name'] == 'Player-%d', r['name'])
r = cfg_case(b"multiplayer_server=bad host\nplayer_name=100%\nvoice_chat=maybe\n")
check('config: invalid server ignored', r['lsg'] == 'ORIGINAL', r['lsg'])
check('config: name with % rejected', r['name'] == 'Player-%d', r['name'])
r = cfg_case(b"player_name=ThisNameIsWayTooLong\n")
check('config: over-long name rejected', r['name'] == 'Player-%d', r['name'])
r = cfg_case(None)
check('config: no file -> everything passes through', r['lsg'] == 'ORIGINAL' and r['name'] == 'Player-%d')
r = cfg_case(b"player_name=AppDirName\nmultiplayer_server=app.example.org\n", path=P2)
check('config: fallback app-folder path works', r['name'] == 'AppDirName' and r['lsg'] == 'app.example.org:3074')
# the exact text the app writes on first start (BozConfig.kt): server, voice chat off, NO name line
default_file = b"# Black Ops Zombies settings. Read when the game starts; lines starting with # are ignored.\n\n# Multiplayer server: host or host:port (the port defaults to 3074)\nmultiplayer_server=boz-online.xubi.org\n\n# Voice chat flag: 0 = off, 1 = on\nvoice_chat=0\n\n# Player name: up to 13 characters from A-Z a-z 0-9 space _ . -\n# Without a player_name line the game picks its own default name (Player-12345).\n#player_name=MyName\n"
r = cfg_case(default_file)
check('config: the first-run default file is parsed completely (game keeps its own name)', r['name'] == 'Player-%d' and r['lsg'] == 'boz-online.xubi.org:3074' and r['stun'] == 'boz-online.xubi.org' and r['voice'] == 0, str(r))
print('\nALL PASSED' if ok_all else '\nSOME TESTS FAILED'); sys.exit(0 if ok_all else 1)
