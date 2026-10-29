# Call of Duty: Black Ops Zombies on 64-bit Android (arm64 port)

> ### 📱 Build it on your phone: **[Open the patcher](https://shy-doge.github.io/COD-BOZ-Partially-Decompiled-64Bits/)** (beta)
> Pick your own iOS IPA (v1.3.5) and the page builds the playable APK on your device. Nothing is uploaded.
> Needs Android 7.0+ and about 2 GB of free space. No computer, Android Studio or Python required.

This fork makes the iOS build's 64-bit game code run on modern, **64-bit-only** Android phones. The original Android game only shipped 32-bit code, which current CPUs cannot run.

**Status:** boots to the main menu, textures load, maps load, online play on the community server works (also over mobile data), and the server, voice chat flag and player name can be set in a text file. Tested on a Galaxy S24 Ultra. **Controls, audio, saving and long sessions have had little testing**, see [Known issues](#known-issues).

> **No game files are in this repository, and none will be.** You must provide your own legally obtained copy of the iOS game (`.ipa`, version **1.3.5**). A script in `tools/` builds everything from it on your computer.

## Quick start

**You need:** a computer with Python 3.8+ (no extra packages), JDK 21, the Android SDK (Android Studio sets this up), `adb`, a 64-bit Android phone, about 3 GB free disk space, and the IPA.

1. Clone this repository.
2. Build the game files from your IPA:
   ```
   python3 tools/patch_ipa.py "path/to/Call of Duty Black Ops Zombies v1.3.5.ipa"
   ```
   It writes `app/src/main/assets/boz_aarch64.s3e` and copies `blackops_gles1.dz` and `blackops_hires.dz` (about 820 MB) into the same folder. It stops with a clear message if the IPA is not the supported build. All of these files are git-ignored.
3. Build the app:
   ```
   ./gradlew :app:assembleDebug
   ```
   (Windows: `gradlew.bat :app:assembleDebug`.) The APK is about 890 MB because the game data is inside it.
4. Install it:
   ```
   adb install -r app/build/outputs/apk/debug/app-debug.apk
   ```
5. Start the game. Allow the storage permission prompts ("All files access" is needed for the settings file below). Do **not** put game files in `/sdcard/Android/obb/`, they are not used.
6. The first start creates your settings file by itself, see [Settings file](#settings-file). You only edit it to change something.

If you ever replace the `.s3e` or the data files, clear the app data first: `adb shell pm clear com.activision.boz`.

## Settings file

The game reads a plain text file when it starts. **The app creates it for you on the first start** (right after you grant "All files access") at `/sdcard/BlackOpsZombies/config.txt`, with the community server and voice chat off. It never overwrites an existing file. No name is set, so the game keeps its own default name; add a `player_name` line to choose one. To change something, edit the file with any file manager; it looks like this (see also [docs/config.example.txt](docs/config.example.txt)):

```
multiplayer_server=boz-online.xubi.org
voice_chat=0
player_name=MyName
```

| Key | Meaning |
|---|---|
| `multiplayer_server` | Server host, or `host:port`. The port defaults to 3074. Replaces the game server, auth server and STUN server |
| `voice_chat` | `0` or `1`, replaces the game's voice chat flag. The flag is applied, but whether voice audio actually works is not verified |
| `player_name` | Up to 13 characters from letters, digits, space, `_`, `.`, `-`. Without it the game picks a random `Player-12345` name |

Notes: a line that is invalid is ignored and the game keeps its normal value. If `/sdcard/BlackOpsZombies/config.txt` does not exist, `/sdcard/Android/data/com.activision.boz/files/config.txt` is tried; that one works even without "All files access", but Android 11 and newer hide that folder from normal file managers (use `adb push`). Changes apply the next time the game starts.

## What this fork changes

| Path | Change |
|---|---|
| `tools/patch_ipa.py` | Builds `boz_aarch64.s3e` from your IPA (memory sizes, texture hook, online settings, settings file support) and copies the data archives |
| `tools/hook/` | Source, prebuilt blob and tests of the texture hook (no game code in it) |
| `docs/HOW_IT_WORKS.md`, `docs/config.example.txt` | The full technical explanation of how the port was done, and an example settings file |
| `app/build.gradle.kts` | arm64-v8a enabled, `.dz` stored uncompressed, wrong-ABI 32-bit lib excluded, `keystore.properties` and `version.properties` optional |
| `IsDeviceActivity.kt`, `SoundPlayer.kt`, `LoaderThread.kt`, `LoaderView.kt`, `BozConfig.kt` | Small crash guards for 64-bit devices and newer Android (including a retry when the window surface is not ready after a resume); `BozConfig.kt` (new) writes the default settings file on first start |
| `gradle.properties` | AndroidX flag (the project did not build without it) |
| `.gitignore` | Ignores the generated game files and the IPA |

## How it works (short version)

* The iOS executable contains the 64-bit game as a Marmalade "s3e" image, split into three Mach-O sections. The script puts it back together, decrypts its data section, and packs it the way the Android loader wants.
* The embedded iOS configuration only gives the game its full memory when running on iOS. Four values are patched so Android gets the same sizes, otherwise the game crashes when a map loads.
* The embedded iOS configuration selects an OpenFeint online account (a service that no longer exists) and points at Activision's old servers, so every multiplayer option reported "your current connection does not support online multiplayer". The script appends the same online settings the Android build uses: the community server `boz-online.xubi.org`, a generic account, and game version `1. 0. 9` (the iOS value `1. 3. 1` is not accepted for matchmaking).
* The iOS textures are PVRTC, which Android GPUs (Adreno) cannot use. A small decoder is injected into the game's code. It converts PVRTC to RGBA when the game uploads a texture, and it makes the GPU report PVRTC support so the game picks the right data set.
* The same injected code reads the settings file: it intercepts the game's own configuration reads (server, voice chat flag) and redirects the default `Player-%d` name format to your name.

The details, file formats, offsets and how each bug was found are in [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md).

## Known issues

* Only the **iOS v1.3.5** build is supported. The script refuses anything else.
* No 64-bit `libIsDevice.so` / `libIsIOSUtils.so`, so hardware key events and some device queries are missing.
* **Joining another player's online room failed in a first test with two phones on the same Wi-Fi** ("Error on joining room", also with different names). The game is peer-to-peer: the host opens UDP ports 50117 and 50118 on the router with UPnP and the other player connects to the host's public address, which many routers cannot loop back to a phone on the same network. Not yet tested with the players on different networks.
* **Mobile data:** the game refused online play on some mobile connections (it rejects the 3G HSDPA/HSUPA/HSPA network types, which some carriers also report on faster networks). The patcher removes that check (`--keep-mobile-check` keeps it). With it removed, online play worked over mobile data in a test on one carrier; hosting from a carrier connection depends on the carrier's NAT.
* **LAN play: lobby opens without crashing, but LAN discovery still does not work.** LAN discovery failure is a separate issue — likely caused by Android 10+ scoped networking restrictions, Samsung's network manager blocking inter-app traffic, or the router having multicast disabled.
* There is no in-game screen to change the player name (the game never had one for this account type). Use the settings file.
* Online play was only tested by hosting a room. Playing with other people and the game version mismatch with players on other builds are untested.
* No in-app purchases, Game Center or Google Play billing. Activision's original online services no longer exist; online play uses the community server.
* Only 4bpp PVRTC textures are decoded (exactly). 2bpp PVRTC textures are not supported and would stay blank; it is not known whether the game has any. Textures wider than 2048 px are skipped.
* Textures are stored as RGBA8888 on the GPU, which uses more memory than the original.
* Tested on Galaxy S24 Ultra.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Script says "code hash differs" | Wrong IPA version. You need v1.3.5 (the universal build with both 32-bit and 64-bit code) |
| App closes right at startup | `boz_aarch64.s3e` or a `.dz` file is missing from `app/src/main/assets/`, or there is more than one `*_aarch64.s3e`. Re-run the script, then `adb shell pm clear com.activision.boz` |
| Menu works, no textures | The `.s3e` was not made by this script, or an old build is installed. Re-run the script, rebuild, clear app data |
| Crash when a map loads | The memory patch is missing, same fix as above |
| "connection does not support online multiplayer" | The online settings are missing. Re-run the script without `--no-online`, rebuild, clear app data |
| Online rooms fail to join or list | The phone cannot reach the server (`adb shell ping boz-online.xubi.org`), the server is down, or `multiplayer_server` in your settings file is wrong |
| Joining a friend's room fails | The two phones are probably on the same router; try one on mobile data. The host's router needs UPnP enabled (or forward UDP 50117 and 50118 to the host, plus TCP 3074 and UDP 3478 for the online services). See [Known issues](#known-issues) |
| Settings file has no effect | The file is not at `/sdcard/BlackOpsZombies/config.txt` (check the exact name and folder), the app lacks "All files access", or the app was not restarted |
| Gradle: "path may not be null or empty" or "Could not read version.properties" | The fork's `app/build.gradle.kts` was replaced by the original one |
| Gradle: AndroidX error | `gradle.properties` is missing |

### Reporting a crash

See [docs/CAPTURING_CRASH_LOGS.md](docs/CAPTURING_CRASH_LOGS.md): the log has to be recorded *while* you reproduce the crash. Please include the output of:
```
adb logcat -d -b crash -v threadtime
adb shell dumpsys activity exit-info com.activision.boz
```
and what you were doing. For a native crash, the full register dump matters (every `x0` ... `x30`, `sp`, `pc` line).

### Rebuilding the texture hook (optional)

Options of `patch_ipa.py`: `--no-online` (leave the iOS online settings alone), `--no-config-hooks` (no settings file), `--keep-mobile-check`, `--ios-game-version`, `--custom-mdns`, `--skip-dz`, `--keep-raw FILE`. A prebuilt `tools/hook/hook.bin` is included. To rebuild it: `pip install ziglang pyelftools`, then `tools/hook/build_hook.sh`. To run its emulated tests: `pip install unicorn numpy texture2ddecoder`, `python3 tools/patch_ipa.py <ipa> --assets /tmp/x --keep-raw patched.raw`, then `python3 tools/hook/test_hook_emulated.py patched.raw`. The test checks the texture decoder, the extension filter and the settings file parser.

## Credits and legal

* The Kotlin port and the Android project this fork is based on: **eugene373**, and the original decompiled Java source by **12brendon34** (see the original README below).
* Black Ops Zombies is the property of Activision. Marmalade is the property of its owners. This repository contains no Activision or Marmalade game code, assets or the IPA, only tools that process a copy you already own. Do not upload the generated files anywhere.

---

# Original README (unmodified)

# BOZ Kotlin Port (`Master/`)

Dogecoin Wallet: DLXroiTaabPubUayDqhsHKsU7yTuegMDen

This is the Kotlin source tree for the Call of Duty: Black Ops Zombies
mobile project hosted under `Main/`. The original tree was a
Groovy-flavored mix of Marmalade-era Java plus raw JAR blobs; this port
moves everything to Kotlin source while preserving the JVM ABI needed by
the native libs in `app/src/main/jniLibs/`.

Big thanks to [12brendon34] https://github.com/12brendon34  for laying the
ground work with the decompiled java source! I'm sure I'd still be
converting to Kotlin if not for the Github Repo.

For context, original-tree documentation (and the full Maven build that
this port inherits from) lives in `../Master/README.md`.

## Overview

Call of Duty: Black Ops Zombies mobile was released in 2012, developed
using the now-discontinued "Marmalade" game engine. This port aims to
decompile every class the runtime needs and rewrite it as Kotlin source
so the project is editable end-to-end on modern Android Studio, without
relying on binary JARs.

**Build status:** `./gradlew assembleDebug` → `BUILD SUCCESSFUL`,
`app/build/outputs/apk/debug/app-debug.apk` (~53 MB). `compileDebugKotlin`
is at **0 errors** after the rebuild pass documented below.

## Layout

```
Main/
├── app/
│   ├── build.gradle.kts            # Kotlin DSL build script
│   ├── version.properties          # auto-incremented (gitignored)
│   └── src/main/
│       ├── AndroidManifest.xml     # copied from Master
│       ├── assets/                 # boz.s3e + blackops_loader.dz (+ music dirs)
│       ├── java/                   # all Kotlin sources (target tree)
│       ├── jniLibs/                # arm64-v8a / armeabi-v7a / armeabi *.so
│       └── res/                    # drawables, layouts, 120+ locale values
├── buildtools/                     # Python pipeline that produced java/
│   ├── check_*.py                  # audits Kotlin against .so / smali
│   ├── decompile_jar.py            # javap driver (JAR -> text dumps)
│   ├── smali_to_kt.py              # javap-text -> Kotlin skeleton converter
│   ├── syntex_to_kt.py             # REAL-smali -> Kotlin skeleton converter (new)
│   ├── extract_7z.py               # handles the marmalade.7z archive
│   ├── decoded/                    # javap -p -c text dumps from the JARs
│   ├── generated/                  # prior skeleton output staging
│   └── smali_out/                  # output of syntex_to_kt.py (4967 skeletons)
├── build.gradle.kts                # root build, plugins declared apply false
├── settings.gradle.kts             # repos + plugin versions
├── gradle.properties               # (gitignored — machine-local)
├── gradle/wrapper/                 # Gradle 8.11.1 wrapper
├── gradlew, gradlew.bat
├── keystore.properties             # signing material (gitignored)
├── upload-keystore.jks             # signing keystore (gitignored)
├── local.properties                # sdk.dir, java.home (gitignored)
├── HOWTO_BUILD.md                  # build + re-decompile workflow
��── README.md                       # ← you are here
```

## How `app/src/main/java/` was produced

The port used **two** decompilation passes, both feeding the same
`buildtools/` pipeline:

1. **Javap pass (signatures only).** The original Marmalade SDK
   (`s3e_release.jar` + `s3e_debug.jar` + `s3e_resources.jar`), the
   Bolts Task library, and the 5 root Marmalade shell classes were
   decompiled with `javap -p -c` and rewritten to Kotlin skeleton
   sources via `buildtools/smali_to_kt.py`. These skeletons have real
   signatures but only `TODO("body")` stubs — no method bodies.

2. **Real-smali pass (signatures + bodies).** A full real-smali
   decompilation of the Master APK lives at `../Main/smali/`
   (��4,967 `.smali` files with complete bytecode bodies — `invoke-*`,
   branches, `iget`/`iput`, `:cond_N`/`:goto_N`). This was converted
   to Kotlin skeletons via the new
   [`buildtools/syntex_to_kt.py`](buildtools/syntex_to_kt.py), which
   parses the actual smali grammar (`.class`, `.field name:type`,
   `.method/.end method`, `.param`, `.throws`, `.annotation`) — unlike
   `smali_to_kt.py`, which only understands javap text. Output landed
   in [`buildtools/smali_out/`](buildtools/smali_out/) mirroring the
   `main/smali` package tree.

   The smali-derived skeletons carry the raw smali body as a `/* */`
   comment block under each `TODO("body")`, which is what made it
   possible to fix the Java→Kotlin ABI mismatches below — the real
   bytecode comments are the authoritative truth source when a
   hand-written stub disagrees with the smali.

See [`HOWTO_BUILD.md`](HOWTO_BUILD.md) for re-running the decompile
steps and the `check_*.py` verification helpers.

A few conversions were done by hand because the bodies were small
enough to translate 1:1:

| Original                                       | → Kotlin |
|-----------------------------------------------|----------|
| `Java/com/activision/boz/Main.java`           | `java/com/activision/boz/Main.kt` |
| `Java/com/ideaworks3d/studio/IsDevice.java`   | `java/com/ideaworks3d/studio/IsDevice.kt` |
| `Java/com/ideaworks3d/studio/IsDeviceActivity.java` | `java/com/ideaworks3d/studio/IsDeviceActivity.kt` |
| `Java/com/savegame/SavesRestoring.java`       | `java/com/savegame/SavesRestoring.kt` |

## The rebuild pass: 283 → 0 Kotlin compile errors

Once the skeletons were in place the port did not actually compile.
A systematic pass reduced `compileDebugKotlin` errors from 283 down
to 0, with `buildtools/smali_out/` (the real-smali skeletons) used as
the ground truth for every member name, type signature, and ABI
detail. The reduction stages:

| Stage | Errors | What landed |
|------:|------:|-------------|
| 1 | 283 → 214 | Fixed `s3ePermissions` family: `onRequestPermissionsResult` grantResults are `IntArray` (`[I`), not `Array<Int>` — primitive-array JNI ABI hazard. |
| 2 | 214 → 144 | Nullable `!!` wave across `LoaderActivity.m_Activity` / `m_View` / `m_TopLevel` / `m_FrameLayout` chains; `IsDevice` field renamed to smali-true `m_IsActivated`; `s3eTest` `Object` monitor methods via `(lock as java.lang.Object).wait()/.notifyAll()`. |
| 3 | 144 → 79 | `bolts.Continuation` → `fun interface` (SAM lambdas compile) + `then` return type relaxed to `TContinuationResult?` (Void continuations can `return null` as Java did); `AndroidExecutors`/`BoltsExecutors` dropped bogus `INSTANCE = Self()` self-constructors; `s3eImagePicker.CopyStream` restructured so the infinite loop is finite; many `String?`/`Context` `!!` fixes. |
| 4 | 79 → 30 | Added missing `ListenerManager` import; fixed Kotlin-property-vs-method calls (`getCurrentOrientation()`/`getOrientation()`/`size()` need parens — Kotlin-defined methods do not auto-convert to property syntax); boxed-`Integer` ABI for `LoaderThread.m_OnTouchWait` (smali shows `Ljava/lang/Integer;`); `LoaderView` local-`val` bind for mutable-bitmap smart-cast; dropped inner-class `companion object` (Kotlin forbids inside inner classes). |
| 5 | 30 → 1 | `AppLink.Target` dropped redundant getters that collided at JVM level with the public `val` accessors; `s3ePermissions_PermissionRequestResult` `@JvmField` on `result` to avoid a synthetic `setResult(I)V` clash. |
| 6 | 1 → 0 | `LoaderThread.m_OnTouchWait` init reworded to `valueOf(0) as java.lang.Integer`. |

### Key ABI / idioms learned (likely to recur if more bodies get filled in)

- **Primitive arrays for JNI.** Java `int[]`/`short[]`/`byte[]` (smali
  `[I`/`[S`/`[B`) **must** map to Kotlin `IntArray`/`ShortArray`/
  `ByteArray`, **not** `Array<Int>`/`Short`/`Byte`. When a stub says
  the latter, it's the skeleton emitter being wrong — fix at the
  source. Confirmed against
  `LoaderActivity.onRequestPermissionsResult(I[Ljava/lang/String;[I)V`
  and `LoaderAPI.s3eConfigGetInt(...[I)I` in smali.

- **Kotlin property access vs `getX()` methods.** `x.f` only works on
  declared `val`/`var` properties. A Kotlin-defined method named
  `getF()` (or `getCurrentOrientation()`, `isSucceeded()`) is **not**
  auto-promoted to property syntax — you must call it as `x.f()`. Java
  `Collection.size()` likewise needs `x.size()`, not `x.size`.

- **`Object` monitor methods from Kotlin.** `wait()` / `notify()` /
  `notifyAll()` don't exist on Kotlin `Any`. Use
  `(obj as java.lang.Object).wait()` etc. — see memory of the prior
  `IllegalMonitorStateException` touch-wait bug.

- **Lifecycle visibility.** `LoaderActivity`'s `onResume`/`onStop`
  overrides are `protected` in smali (inherited from `Activity`).
  Java allowed same-package callers (`s3eTest`) to invoke them
  through a reference; Kotlin does not, so they were widened to
  `public override` to keep the same callsite working.

- **Inner classes.** Kotlin forbids `companion object` inside a
  non-`inner`-only nested class; compile-time-constant `const val`
  members that the Java source put on a non-static inner class have
  to move to the outer class's companion or be dropped.

- **Same-package protected access is gone in Kotlin.** Any cross-class
  call to a `protected` member needs either widening visibility (as
  above) or a reflective workaround.

- **Boxed-integer fields the native code reads.** Where smali shows a
  field as `Ljava/lang/Integer;` (boxed), keep that type — Kotlin `Int`
  would change the field's bytecode shape and can break JNI/reflection
  symmetry with the original `.so`s. Initialize with
  `java.lang.Integer.valueOf(x) as java.lang.Integer`.

## Disabling the s3e signature check

The Marmalade engine's native library `libs3e_android.so` verifies digital
signatures on `.s3e` container files at load time. The game archive
`app/src/main/assets/boz.s3e` is an LZMA1-RAW-compressed s3e container
with an embedded `icf` header and ARM executable payload. When signature
checking is active, the engine refuses to load any `.s3e` whose signature
doesn't match — which means you cannot edit the embedded `s3e.cfg`
configuration or map data without breaking the file.

**Why we disable it:** The signature check on `libs3e_android.so` is patched
out so that `boz.s3e` can be freely edited for configuration tweaks
(`s3e.cfg`) and map/data modifications. Without this patch, any edit to the
s3e archive causes the engine to reject it at startup, halting development
on the Kotlin port's runtime behavior.

**The patch** replaces a conditional branch (`BLE` on ARM32, `B.LE` on
ARM64) in the signature-verification function with an instruction that
always takes the success path:

| Architecture | Original instruction | Patch | Offset |
|---|---|---|---|
| armeabi (ARM 32-bit) | `BLE +0x13` | `MOVNE R0, #0` | 0x2496c |
| armeabi-v7a (Thumb 32-bit) | `BLE +0x13` | `MOVNE R0, #0` | 0x383b0 |
| arm64-v8a (AArch64) | `B.LE +0x45c` | `NOP` | 0x2aa08 |

The patch is applied via `buildtools/disable_sig_check.py`, which scans for
all `libs3e_android.so` files (jniLibs + build intermediates) and applies
the correct architecture-specific fix. It verifies each file contains the
`Incorrect signature` error string before patching, ensuring it only
modifies the correct Marmalade library.

To (re-)apply the patch:
```
python buildtools/disable_sig_check.py
```

## Building

```
./gradlew :app:assembleDebug     # produces app/build/outputs/apk/debug/app-debug.apk
./gradlew :app:assembleRelease   # signed with upload-keystore.jks
```

The debug and release variants differ only in which Marmalade native
libs are linked; the Kotlin side is build-variant-agnostic.

Requirements: JDK 11+ (validated on Temurin 21), Android SDK with
build-tools 36, Gradle 8.11.1 (auto-fetched by the wrapper).

## Game data

The `.dz` texture data files are bundled inside the APK from the IPA. No downloads or OBB writes are needed.

## Storage / OBB write access (Android 11+)

The Marmalade engine writes its expansion data to the *raw* external
storage path `/sdcard/Android/obb/com.activision.boz/`, which modern
Android blocks under scoped storage. The fix that made downloads land
reliably on Android 12 and 15:

- **`targetSdk = 30`** in `app/build.gradle.kts` (compileSdk stays 36).
  At SDK 30 the legacy `WRITE_EXTERNAL_STORAGE` semantics the engine
  was written for return, and writes to the OBB path succeed again.
- **`AndroidManifest.xml`** declares `WRITE_EXTERNAL_STORAGE` (no
  `maxSdkVersion` cap) plus `MANAGE_EXTERNAL_STORAGE` as a belt-and-
  braces for API 30+.
- **`IsDeviceActivity.kt`** gates the native loader behind storage
  access before starting the engine: it requests the runtime
  `WRITE_EXTERNAL_STORAGE` grant on API 23-29 and the All-files-access
  (`MANAGE_EXTERNAL_STORAGE`) grant on API 30+, and pre-creates the
  `Android/obb/<pkg>/` dir (via `context.obbDir` *and* the raw sdcard
  path the engine constructs) so the engine's mkdir doesn't fail.

Without these the engine's write to the OBB path fails with `EACCES`,
which it surfaces as a misleading *"device does not have enough storage
to download the needed files"* error — not an actual disk-space
problem.

## Repo exclusions (`.gitignore` policy)

These are intentionally **not** committed and live only on the build
machine:

- `keystore.properties`, `upload-keystore.jks` — signing material,
  contains plaintext passwords; never pushed.
- `app/version.properties`, `gradle/gradle-daemon-jvm.properties` —
  re-touched by gradle on every invocation; not project state.
- `app/src/main/assets/blackops_atitc.dz`,
  `blackops_etc.dz`, `blackops_gles1.dz` —
  oversized game-data blobs (>100 MB, exceeds GitHub's limit).
- `app/src/main/assets/blackops-music/`,
  `app/src/main/assets/deadops-music/` — bulk music assets.
- `buildtools/smali_out/` — generated by `syntex_to_kt.py`, not
  hand-edited source. It is committed as a reference snapshot of the
  smali→Kotlin conversion (4,967 skeleton files); future regenerations
  are gitignored so re-running the converter doesn't churn the tree.
- `app/build/`, `.gradle`, `.kotlin`, `local.properties`,
  `gradle.properties`, etc. — standard IDE/build outputs.

If you want the big binaries in a clone, fetch them from the CDN links
above or copy from a working tree.

## Recent Fixes (v1.0.9)

### Google Play Services / JNI Crash Fix

**What was broken:** The game crashed with a JNI `CallVoidMethodV` /
`CallBooleanMethodV` on `null obj` when ending a match or attempting
online play. The native `libs3eGooglePlayServices.so` held stale global
references to Java objects (Activity, callbacks) that became invalid
after `onDestroy()` / process shutdown. When the native tick loop or
match-ending logic fired callbacks (`native_ScoreSubmittedCallback`,
`native_SignOutCallback`, etc.), the JNI call crashed.

**Fix applied:**
- Replaced all Google Play Services integration with GMS-free Kotlin
  stubs (`s3eGooglePlayServices`, `s3eGooglePlayServicesActivity`,
  `GameHelper`, `BaseGameActivity`). The stubs implement the same
  JVM-facing API but return `false` / empty strings / no-op for every
  call.
- Converted every `public external @JvmStatic fun native_*()` JNI
  callback in `s3eGooglePlayServicesActivity` to a regular Kotlin
  no-op (`{ Log.i(...) }`) so the native library's JNI `CallStatic*`
  sites resolve to harmless stubs instead of crashing on destroyed
  objects.
- Removed `libs3eGooglePlayServices.so` from `jniLibs/armeabi-v7a/`
  (and `arm64-v8a/`) so the shared object is never loaded. The build
  now depends on `play-services-base:11.0.4` /
  `play-services-games:11.0.4` only for compile-time API surface.
- This runs in a **private-server / offline mode** — no Google Play
  Services sign-in, achievements, leaderboards, or real-time
  multiplayer via GPS are expected to work. Co-op matchmaking is served
  by the private Demonware backend configured in `game_config.icf`.

### Graphics / Rendering — Performance Improvements

- Optimized the Marmalade Android renderer for modern devices.
- Increased hardware rendering cache capacity for improved rendering
  performance and reduced cache pressure.
- Increased vertex cache capacity for smoother geometry processing.
- Re-enabled triangle-strip optimizations.
- Re-enabled the newer texture atlas system.
- Kept hardware texture compression enabled to reduce memory usage and
  improve texture performance.
- Disabled unnecessary benchmark output and release-build debugging
  overhead.
- Cleaned up Android release rendering configuration and GLES2 settings.

### Multiplayer / Audio

- Reverted the experimental voice chat changes.
- Restored the original multiplayer audio behavior after voice chat was
  found to interfere with in-game Co-Op sound output.
- Online Co-Op multiplayer audio now uses the previously working
  configuration.

### General

- Tuned the Android release configuration specifically for modern
  Android hardware.
- Removed redundant configuration entries where applicable.
- No gameplay changes were made as part of this update.
