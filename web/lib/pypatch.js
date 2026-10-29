// Runs the repository's own tools/patch_ipa.py inside Pyodide, unchanged.
// Only Info.plist and the game executable are handed to Python (a few tens of MB); the big .dz
// files are streamed by JavaScript instead, with --skip-dz.

const SETUP = `
import io, os, sys, plistlib, zipfile, runpy, contextlib, json

def _b(x):
    return x.to_bytes() if hasattr(x, "to_bytes") else bytes(x)

def boz_read_plist(data):
    info = plistlib.loads(_b(data))
    return json.dumps({k: str(info.get(k)) for k in ("CFBundleExecutable", "CFBundleIdentifier", "CFBundleVersion")})

def boz_patch(app_name, plist, exe_name, exe, args):
    os.makedirs("/work/out", exist_ok=True)
    ipa = "/work/mini.ipa"
    with zipfile.ZipFile(ipa, "w", zipfile.ZIP_STORED) as z:
        z.writestr("Payload/%s/Info.plist" % app_name, _b(plist))
        z.writestr("Payload/%s/%s" % (app_name, exe_name), _b(exe))
    del exe
    out = "/work/out/boz_aarch64.s3e"
    if os.path.exists(out):
        os.remove(out)
    log = io.StringIO()
    sys.argv = ["patch_ipa.py", ipa, "--skip-dz", "--assets", "/work/out"] + list(args)
    code = 0
    g = {}
    with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        try:
            g = runpy.run_path("/boz/tools/patch_ipa.py", run_name="__main__")
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    os.remove(ipa)
    dz = list(g.get("DZ_FILES", ["blackops_gles1.dz", "blackops_hires.dz"]))
    return json.dumps({"code": code, "log": log.getvalue(), "dz": dz})
`;

// files: { "patch_ipa.py": Uint8Array, "hook.bin": ..., "hook.json": ..., "game_config.icf": ... }
export async function setupPatcher(pyodide, files) {
  const FS = pyodide.FS;
  for (const d of ["/boz", "/boz/tools", "/boz/tools/hook", "/boz/app", "/boz/app/src", "/boz/app/src/main", "/boz/app/src/main/assets", "/work"]) {
    try { FS.mkdir(d); } catch { /* exists */ }
  }
  FS.writeFile("/boz/tools/patch_ipa.py", files["patch_ipa.py"]);
  FS.writeFile("/boz/tools/hook/hook.bin", files["hook.bin"]);
  FS.writeFile("/boz/tools/hook/hook.json", files["hook.json"]);
  FS.writeFile("/boz/app/src/main/assets/game_config.icf", files["game_config.icf"]);
  pyodide.runPython(SETUP);
}

export function readPlist(pyodide, plistBytes) {
  return JSON.parse(pyodide.globals.get("boz_read_plist")(plistBytes));
}

export function runPatch(pyodide, { appName, plist, exeName, exe, args = [] }) {
  const fn = pyodide.globals.get("boz_patch");
  const pyArgs = pyodide.toPy(args);
  const res = JSON.parse(fn(appName, plist, exeName, exe, pyArgs));
  pyArgs.destroy();
  fn.destroy();
  if (res.code !== 0) {
    const err = res.log.match(/ERROR: (.*)/);
    const e = new Error(err ? err[1] : "patch_ipa.py failed");
    e.log = res.log;
    throw e;
  }
  const s3e = pyodide.FS.readFile("/work/out/boz_aarch64.s3e");
  pyodide.FS.unlink("/work/out/boz_aarch64.s3e");
  return { s3e, log: res.log, dzNames: res.dz };
}
