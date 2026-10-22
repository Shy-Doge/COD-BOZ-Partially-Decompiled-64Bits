#!/usr/bin/env python3
"""
patch_ipa.py - build the 64-bit Android game image from YOUR OWN copy of the iOS IPA.

    python3 tools/patch_ipa.py path/to/Call_of_Duty_Black_Ops_Zombies_v1_3_5.ipa

What it does (details in docs/HOW_IT_WORKS.md):
  1. reads the iOS arm64 executable out of the IPA
  2. rebuilds the Marmalade "s3e" image that the iOS deploy tool split into Mach-O sections
     (header/tables in s3e_meta, code in __symbolstub1, data in s3e_data)
  3. decrypts the data section
  4. applies the Android patches (memory sizes and online settings in the embedded ICF, PVRTC texture decoder hook,
     config.txt hooks for server / voice chat / player name)
  5. LZMA-packs it like the loader expects and writes  app/src/main/assets/boz_aarch64.s3e
  6. copies blackops_gles1.dz and blackops_hires.dz from the IPA into app/src/main/assets/

Needs only Python 3.8+ (standard library). Nothing from the game is stored in this repository.
"""

import argparse, hashlib, json, lzma, os, plistlib, shutil, struct, sys, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
HOOK_DIR = os.path.join(HERE, "hook")

# ---- what this script was written and tested against --------------------------------------
SUPPORTED_BUNDLE_ID = "com.activision.callofduty.blackopszombies"
SUPPORTED_VERSION = "1.3.5"
SUPPORTED_CODE_SHA256 = "99ce673b4aef2ae55f3575ce3e8ee808280782c1c9d0a13f8253c163587428a5"  # sha256 of the arm64 code section
# sha256 of the patched image (before LZMA) for builds that were run on a real device
TESTED_OUTPUT_SHA256 = {
    "8e098e14ae09e52f7c5886da0a230335d3d41cf3d6e587c92336ef4775103cd7": "tested on a phone: boots, textures, maps, online room hosting, config.txt (server / voice flag / player name); this is the build WITHOUT the mobile data patch (--keep-mobile-check)",
    "bb99d2e7ba00a2b42bc156687a562604020387730d54674eac104f9e487b0aff": "tested on two phones: boots, online play works over mobile data; LAN lobby crashed on one of them (default options)",
}
DEFAULT_GAME_VERSION = "1. 0. 9"  # what the community server expects (the Android build's version); iOS says 1. 3. 1
CODE_SIZE = 0x4C4000
KNOWN_DATA_KEY = 0x7E6E  # low 16 bits are all that matter (see docs)
DZ_FILES = ["blackops_gles1.dz", "blackops_hires.dz"]

# Embedded ICF edits. Every edit keeps the text length identical, so no header offsets move.
# The iOS config only gives the game its full heap / UI pools when {OS=IPHONE}; on Android it
# would get a 46 MB heap and the game crashes with a null pointer when a map loads.
ICF_EDITS = [
    (b"ConsoleHeapSize=150000", b"ConsoleHeapSize=300000"),
    (
        b"GuiBucketSize=2500000           #",
        b"GuiBucketSize=5000000          #",
    ),  # one space dropped to pay for the extra Flash digit
    (b"FlashBucketSize=8500000", b"FlashBucketSize=11500000"),
    (
        b"MemSize = [GAME] ExtraMemorySize + 46000000",
        b"MemSize = [GAME] ExtraMemorySize + 76000000",
    ),
]
# Online multiplayer. The iOS config has an {OS=ANDROID} block that selects OnlineAccount=OF (OpenFeint, shut down
# years ago) and EnableOF=1, and its [Demonware]/[STUN] sections point at Activision's dead servers. Every online
# and LAN multiplayer option then reports "connection does not support online multiplayer". The Android build
# appends the settings below to its own embedded config; the same is done here. Appended text is processed last,
# so it overrides everything above it. The header offsets are updated for the longer ICF.
ICF_ONLINE = (
    b"\r\n{}\r\n"
    b"[Demonware]\r\nTitleID = 18436\r\nLSGServer=boz-online.xubi.org:3074\r\nAuthServer=boz-online.xubi.org:3074\r\n"
    b"[STUN]\r\nSTUNServer=boz-online.xubi.org\r\nSTUNServerPort=3478\r\n"
    b"[GAME]\r\nOnlineAccount=GENERIC\r\nEnableOF=0\r\nMatchmakingSearchAndPublishMode=1\r\nVoiceChatEnabled=0\r\nDisableAchievementDB=1\r\n"
)
ICF_MDNS_OFF = b"[NETWORK]\r\ncustom_mDNS_impl=0\r\n"
BLOB_OFFSET = 0x4C3180
STUB_HOOKS = {
    0xC2C: "hook_CompTexImage2D",
    0x143C: "hook_CompTexSubImage2D",
    0xC7C: "hook_GetString",
}
# runtime config file (config.txt): hooks on the game's s3eConfigGetString / s3eConfigGetInt import stubs ...
CONFIG_HOOKS = {0x140C: "hook_ConfigGetString", 0xC8C: "hook_ConfigGetInt"}
# ... and the default-name builder  snprintf(buf, 14, "Player-%d", n)  at 0xdd1d4 (adrp x2) / 0xdd1dc (add x2,x2,#0x398):
# its format-string reference is pointed at the name buffer inside the config area (CFG + 8).
NAME_ADRP, NAME_ADD, NAME_BUF = 0xDD1D4, 0xDD1DC, 0x5E7DF8

# Mobile data. The game's "is this connection good enough for online play" test (image offset 0xdb850) returns false when
# the network type value is 0 or 8; the Android loader reports every 3G HSDPA/HSUPA/HSPA connection as 8, and some
# carriers report that subtype even on faster networks. The test at 0xdb888 (tst w19,#0xfffffff7 -> b.ne) is replaced by
# cmp w0,wzr, so any connection the OS reports as available is accepted. Whether online play then works on a carrier
# connection still depends on the carrier's NAT; joining a host that has an open port should work.
NETCHECK_ADDR, NETCHECK_OLD, NETCHECK_NEW = 0xDB888, 0x721C7A7F, 0x6B1F001F
EXTRA_MEMORY = 0x4000


def die(msg):
    print("\nERROR: " + msg, file=sys.stderr)
    sys.exit(1)


# ---- minimal Mach-O reader --------------------------------------------------------------
def macho_arm64_sections(exe):
    magic = struct.unpack_from(">I", exe, 0)[0]
    base = 0
    if magic == 0xCAFEBABE:  # fat binary
        n = struct.unpack_from(">I", exe, 4)[0]
        for i in range(n):
            cputype, _, off, size, _ = struct.unpack_from(">5I", exe, 8 + 20 * i)
            if cputype == 0x0100000C:
                base = off
                break
        else:
            die("no arm64 slice in this executable")
    if struct.unpack_from("<I", exe, base)[0] != 0xFEEDFACF:
        die("arm64 slice is not a 64-bit Mach-O")
    ncmds = struct.unpack_from("<I", exe, base + 16)[0]
    p = base + 32
    secs = {}
    for _ in range(ncmds):
        cmd, size = struct.unpack_from("<II", exe, p)
        if cmd == 0x19:  # LC_SEGMENT_64
            nsects = struct.unpack_from("<I", exe, p + 64)[0]
            q = p + 72
            for _ in range(nsects):
                name = exe[q : q + 16].split(b"\0")[0].decode("latin1")
                sz = struct.unpack_from("<Q", exe, q + 40)[0]
                off = struct.unpack_from("<I", exe, q + 48)[0]
                secs[name] = exe[base + off : base + off + sz]
                q += 80
        p += size
    return secs


def decode_data(enc, key):
    out = bytearray(len(enc))
    k = key
    for i, c in enumerate(enc):
        out[i] = c ^ ((k >> 8) & 0xFF)
        k = ((c + (k & 0xFFFF)) * 0xCE6D + 0x58BF) & 0xFFFFFFFF
    return bytes(out)


def zero_fraction(buf, n=4096):
    s = buf[:n]
    return s.count(0) / max(1, len(s))


def find_key(enc):
    if zero_fraction(decode_data(enc[:4096], KNOWN_DATA_KEY)) > 0.3:
        return KNOWN_DATA_KEY
    print(
        "  known key did not fit, searching all 65536 candidates (takes about a minute)..."
    )
    best = (0, None)
    for key in range(65536):
        z = zero_fraction(decode_data(enc[:192], key), 192)
        if z > best[0]:
            best = (z, key)
    if best[0] < 0.3:
        die("could not find the data key; this IPA is not the supported version")
    return best[1]


def lzma_pack(raw):
    c = bytearray(
        lzma.compress(
            raw,
            format=lzma.FORMAT_ALONE,
            filters=[
                {
                    "id": lzma.FILTER_LZMA1,
                    "dict_size": 64 * 1024,
                    "lc": 3,
                    "lp": 0,
                    "pb": 2,
                }
            ],
        )
    )
    struct.pack_into(
        "<Q", c, 5, len(raw)
    )  # exact uncompressed size - the Marmalade loader wants it
    return bytes(c)


def _encode_type4_entry(slot_offset, import_idx):
    """Encode one type-4 s3e import-slot record.

    Format: u32 (with 16-bit halves swapped) + u16 (import index).
    The u32 is computed by swapping the 16-bit halves of slot_offset:
        v = ((slot_offset & 0xFFFF) << 16) | (slot_offset >> 16)
    """
    v = (slot_offset & 0xFFFF) << 16 | (slot_offset >> 16)
    return struct.pack("<I", v) + struct.pack("<H", import_idx)


# Slot arrays used by the injected hook — each (image_offset, import_index).
# These imports already exist in the name table; the s3e loader just needs a
# type-4 entry telling it WHERE to write the resolved pointer.
# On the unpatched image ALL 407 type-4 entries point at import #82
# (s3eCompressionDecompRead), so the loader never touches these slots.
_HOOK_TYPE4 = [
    (0x527388, 194),  # glCompressedTexImage2D
    (0x527790, 322),  # glCompressedTexSubImage2D
    (0x5273B0, 199),  # glGetString
    (0x527778, 319),  # s3eConfigGetString
    (0x5273B8, 200),  # s3eConfigGetInt
]


def _add_type4_entries(rec):
    """Append type-4 import-slot entries to the record stream and return
    (modified_rec, new_rec_len).  If no type-4 record exists, creates one.
    """
    # Find the existing type-4 record (if any)
    pos = 0
    t4_start = t4_size = None
    while pos + 8 <= len(rec):
        rtype = struct.unpack_from("<I", rec, pos)[0]
        rtotal = struct.unpack_from("<I", rec, pos + 4)[0]
        if rtype == 4:
            t4_start = pos
            t4_size = rtotal
            break
        pos += rtotal
        if pos > len(rec):
            break

    # Encode new entries
    new_bytes = b"".join(_encode_type4_entry(slot, idx) for slot, idx in _HOOK_TYPE4)

    if t4_start is not None:
        # Extend existing type-4 record: replace total size
        new_t4_total = t4_size + len(new_bytes)
        buf = bytearray(rec)
        struct.pack_into("<I", buf, t4_start + 4, new_t4_total)
        buf.extend(new_bytes)
        return bytes(buf), len(buf)
    else:
        # No type-4 record exists; create a new one
        header = struct.pack("<II", 4, 8 + len(new_bytes))
        buf = bytearray(rec + header + new_bytes)
        return bytes(buf), len(buf)


def build_image(
    secs,
    force,
    online=True,
    mdns_off=False,
    game_version=None,
    config_hooks=True,
    mobile_data=True,
):
    meta, code, enc = (
        secs.get("s3e_meta"),
        secs.get("__symbolstub1"),
        secs.get("s3e_data"),
    )
    if not (meta and code and enc):
        die(
            "this executable has no s3e_meta / __symbolstub1 / s3e_data sections - is it the right IPA?"
        )
    code_hash = hashlib.sha256(code).hexdigest()
    print("  code section sha256:", code_hash)
    if code_hash != SUPPORTED_CODE_SHA256:
        msg = (
            "this IPA is not Black Ops Zombies %s (code hash differs). The patch offsets are only valid for that exact build."
            % SUPPORTED_VERSION
        )
        if not force:
            die(msg + " Use --force to try anyway (will almost certainly not work).")
        print("WARNING: " + msg)
    key = find_key(enc)
    print("  data key: 0x%04x" % key)
    data = decode_data(enc, key)

    hoff = struct.unpack_from("<I", meta, 0)[0]
    H = list(struct.unpack_from("<4s18I", meta, hoff))
    if H[0] != b"XE3U":
        die("s3e header magic not found")
    f = H[1:]
    rec_len, code_start, image, memsz = f[3], f[4], f[5], f[6]
    icf_off, icf_len, f_lic_off, lic_len, code_pg = f[10], f[11], f[13], f[14], f[16]
    if code_pg != len(code) or code_pg != CODE_SIZE:
        die("unexpected code size")
    data_len = image - code_pg
    lic_off = struct.unpack_from("<I", meta, 0x28)[0]
    if lic_off != hoff + code_start or f_lic_off != code_start + image:
        die("unexpected s3e layout")
    if hoff + icf_off + icf_len + rec_len != hoff + code_start:
        die("unexpected s3e layout (ICF/records)")
    hdr = bytearray(meta[hoff : hoff + icf_off])  # fixed-size header (0x4c)
    icf = meta[hoff + icf_off : hoff + icf_off + icf_len]
    rec = meta[hoff + icf_off + icf_len : hoff + code_start]  # record stream
    lic = meta[lic_off : lic_off + lic_len]

    # --- add type-4 import-slot entries for GL functions used by the hook ---
    rec, rec_len = _add_type4_entries(rec)
    struct.pack_into("<I", hdr, 0x10, rec_len)  # record stream length in header
    rec_growth = len(rec) - (
        hoff + code_start - (hoff + icf_off + icf_len)
    )  # bytes added to record stream
    code_start += rec_growth  # shift code start to match the expanded record stream

    # --- patch 1: embedded ICF: memory sizes (length-preserving edits) ---
    for old, new in ICF_EDITS:
        if icf.count(old) != 1:
            die("ICF text differs from the supported build (%r)" % old[:30])
        icf = icf.replace(old, new)

    # --- patch 1a: append the repo's {OS=ANDROID} overrides onto the base ICF ---
    repo_icf_path = os.path.join(
        REPO, "app", "src", "main", "assets", "game_config.icf"
    )
    with open(repo_icf_path, "r") as f:
        repo_text = f.read()
    repo_text = repo_text.replace("\r\n", "\n").replace("\n", "\r\n")
    repo_icf = repo_text.encode("latin-1")

    # Add only the texture/display settings from the repo config, without the full
    # {OS=ANDROID} overlay that includes nested conditionals and causes the LAN crash.
    android_lines = [
        b"[S3E]",
        b'DispFixRot="FixedLandscape"',
        b"SysGlesVersion=2",
        b"[GX]",
        b"Rasterisation=HW",
        b"DataCacheSizeHW=5000000",
        b"[GAME]",
        b"UseAnyAssetType=1",
        b"EnableAndroidMarketBilling=1",
        b"[SYSTEM]",
        b"OverrideSupportKeyBindings=1",
    ]
    if android_lines:
        icf += b"\r\n".join(android_lines) + b"\r\n\r\n"

    # --- patch 1b: online multiplayer settings appended to the ICF ---
    if online:
        icf += ICF_ONLINE
        if game_version:  # experimental: version string sent to the matchmaking server
            if len(game_version) > 16 or not all(
                32 <= ord(ch) < 127 and ch != '"' for ch in game_version
            ):
                die("bad --game-version")
            icf += b'[GAME]\r\nGameVersion="' + game_version.encode() + b'"\r\n'
        if mdns_off:
            icf += ICF_MDNS_OFF
    delta = len(icf) - icf_len
    if delta:  # header fields that depend on the ICF length
        struct.pack_into("<I", hdr, 0x0C, icf_off + len(icf))  # end of ICF
        struct.pack_into("<I", hdr, 0x14, code_start + delta)  # code start
        struct.pack_into("<I", hdr, 0x30, len(icf))  # ICF length
        struct.pack_into("<I", hdr, 0x38, f_lic_off + delta)  # license block offset
    img = bytearray(bytes(hdr) + icf + rec + code + data[:data_len] + lic)
    if len(img) != f_lic_off + delta + lic_len + rec_growth:
        die("image size mismatch")
    code_start += delta  # where the code now starts inside img

    # --- patch 2: PVRTC decoder hook injected into the unused tail of the code section ---
    blob = open(os.path.join(HOOK_DIR, "hook.bin"), "rb").read()
    sym = json.load(open(os.path.join(HOOK_DIR, "hook.json")))["symbols"]
    cs = code_start
    if any(img[cs + BLOB_OFFSET : cs + BLOB_OFFSET + 0xE80]):
        die("code tail is not empty")
    if BLOB_OFFSET + len(blob) > code_pg:
        die("hook does not fit")
    img[cs + BLOB_OFFSET : cs + BLOB_OFFSET + len(blob)] = blob
    for stub, name in STUB_HOOKS.items():
        old = struct.unpack_from("<I", img, cs + stub)[0]
        if (old & 0x9F000000) != 0x90000000:
            die("GL stub at 0x%x has unexpected code" % stub)
        struct.pack_into(
            "<I",
            img,
            cs + stub,
            0x14000000 | (((int(sym[name], 0) - stub) >> 2) & 0x3FFFFFF),
        )
    if config_hooks:
        for stub, name in CONFIG_HOOKS.items():
            old = struct.unpack_from("<I", img, cs + stub)[0]
            if (old & 0x9F000000) != 0x90000000:
                die("config stub at 0x%x has unexpected code" % stub)
            struct.pack_into(
                "<I",
                img,
                cs + stub,
                0x14000000 | (((int(sym[name], 0) - stub) >> 2) & 0x3FFFFFF),
            )
        w = struct.unpack_from("<I", img, cs + NAME_ADRP)[0]
        imm = (((w >> 5) & 0x7FFFF) << 2 | ((w >> 29) & 3)) << 12
        if (w & 0x9F00001F) != 0x90000002 or ((NAME_ADRP & ~0xFFF) + imm) != 0x46C000:
            die('"Player-%d" reference (adrp) not as expected')
        a = struct.unpack_from("<I", img, cs + NAME_ADD)[0]
        if (a & 0xFFC003FF) != 0x91000042 or ((a >> 10) & 0xFFF) != 0x398:
            die('"Player-%d" reference (add) not as expected')
        pg = ((NAME_BUF & ~0xFFF) - (NAME_ADRP & ~0xFFF)) >> 12
        struct.pack_into(
            "<I",
            img,
            cs + NAME_ADRP,
            0x90000002 | ((pg & 3) << 29) | (((pg >> 2) & 0x7FFFF) << 5),
        )
        struct.pack_into(
            "<I", img, cs + NAME_ADD, 0x91000042 | ((NAME_BUF & 0xFFF) << 10)
        )
    if mobile_data:
        if struct.unpack_from("<I", img, cs + NETCHECK_ADDR)[0] != NETCHECK_OLD:
            die("network type test not as expected")
        struct.pack_into("<I", img, cs + NETCHECK_ADDR, NETCHECK_NEW)
    struct.pack_into(
        "<I", img, 0x1C, memsz + EXTRA_MEMORY
    )  # header memory size: room for the filtered extension string
    return bytes(img)


def main():
    ap = argparse.ArgumentParser(
        description="Build boz_aarch64.s3e and copy the .dz data from your own IPA."
    )
    ap.add_argument(
        "ipa",
        help="path to Call of Duty Black Ops Zombies v1.3.5 .ipa (you must provide it)",
    )
    ap.add_argument(
        "--assets",
        default=os.path.join(REPO, "app", "src", "main", "assets"),
        help="output folder (default: app/src/main/assets)",
    )
    ap.add_argument(
        "--skip-dz", action="store_true", help="do not copy the large .dz data files"
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="continue even if the IPA is not the supported version",
    )
    ap.add_argument(
        "--no-online",
        action="store_true",
        help="do not add the online multiplayer settings (custom server, generic account)",
    )
    ap.add_argument(
        "--custom-mdns",
        action="store_true",
        help="experimental: keep the game's own mDNS for LAN discovery (it crashes in the LAN lobby on the tested phone)",
    )
    ap.add_argument(
        "--game-version",
        metavar="STR",
        default=DEFAULT_GAME_VERSION,
        help='GameVersion sent to the server (default "%s")' % DEFAULT_GAME_VERSION,
    )
    ap.add_argument(
        "--ios-game-version",
        action="store_true",
        help="keep the iOS GameVersion string (1. 3. 1) instead of the server one",
    )
    ap.add_argument(
        "--no-config-hooks",
        action="store_true",
        help="do not install the config.txt hooks (server / voice chat / player name)",
    )
    ap.add_argument(
        "--keep-mobile-check",
        action="store_true",
        help="keep the game's check that refuses some mobile-data connections",
    )
    ap.add_argument(
        "--keep-raw",
        metavar="FILE",
        help="also write the unpacked image here (debugging)",
    )
    a = ap.parse_args()

    if not os.path.isfile(a.ipa):
        die("file not found: " + a.ipa)
    if not zipfile.is_zipfile(a.ipa):
        die("not a valid IPA/zip file")
    os.makedirs(a.assets, exist_ok=True)
    print("[1/4] reading", a.ipa)
    with zipfile.ZipFile(a.ipa) as z:
        apps = sorted(
            {
                n.split("/")[1]
                for n in z.namelist()
                if n.startswith("Payload/") and n.split("/")[1].endswith(".app")
            }
        )
        if not apps:
            die("no Payload/*.app in this IPA")
        app = "Payload/%s/" % apps[0]
        info = plistlib.loads(z.read(app + "Info.plist"))
        print(
            "  app: %s  bundle: %s  version: %s"
            % (apps[0], info.get("CFBundleIdentifier"), info.get("CFBundleVersion"))
        )
        if info.get("CFBundleIdentifier") != SUPPORTED_BUNDLE_ID:
            die("this is not Black Ops Zombies (bundle id mismatch)")
        if str(info.get("CFBundleVersion")) != SUPPORTED_VERSION and not a.force:
            die(
                "IPA version %s, but this patch supports %s only (--force to try anyway)"
                % (info.get("CFBundleVersion"), SUPPORTED_VERSION)
            )
        exe = z.read(app + info["CFBundleExecutable"])

        print("[2/4] rebuilding and patching the 64-bit image")
        secs = macho_arm64_sections(exe)
        img = build_image(
            secs,
            a.force,
            online=not a.no_online,
            mdns_off=not a.custom_mdns,
            game_version=None if a.ios_game_version else a.game_version,
            config_hooks=not a.no_config_hooks,
            mobile_data=not a.keep_mobile_check,
        )
        out_sha = hashlib.sha256(img).hexdigest()
        print(
            "  patched image sha256:",
            out_sha,
            "-",
            TESTED_OUTPUT_SHA256.get(
                out_sha, "NOT YET VERIFIED ON A DEVICE (new combination of options)"
            ),
        )
        if a.keep_raw:
            open(a.keep_raw, "wb").write(img)

        print("[3/4] writing boz_aarch64.s3e")
        dst = os.path.join(a.assets, "boz_aarch64.s3e")
        packed = lzma_pack(img)
        open(dst, "wb").write(packed)
        print("  %s (%d bytes)" % (dst, len(packed)))

        if a.skip_dz:
            print("[4/4] skipped .dz data (--skip-dz)")
        else:
            print("[4/4] copying game data archives (large, this takes a while)")
            for name in DZ_FILES:
                try:
                    info_ = z.getinfo(app + name)
                except KeyError:
                    die("%s is missing from the IPA" % name)
                target = os.path.join(a.assets, name)
                if (
                    os.path.exists(target)
                    and os.path.getsize(target) == info_.file_size
                ):
                    print("  %s already present, skipping" % name)
                    continue
                with z.open(info_) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out, 1 << 20)
                print("  %s (%.0f MB)" % (name, info_.file_size / 1e6))
    print("\nDone. Now build:  ./gradlew :app:assembleDebug   (see README.md)")


if __name__ == "__main__":
    main()
