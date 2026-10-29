# IPA patcher page

A web page (GitHub Pages) that builds the playable 64-bit APK from the user's own iOS IPA, entirely in
their browser, including on an Android phone. Nothing is uploaded: the IPA, the patched code and the
finished APK never leave the device.

## Files

| Path | What it is |
|------|------------|
| `.github/workflows/patcher-page.yml` | Builds the game-free "shell" APK, attaches it to the release, publishes `web/` to Pages with a copy of the shell APK and the patch script, and adds the page link to the release notes |
| `web/index.html`, `web/app.js` | The page |
| `web/patch-worker.js`, `web/lib/pypatch.js` | Runs `tools/patch_ipa.py` unchanged inside [Pyodide](https://pyodide.org) (Python in WebAssembly), in a Web Worker |
| `web/lib/zip.js` | Reads the IPA piece by piece (ZIP64 supported), so a 1 GB file never has to fit in memory |
| `web/lib/apk.js`, `web/lib/build.js` | Writes the APK as a stream and signs it with APK Signature Scheme v2 |
| `web/lib/keys.js` | Creates the signing key and certificate in the browser and keeps them in IndexedDB |
| `web/lib/sinks.js`, `web/sw.js` | Saves the APK: a service-worker streamed download on phones, the save dialog on desktop Chrome/Edge |

## Setup (once)

1. Copy these files into the repository root and push.
2. **Settings → Pages → Build and deployment → Source: GitHub Actions.**
3. Publish a release. The workflow builds the shell APK, uploads it to the release as
   `boz-shell-<tag>.apk`, deploys the page, and appends the page link to the release notes.
   (You can also run it by hand from the Actions tab; that deploys the page without touching a release.)

The page is at `https://shy-doge.github.io/COD-BOZ-Partially-Decompiled-64Bits/`

## How a build works

1. The user picks the IPA. The page reads only `Info.plist` and the game executable out of it.
2. `tools/patch_ipa.py` runs in Pyodide with `--skip-dz` and produces `boz_aarch64.s3e`, exactly as it
   does on a computer (same bytes).
3. The page downloads the shell APK from the Pages site (same origin, so no CORS problem; GitHub release
   downloads can't be fetched from a page).
4. On "Build & save", the APK is written as a stream: the shell's entries (old signature removed),
   the `.s3e`, then `blackops_gles1.dz` and `blackops_hires.dz` streamed straight out of the IPA,
   stored uncompressed and aligned like the Gradle build. The v2 signature is computed on the fly and
   inserted at the end. Memory use stays small, so it works on phones.

## Notes and limits

- **Android 7.0 or newer.** The page signs with APK Signature Scheme v2 only. The app's `minSdk` is 21,
  but Android 5 and 6 need the old v1 (JAR) signature, which the page does not write.
- **Signing key.** Each browser gets its own key, created on the first build. Rebuilding in the same
  browser installs as an update. Users can save a key backup to rebuild on another device. An APK built
  on a computer with Gradle uses a different key, so Android will ask to uninstall it first.
- **Space and time.** The user needs about 2 GB free (IPA + APK). On a recent phone a build takes a few
  minutes; the page keeps the screen awake while it runs.
- **Browsers.** On Android the APK is saved through a service-worker download (tested with Chromium;
  other browsers with service workers should work but haven't been tried). Desktop Chrome/Edge use the
  save dialog; other desktop browsers assemble the APK in memory first.
- **Pyodide** loads from `cdn.jsdelivr.net` (pinned to 314.0.7, about 10 MB, cached after the first use).
- The workflow fails if the shell APK contains any `.dz` or `*_aarch64.s3e` file, so game data can't
  be published by accident.
- The advanced options on the page map to `--keep-mobile-check`, `--no-online` and `--no-config-hooks`.
