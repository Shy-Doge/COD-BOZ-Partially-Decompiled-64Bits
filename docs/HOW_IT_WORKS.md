# How the 64-bit port works

Everything here was found by static analysis of the iOS build and of the Android loader, then confirmed on a real device (Galaxy S25 Ultra, Android 15). Where something is an inference and not a measured fact it says so.

## 1. The problem

The Android build of Black Ops Zombies ships its game code as `boz.s3e`, compiled for **32-bit ARM**. Current phones have 64-bit-only CPUs. The iOS build contains a **64-bit ARM** version of the same game on the same engine (Marmalade). The Android loader in this repo (`libs3e_android.so`, Marmalade 8.6.0) can load 64-bit game code; it just never had a 64-bit `.s3e` for this game. So the job is to turn the iOS arm64 code into a `.s3e` the Android loader accepts, then fix what differs between the two platforms.

## 2. The iOS binary

`blackops` inside the IPA is a fat Mach-O (armv7 + arm64). The arm64 slice is not encrypted. It is a Marmalade build: the deploy tool took the game's `.s3e` image and **split it across Mach-O sections** instead of keeping a separate file:

| Part of the s3e image | Where it lives in the Mach-O |
|---|---|
| header, ICF text, import/relocation records, license block | section `s3e_meta` |
| code (0x4c4000 bytes) | section `__symbolstub1` |
| data (0x75578 bytes) | section `s3e_data` (encrypted) |

### 2.1 s3e file layout

```
[header 0x4c][ICF text][record stream][code][data][signature block (Android only)][license block]
```

Header, little-endian 32-bit words (offsets from the start of the header):

| Offset | Meaning | iOS value |
|---|---|---|
| +0x00 | magic `XE3U` | |
| +0x04 | S3E version (major<<16 \| minor<<8 \| patch) = 4.40.0 | 0x42800 |
| +0x08 | SDK/format version | 0x010f001a |
| +0x0c | end of ICF | 0x41ac |
| +0x10 | byte length of the record stream (starts right after the ICF) | 0x33118 |
| +0x14 | offset where the code starts | 0x372c4 |
| +0x18 | image size = code + data | 0x539578 |
| +0x1c | memory size including BSS | 0x5e5de4 |
| +0x20 / +0x24 | signature offset / length (0 = none) | 0 / 0 |
| +0x2c / +0x30 | ICF offset / length | 0x4c / 0x4160 |
| +0x34 | link base address | 0x4a000000 |
| +0x38 / +0x3c | license block offset / length | 0x57083c / 0x124 |
| +0x44 | code size, page aligned | 0x4c4000 |

Consistency checks that hold on both the Android 32-bit file and the iOS file: `code start + image size = license offset`, and the iOS loader's own parsing code (`IwS3ERead`) reads the same fields.

### 2.2 The record stream

Both loaders walk the same list of records, `{u32 type, u32 size, payload}`:

| Type | Content | iOS file |
|---|---|---|
| 0 | import names (NUL separated) | 395 names |
| 1 | rebase list: offsets of 64-bit pointers to which `load_base - link_base` is added; offsets below the code size are skipped | 50,032 entries, all in the data section |
| 2 | small record handled by the external-relocation routine | 8-byte payload |
| 4 | import slots: where to write the address of import N | 406 entries, 6 bytes each |

A type 4 entry is `u32 + u16`. The two 16-bit halves of the `u32` are swapped (offset = `(v & 0xffff) << 16 | v >> 16`) and the `u16` is the index into the name list. The Android 64-bit loader dispatches these types exactly like the iOS loader (verified by disassembling both), so **no relocation conversion is needed**. An earlier assumption that the formats were incompatible was wrong.

### 2.3 Data decryption

`s3e_data` is encrypted by a ciphertext-feedback stream cipher implemented in the iOS loader (`s3eDecodeData`):

```
plain = cipher ^ ((key >> 8) & 0xff)
key   = ((cipher + (key & 0xffff)) * 0xce6d + 0x58bf) & 0xffffffff
```

The start key comes from a hash of the code section on iOS, but only its low 16 bits matter, so it can be recovered by trying all 65,536 values and scoring the fraction of zero bytes (the correct one gives about 54% zeros). The key for this build is `0x7e6e`. The decrypted data starts with 64-bit pointers based at `0x4a000000`, which matches the header's link base.

## 3. Rebuilding the image

`tools/patch_ipa.py` concatenates `s3e_meta[header..end of record stream]`, the code, the decrypted data (trimmed to image size minus code size) and the license block, then asserts the total equals the size the header promises. The Android loader accepts it as is: its signature check is disabled in the loader shipped in this repo (the flag byte is `0xff`), the version field matches, and all 395 imports exist in the loader.

The finished file is LZMA-packed (`lzma alone`, 64 KB dictionary) with the exact uncompressed size written in the header, because the loader needs a sized stream, not a streamed one.

## 4. Things that had to be fixed

### 4.1 Game data
The iOS code expects its own data archives (`blackops_gles1.dz`, `blackops_hires.dz`) as app assets. Without them the game crashes during startup (a lookup returns null and is dereferenced). The Android archives are not used.

### 4.2 Memory settings (the "crash when selecting a map" bug)
The embedded ICF applies the full heap and UI pool sizes only inside a `{OS=IPHONE}` block. On Android that block is false, so the game ran with a 46.5 MB heap. When a map loads, an allocation fails, the allocator returns null (the game is built without exceptions), and a small constructor writes to address `0x8`. `patch_ipa.py` edits four values in place with identical lengths (so no header offset changes): `MemSize` 46000000 to 76000000, `GuiBucketSize` 2500000 to 5000000, `FlashBucketSize` 8500000 to 11500000, `ConsoleHeapSize` 150000 to 300000. The new values equal what the game gets on iOS.

### 4.3 Textures (PVRTC)
The iOS data uses PVRTC textures. Adreno GPUs cannot use PVRTC, and the game only takes its PVRTC path if the driver advertises it. `tools/hook/` contains a freestanding AArch64 blob (3.7 KB, built from `hook.c`) injected into the unused end of the game's code section (image offset `0x4c3180`, exactly 3,712 bytes are free; it also contains the settings file code of 4.5). The game calls GL through small stubs at the start of its code that jump through loader-filled slots, so three of those stubs are overwritten with a branch to the hook:

* `glCompressedTexImage2D` and `glCompressedTexSubImage2D`: PVRTC 4bpp formats (0x8C00 RGB, 0x8C02 RGBA) are decoded to RGBA8888 and uploaded with `glTexImage2D` / `glTexSubImage2D` one block row at a time. All other formats, including 2bpp PVRTC (0x8C01, 0x8C03), pass straight through. 4bpp decoding matches an independent reference decoder within 2/255. An earlier version approximated 2bpp; that was removed to make room for the settings file code (it was never checked against a reference).
* `glGetString(GL_EXTENSIONS)`: returns the driver list without any "compress" extension plus `GL_IMG_texture_compression_pvrtc`, which makes the game choose its PVRTC data set. Without this the decoder is never called and nothing is textured.

The filtered string needs a persistent buffer, so the header's memory size grows by 0x4000 bytes and the hook uses the first 0x2000 of them. Because the image is linked at a fixed base the injected code uses only PC-relative addressing and needs no relocations.

Both the decoder and the hook were run, bit-exact as shipped, in an ARM64 emulator against `texture2ddecoder` (`tools/hook/test_hook_emulated.py`) before they were ever run on a phone.

### 4.4 Online multiplayer settings
The iOS ICF has an `{OS=ANDROID}` block that is active on Android. It selects `OnlineAccount=OF` and `EnableOF=1` (OpenFeint, long shut down), and its `[Demonware]` / `[STUN]` sections name Activision's retired servers. With no usable account every online and LAN option reports "your current connection does not support online multiplayer".

The game's account type function (image offset `0x11e6b0`) reads `OnlineAccount` and returns 0 for `GC`, 1 for `OF`, 2 for anything else; 21 call sites dispatch on it. The Android build's own embedded ICF ends with an Android-only block that selects `GENERIC` and points the servers at the community server. `patch_ipa.py` appends the equivalent text to the iOS ICF (later settings win): `[Demonware]` `LSGServer`/`AuthServer` = `boz-online.xubi.org:3074`, `[STUN]` server and port 3478, `OnlineAccount=GENERIC`, `EnableOF=0`, `MatchmakingSearchAndPublishMode=1`, `VoiceChatEnabled=0`, `DisableAchievementDB=1`. Because the ICF gets longer, four header fields are recomputed (ICF end `+0x0c`, code start `+0x14`, ICF length `+0x30`, license offset `+0x38`). The patcher also asserts the full layout is consistent afterwards.

`GameVersion` is set to `1. 0. 9`. With the iOS value `1. 3. 1` joining rooms failed; with `1. 0. 9` hosting a room worked and the session stayed connected (device test).

What is still missing, and why (partly inferred):
* **LAN.** In tests with the default settings, a Galaxy S24 Ultra crashed in the LAN lobby and an Android 7 phone opened the lobby without crashing (neither found the other). With `--custom-mdns` both crashed. No crash log has been analysed yet. Background: the `{OS=ANDROID}` block also sets `custom_mDNS_impl=1`, which makes the game use its own mDNS code (`i3d_mDNS`, `CIwNetworkDiscoveryBonjour`, multicast `224.0.0.251`). That path crashes in the LAN lobby on the tested phone (SIGSEGV at fault address 0x1; no register dump was captured, so the cause is not known). `patch_ipa.py` therefore sets `custom_mDNS_impl=0`, which falls back to the `s3eZeroConf` extension; that extension does not exist for arm64 here, so the lobby opens but no other device finds it.
* **Player name.** The game has no screen for it with this account type; see 4.5 for how it is set.

### 4.5 Settings file (server, voice chat, player name)
The game builds its default player name with `snprintf(buf, 14, "Player-%d", n)` (in the account/online setup code, the format reference is at image offset `0xdd1dc`) and there is no screen for changing it. The community port of the Android build (Producdevity/cod-boz-port) handles this by pointing the format string at the configured name; the same is done here. Server hostnames and the voice chat flag come from the ICF through the game's `s3eConfigGetString` / `s3eConfigGetInt` imports.

The injected blob hooks the two config import stubs (`0x140c`, `0xc8c`) and the format-string reference (the `adrp`/`add` pair at `0xdd1d4`/`0xdd1dc` is rewritten to point at a name buffer). On the first config read it opens `raw:///sdcard/BlackOpsZombies/config.txt`, then `raw:///sdcard/Android/data/com.activision.boz/files/config.txt`, through the game's own `s3eFileOpen` / `s3eFileRead` / `s3eFileClose` stubs, parses `key=value` lines and stores the results in the second half of the extra BSS. `multiplayer_server` replaces `LSGServer` and `AuthServer` (`host:port`, port defaults to 3074) and `STUNServer` (host only); `voice_chat` replaces `VoiceChatEnabled`; `player_name` (1 to 13 of `A-Za-z0-9 _.-`, so never a `%`) replaces the format string. Every value is validated; invalid lines are ignored.

The app already requests the "all files access" permission at startup (`MANAGE_EXTERNAL_STORAGE`) and holds back the native loader until it is granted (`IsDeviceActivity.onStart`), which is why a shared folder is readable. `BozConfig.ensureDefaultConfig()` is called there, after the check and before `super.onStart()` starts the engine, so the file exists before the game's first config read. It writes `/sdcard/BlackOpsZombies/config.txt` only if it does not exist: community server and `voice_chat=0`, with the name line commented out so the game keeps its own default name. The application folder under `Android/data` is the fallback when the shared file does not exist.

Tested on a phone: with no file the behaviour is unchanged and the name is `Player-12345`; with a file the name, server override and voice flag take effect; a wrong server makes online play fail without crashing. The parser is also covered by `tools/hook/test_hook_emulated.py` (valid and invalid files, line endings, comments, both paths, pass-through of every other key). Whether voice chat audio works is not verified; the voice flag is only passed on.

### 4.6 How online play works and why joining can fail
Strings in the game data (the localisation table at the end of `blackops_gles1.dz`) say that online services use TCP 3074 and UDP 3478 (lobby, auth and STUN on the community server) and that game play uses UDP 50117 and 50118, mapped on the router with UPnP when possible ("Port Forwarding (uPnP) is fully supported"). The game contains a UPnP / NAT-PMP state machine (image offset `0x6190`). A client connects to the host's public address, so two phones behind the same router only work if that router supports NAT loopback ("hairpinning"). The in-game help also states that multiplayer is only possible between equal game versions.

**Mobile data.** The error "Your current connection does not support online multiplayer." comes from error code 10 of the online prerequisite check (`0xd4690`), which calls the network test at `0xdb850`. Off iOS (OS id 3) that test asks the loader for the network type and availability and accepts the connection only if `type & ~8` is non-zero. The Android loader maps WIFI to 3, ETHERNET to 2, LTE to 14, other types to 1 and every HSDPA/HSUPA/HSPA connection to 8; type 8 is refused. `patch_ipa.py` replaces the `tst w19,#0xfffffff7` at `0xdb888` with `cmp w0,wzr`, so any available connection is accepted. With this patch online play worked over mobile data in a phone test; the carrier's network type was not recorded, so it is not confirmed that type 8 was the cause.

### 4.7 Android project changes
* `app/build.gradle.kts`: `arm64-v8a` in the release ABI filters (duplicate `release` block merged), `.dz` stored uncompressed, the wrong-ABI 32-bit `libs3eGooglePlayServices.so` in `jniLibs/arm64-v8a` excluded from the APK, and the build no longer requires `keystore.properties` or `version.properties`.
* `IsDeviceActivity.kt`: key callbacks go through a helper that ignores the missing native method (`libIsDevice.so` exists only for 32-bit ARM).
* `SoundPlayer.kt`: `AudioTrack.stop()` is wrapped, because modern Android throws if the track never started.
* `gradle.properties`: the AndroidX flag (the project did not build without it).

## 5. How the bugs were located

* The crash dump gives only a pc relative to a mapping. The real image offset was found by matching the register dump against the code: callee-saved registers (`x19`-`x28`) must be consistent with the stack frame of the faulting function. That identified the exact instruction.
* A fault address of exactly `0x0` or `0x8` with a null base register in a leaf constructor means the object pointer itself was null, i.e. a failed allocation, which led to the ICF memory settings.
* "No textures" was bisected with separate builds (decoder only, memory size only, hook with and without the extension filter); only the combination with the extension filter produced textures.

## 6. Verification

`patch_ipa.py` checks the SHA-256 of the code section before doing anything, so it refuses any IPA other than v1.3.5. The script prints the SHA-256 of the patched image (before LZMA) and says whether that exact image was tested on a phone: `8e098e14...3cd7` for the default options. Other option combinations print that they are not verified.

## 7. Known limits

* Only the iOS 1.3.5 build is supported (the patch offsets are build specific).
* 2bpp PVRTC textures are not decoded; textures wider than 2048 px are skipped.
* Textures use RGBA8888 on the GPU, about 8x the memory of PVRTC.
* No 64-bit `libIsDevice.so` / `libIsIOSUtils.so`: key events and some device queries are missing.
* No in-app purchases, Game Center or Android billing (iOS code); Activision's online services are gone, online play uses the community server.
* LAN discovery does not work (see 4.4). Joining another player's online room failed once in a two-phone test, cause unknown (identical default names, same-router NAT loopback or the version string are suspected). The player name is set through the settings file (see 4.5).
* Tested on one device. Audio, controls, saving and long sessions have had little testing.

## 8. Porting a different Marmalade game

The method is generic: find `s3e_meta` / `s3e_data`, reassemble, find the data key, compare the embedded ICF with the Android one, and check the target loader's version and signature settings. The hook offsets (stubs, slots, tail address) are specific to this binary and must be recomputed: find the import stubs for `glCompressedTexImage2D`, `glCompressedTexSubImage2D`, `glGetString`, `glTexImage2D`, `glTexSubImage2D` (the type 4 records give the slot for each import name), find free space at the end of the code section, and rebuild the hook with new `--defsym` values (`tools/hook/build_hook.sh`).
