// Blend Downgrader: proses utama Electron.
// Tugas: deteksi Blender terpasang, baca versi file .blend, jalankan
// legacy_export.py (Blender baru) lalu legacy_import.py (Blender lama),
// dan mengunduh Blender versi lama resmi dari download.blender.org.
const { app, BrowserWindow, ipcMain, dialog, shell } = require("electron");
const path = require("path");
const fs = require("fs");
const fsp = fs.promises;
const os = require("os");
const cp = require("child_process");
const https = require("https");
const zlib = require("zlib");
const crypto = require("crypto");

const ENGINE = app.isPackaged
  ? path.join(process.resourcesPath, "engine")
  : path.join(__dirname, "..", "engine");
const DATA = app.getPath("userData");
const BLENDERS_DIR = path.join(DATA, "blenders");
const SETTINGS = path.join(DATA, "settings.json");
const IS_MAC = process.platform === "darwin";
const IS_WIN = process.platform === "win32";

let win;
let current = null; // proses Blender yang sedang berjalan
let cancelled = false;

// ------------------------------------------------------------------ settings
function loadSettings() {
  try { return JSON.parse(fs.readFileSync(SETTINGS, "utf8")); } catch { return {}; }
}
function saveSettings(s) {
  fs.mkdirSync(DATA, { recursive: true });
  fs.writeFileSync(SETTINGS, JSON.stringify(s, null, 2));
}

// ------------------------------------------------------------------ util
function send(ch, payload) { if (win && !win.isDestroyed()) win.webContents.send(ch, payload); }

function cmpVer(a, b) {
  for (let i = 0; i < 3; i++) { const d = (a[i] || 0) - (b[i] || 0); if (d) return d; }
  return 0;
}
const verStr = (v) => v.slice(0, 2).join(".") + (v[2] != null ? "." + v[2] : "");

function exists(p) { try { fs.accessSync(p); return true; } catch { return false; } }
function lsdir(p) { try { return fs.readdirSync(p); } catch { return []; } }

// .app -> executable di dalamnya
function normalizeExe(p) {
  if (IS_MAC && p.endsWith(".app")) return path.join(p, "Contents", "MacOS", "Blender");
  return p;
}
function displayName(exe) {
  if (IS_MAC) { const m = exe.match(/([^/]+)\.app\/Contents\/MacOS\/Blender$/); if (m) return m[1]; }
  return path.basename(path.dirname(exe));
}

// ------------------------------------------------------------------ deteksi Blender
function candidatePaths() {
  const out = new Set();
  const add = (p) => { if (p && exists(p)) out.add(p); };
  const s = loadSettings();
  (s.customBlenders || []).forEach(add);

  if (IS_MAC) {
    for (const dir of ["/Applications", path.join(os.homedir(), "Applications")]) {
      for (const n of lsdir(dir)) {
        if (/blender/i.test(n) && n.endsWith(".app")) add(path.join(dir, n, "Contents/MacOS/Blender"));
        if (/blender/i.test(n) && !n.endsWith(".app"))
          for (const m of lsdir(path.join(dir, n)))
            if (m.endsWith(".app")) add(path.join(dir, n, m, "Contents/MacOS/Blender"));
      }
    }
    add(path.join(os.homedir(), "Library/Application Support/Steam/steamapps/common/Blender/Blender.app/Contents/MacOS/Blender"));
    for (const v of lsdir(BLENDERS_DIR)) add(path.join(BLENDERS_DIR, v, "Blender.app/Contents/MacOS/Blender"));
  } else if (IS_WIN) {
    const roots = [process.env["ProgramFiles"], process.env["ProgramFiles(x86)"], process.env["ProgramW6432"]].filter(Boolean);
    for (const r of roots) {
      const bf = path.join(r, "Blender Foundation");
      for (const n of lsdir(bf)) add(path.join(bf, n, "blender.exe"));
      add(path.join(r, "Steam", "steamapps", "common", "Blender", "blender.exe"));
    }
    for (const v of lsdir(BLENDERS_DIR))
      for (const n of lsdir(path.join(BLENDERS_DIR, v))) add(path.join(BLENDERS_DIR, v, n, "blender.exe"));
  } else {
    ["/usr/bin/blender", "/usr/local/bin/blender", "/snap/bin/blender"].forEach(add);
    for (const n of lsdir(os.homedir())) if (/^blender/i.test(n)) add(path.join(os.homedir(), n, "blender"));
    for (const v of lsdir(BLENDERS_DIR))
      for (const n of lsdir(path.join(BLENDERS_DIR, v))) add(path.join(BLENDERS_DIR, v, n, "blender"));
  }
  return [...out];
}

function run(exe, args, { onLine, timeout = 0 } = {}) {
  return new Promise((resolve) => {
    const p = cp.spawn(exe, args, { windowsHide: true });
    current = p;
    let buf = "", all = "";
    const feed = (d) => {
      const s = d.toString(); all += s; buf += s;
      let i;
      while ((i = buf.indexOf("\n")) >= 0) { const line = buf.slice(0, i).replace(/\r$/, ""); buf = buf.slice(i + 1); onLine && onLine(line); }
    };
    p.stdout.on("data", feed); p.stderr.on("data", feed);
    let t = timeout ? setTimeout(() => p.kill(), timeout) : null;
    p.on("error", (e) => { clearTimeout(t); current = null; resolve({ code: -1, out: all + "\n" + e.message }); });
    p.on("close", (code) => { clearTimeout(t); current = null; if (buf && onLine) onLine(buf); resolve({ code, out: all }); });
  });
}

const versionCache = new Map();
async function blenderVersion(exe) {
  let st; try { st = fs.statSync(exe); } catch { return null; }
  const key = exe + ":" + st.mtimeMs;
  if (versionCache.has(key)) return versionCache.get(key);
  const r = await run(exe, ["--factory-startup", "--version"], { timeout: 60000 });
  const m = r.out.match(/Blender (\d+)\.(\d+)(?:\.(\d+))?/);
  const v = m ? [+m[1], +m[2], +(m[3] || 0)] : null;
  versionCache.set(key, v);
  return v;
}

async function detectBlenders() {
  const list = [];
  for (const exe of candidatePaths()) {
    const v = await blenderVersion(exe);
    if (v) list.push({ exe, version: v, label: verStr(v), name: displayName(exe),
                       managed: exe.startsWith(BLENDERS_DIR) });
  }
  // satu entri per versi persis (hindari duplikat symlink)
  const seen = new Set();
  return list.sort((a, b) => cmpVer(b.version, a.version))
    .filter((b) => { const k = fs.realpathSync(b.exe); if (seen.has(k)) return false; seen.add(k); return true; });
}

// ------------------------------------------------------------------ versi file .blend
function readHead(file, n = 64) {
  return new Promise((resolve, reject) => {
    const fd = fs.openSync(file, "r");
    const raw = Buffer.alloc(256 * 1024);
    const len = fs.readSync(fd, raw, 0, raw.length, 0);
    fs.closeSync(fd);
    const b = raw.subarray(0, len);
    let dec = null;
    if (b[0] === 0x28 && b[1] === 0xb5 && b[2] === 0x2f && b[3] === 0xfd) dec = zlib.createZstdDecompress && zlib.createZstdDecompress();
    else if (b[0] === 0x1f && b[1] === 0x8b) dec = zlib.createGunzip();
    if (!dec) return resolve({ head: b.subarray(0, n), compressed: null });
    let got = Buffer.alloc(0), done = false;
    const finish = () => { if (!done) { done = true; dec.destroy(); resolve({ head: got.subarray(0, n), compressed: b[0] === 0x1f ? "gzip" : "zstd" }); } };
    dec.on("data", (c) => { got = Buffer.concat([got, c]); if (got.length >= n) finish(); });
    dec.on("error", () => (got.length ? finish() : reject(new Error("tidak bisa dibaca"))));
    dec.on("end", finish);
    dec.end(b);
  });
}

async function probeFile(file) {
  const st = await fsp.stat(file);
  const { head, compressed } = await readHead(file);
  const s = head.toString("latin1");
  let v = null, m;
  if ((m = s.match(/^BLENDER\d\d-\d\d[vV](\d\d)(\d\d)/))) v = [+m[1], +m[2]];          // format baru (5.0+)
  else if ((m = s.match(/^BLENDER[-_][vV](\d)(\d\d)/))) v = [+m[1], +m[2]];             // format lama
  if (!v) throw new Error("Bukan file .blend yang valid");
  return { path: file, name: path.basename(file), size: st.size, version: v, label: verStr(v), compressed };
}

// ------------------------------------------------------------------ konversi
function outName(file, target, outDir) {
  const base = path.basename(file, ".blend");
  const dir = outDir || path.dirname(file);
  return path.join(dir, `${base}_blender${target.version[0]}.${target.version[1]}.blend`);
}

async function convertOne(job, opts) {
  const log = (line, kind = "log") => send("job:log", { id: job.id, line, kind });
  const status = (s, extra = {}) => send("job:status", { id: job.id, status: s, ...extra });
  const warnings = [];
  const onLine = (line) => {
    if (/PERINGATAN|WARNING/.test(line) && !/DeprecationWarning/.test(line)) {
      log(line, "warn");
      if (/File written by newer Blender binary/.test(line)) return; // sudah dicakup peringatan versi kita
      const clean = line.replace(/^\[legacy-(export|import)\]\s*PERINGATAN:\s*/, "").replace(/^[\d:.]+\s+\S+\s+\|\s*WARNING\s*/, "");
      if (!warnings.includes(clean)) warnings.push(clean);
    }
    else if (/Error|Traceback|GAGAL/.test(line)) log(line, "error");
    else log(line);
  };

  const fileV = job.version;
  const source = opts.source;
  const target = opts.target;
  if (cmpVer(source.version, fileV) < 0) {
    const w = `File ini dari Blender ${verStr(fileV)}, lebih baru dari Blender pembuka (${source.label}). Dicoba tetap dibuka; bila gagal, pasang Blender ${verStr(fileV)}+.`;
    warnings.push(w); log(w, "warn");
  }
  if (cmpVer(target.version, [3, 0]) < 0)
    throw new Error("Versi tujuan minimal Blender 3.0.");

  const tmp = await fsp.mkdtemp(path.join(os.tmpdir(), "blend-downgrader-"));
  const pkg = path.join(tmp, "pkg");
  const out = outName(job.path, target, opts.outDir);
  try {
    status("export", { step: 1 });
    log(`$ ${source.label}: export paket…`, "step");
    const ea = ["-b", "--factory-startup", job.path, "--python", path.join(ENGINE, "legacy_export.py"), "--", pkg];
    if (opts.keepModifiers) ea.push("--keep-modifiers");
    const r1 = await run(source.exe, ea, { onLine });
    if (cancelled) throw new Error("Dibatalkan");
    if (r1.code !== 0 || !exists(path.join(pkg, "scene.json")))
      throw new Error(cmpVer(source.version, fileV) < 0
        ? `Blender ${source.label} tidak bisa membaca file Blender ${verStr(fileV)}. Pasang Blender ${verStr(fileV)} atau lebih baru.`
        : "Export gagal (lihat log)");

    status("import", { step: 2 });
    log(`$ ${target.label}: membangun ulang scene…`, "step");
    const ia = ["-b", "--factory-startup", "--python", path.join(ENGINE, "legacy_import.py"), "--", pkg, out];
    if (!opts.pack) ia.push("--no-pack");
    const r2 = await run(target.exe, ia, { onLine });
    if (cancelled) throw new Error("Dibatalkan");
    if (r2.code !== 0 || !exists(out)) throw new Error("Import gagal (lihat log)");

    let check = null;
    if (opts.verify) {
      status("verify", { step: 3 });
      log(`$ ${target.label}: membuka hasil untuk dicek…`, "step");
      const r3 = await run(target.exe, ["-b", "--factory-startup", out, "--python-expr",
        "import bpy;print('CHECK', len(bpy.data.objects), len(bpy.data.materials), len(bpy.data.meshes))"], { onLine });
      const m = r3.out.match(/CHECK (\d+) (\d+) (\d+)/);
      if (!m) throw new Error(`Hasil tidak bisa dibuka di Blender ${target.label}`);
      check = { objects: +m[1], materials: +m[2], meshes: +m[3] };
    }
    const outInfo = await probeFile(out);
    return { out, warnings, check, outVersion: outInfo.label };
  } finally {
    fsp.rm(tmp, { recursive: true, force: true }).catch(() => {});
  }
}

// ------------------------------------------------------------------ unduh Blender
const SERIES = ["4.5", "4.4", "4.3", "4.2", "4.1", "4.0", "3.6", "3.5", "3.4", "3.3", "3.0"];

function get(url, { onProgress, file } = {}) {
  return new Promise((resolve, reject) => {
    const req = https.get(url, { headers: { "User-Agent": "BlendDowngrader/1.0" } }, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        res.resume(); return resolve(get(new URL(res.headers.location, url).toString(), { onProgress, file }));
      }
      if (res.statusCode !== 200) { res.resume(); return reject(new Error(`HTTP ${res.statusCode}: ${url}`)); }
      const total = +res.headers["content-length"] || 0;
      let got = 0;
      const hash = crypto.createHash("sha256");
      const chunks = [];
      const ws = file ? fs.createWriteStream(file) : null;
      res.on("data", (c) => {
        got += c.length; hash.update(c);
        if (ws) ws.write(c); else chunks.push(c);
        onProgress && onProgress(got, total);
      });
      res.on("end", () => {
        const fin = () => resolve({ body: ws ? null : Buffer.concat(chunks).toString("utf8"), sha256: hash.digest("hex") });
        ws ? ws.end(fin) : fin();
      });
      res.on("error", reject);
    });
    req.on("error", reject);
    current = { kill: () => req.destroy(new Error("Dibatalkan")) };
  });
}

function platformPattern() {
  const arch = process.arch === "arm64" ? "arm64" : "x64";
  if (IS_MAC) return { re: new RegExp(`blender-(\\d+\\.\\d+\\.\\d+)-macos-${arch}\\.dmg`, "g"), ext: "dmg" };
  if (IS_WIN) return { re: /blender-(\d+\.\d+\.\d+)-windows-x64\.zip/g, ext: "zip" };
  return { re: /blender-(\d+\.\d+\.\d+)-linux-x64\.tar\.xz/g, ext: "tar.xz" };
}

async function resolveRelease(series) {
  const base = `https://download.blender.org/release/Blender${series}/`;
  const { body } = await get(base);
  const { re } = platformPattern();
  let best = null, m;
  while ((m = re.exec(body))) {
    const v = m[1].split(".").map(Number);
    if (!best || cmpVer(v, best.v) > 0) best = { v, file: m[0] };
  }
  if (!best) throw new Error(`Blender ${series} tidak tersedia untuk sistem ini`);
  return { series, version: verStr(best.v), url: base + best.file, file: best.file,
           shaUrl: base + `blender-${verStr(best.v)}.sha256` };
}

async function installBlender(series) {
  cancelled = false;
  const rel = await resolveRelease(series);
  send("dl:progress", { phase: "start", ...rel });
  const dest = path.join(BLENDERS_DIR, rel.version);
  await fsp.mkdir(BLENDERS_DIR, { recursive: true });
  const archive = path.join(BLENDERS_DIR, rel.file);
  let last = 0;
  const { sha256 } = await get(rel.url, {
    file: archive,
    onProgress: (got, total) => {
      const now = Date.now();
      if (now - last > 150 || got === total) { last = now; send("dl:progress", { phase: "download", got, total }); }
    },
  });
  // verifikasi checksum resmi
  try {
    const { body } = await get(rel.shaUrl);
    const line = body.split("\n").find((l) => l.includes(rel.file));
    if (line && !line.startsWith(sha256)) { await fsp.rm(archive, { force: true }); throw new Error("Checksum SHA-256 tidak cocok, unduhan rusak"); }
  } catch (e) { if (/Checksum/.test(e.message)) throw e; }

  send("dl:progress", { phase: "install" });
  await fsp.rm(dest, { recursive: true, force: true });
  await fsp.mkdir(dest, { recursive: true });
  if (IS_MAC) {
    const mnt = await fsp.mkdtemp(path.join(os.tmpdir(), "bdmnt-"));
    await run("hdiutil", ["attach", "-nobrowse", "-readonly", "-mountpoint", mnt, archive]);
    try {
      const appName = lsdir(mnt).find((n) => n.endsWith(".app"));
      if (!appName) throw new Error("Blender.app tidak ditemukan di DMG");
      const r = await run("ditto", [path.join(mnt, appName), path.join(dest, "Blender.app")]);
      if (r.code !== 0) throw new Error("Gagal menyalin Blender.app");
    } finally {
      await run("hdiutil", ["detach", mnt, "-force"]);
    }
  } else if (IS_WIN) {
    const r = await run("tar", ["-xf", archive, "-C", dest]);
    if (r.code !== 0) throw new Error("Gagal mengekstrak zip");
  } else {
    const r = await run("tar", ["-xJf", archive, "-C", dest]);
    if (r.code !== 0) throw new Error("Gagal mengekstrak arsip");
  }
  await fsp.rm(archive, { force: true });
  send("dl:progress", { phase: "done", version: rel.version });
  return rel.version;
}

// ------------------------------------------------------------------ IPC
ipcMain.handle("blenders:list", () => detectBlenders());
ipcMain.handle("blenders:add", async () => {
  const r = await dialog.showOpenDialog(win, {
    title: "Pilih aplikasi Blender",
    properties: ["openFile"],
    filters: IS_MAC ? [{ name: "Blender", extensions: ["app"] }]
      : IS_WIN ? [{ name: "blender.exe", extensions: ["exe"] }] : [],
  });
  if (r.canceled || !r.filePaths[0]) return null;
  const exe = normalizeExe(r.filePaths[0]);
  const v = await blenderVersion(exe);
  if (!v) throw new Error("File yang dipilih bukan Blender");
  const s = loadSettings();
  s.customBlenders = [...new Set([...(s.customBlenders || []), exe])];
  saveSettings(s);
  return detectBlenders();
});
ipcMain.handle("blenders:remove", async (_e, exe) => {
  const s = loadSettings();
  s.customBlenders = (s.customBlenders || []).filter((x) => x !== exe);
  saveSettings(s);
  if (exe.startsWith(BLENDERS_DIR)) {
    const dir = path.join(BLENDERS_DIR, path.relative(BLENDERS_DIR, exe).split(path.sep)[0]);
    await fsp.rm(dir, { recursive: true, force: true });
  }
  return detectBlenders();
});
ipcMain.handle("blenders:series", () => SERIES);
ipcMain.handle("blenders:resolve", (_e, series) => resolveRelease(series));
ipcMain.handle("blenders:install", (_e, series) => installBlender(series));

ipcMain.handle("files:pick", async () => {
  const r = await dialog.showOpenDialog(win, {
    title: "Pilih file .blend",
    properties: ["openFile", "multiSelections"],
    filters: [{ name: "Blender", extensions: ["blend"] }],
  });
  return r.canceled ? [] : r.filePaths;
});
ipcMain.handle("files:probe", async (_e, file) => probeFile(file));
ipcMain.handle("folder:pick", async () => {
  const r = await dialog.showOpenDialog(win, { properties: ["openDirectory", "createDirectory"] });
  return r.canceled ? null : r.filePaths[0];
});
ipcMain.handle("convert:one", async (_e, job, opts) => {
  cancelled = false;
  try { return { ok: true, ...(await convertOne(job, opts)) }; }
  catch (e) { return { ok: false, error: e.message }; }
});
ipcMain.handle("cancel", () => { cancelled = true; if (current) current.kill(); });
ipcMain.handle("settings:get", () => loadSettings());
ipcMain.handle("settings:set", (_e, patch) => { const s = { ...loadSettings(), ...patch }; saveSettings(s); return s; });
ipcMain.handle("reveal", (_e, p) => shell.showItemInFolder(p));
ipcMain.handle("open-in", async (_e, exe, file) => {
  cp.spawn(exe, [file], { detached: true, stdio: "ignore" }).unref();
});
ipcMain.handle("app:info", () => ({ version: app.getVersion(), platform: process.platform, arch: process.arch }));

// ------------------------------------------------------------------ window
function createWindow() {
  win = new BrowserWindow({
    width: 1040, height: 760, minWidth: 760, minHeight: 560,
    title: "Blend Downgrader",
    backgroundColor: "#16171a",
    webPreferences: { preload: path.join(__dirname, "preload.js"), contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  win.setMenuBarVisibility(false);
  win.loadFile(path.join(__dirname, "index.html"));
}

app.whenReady().then(() => {
  createWindow();
  app.on("activate", () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
});
app.on("window-all-closed", () => { if (current) current.kill(); app.quit(); });
