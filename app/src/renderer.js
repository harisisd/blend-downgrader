"use strict";
const $ = (s) => document.querySelector(s);
// `api` disediakan preload.js lewat contextBridge (window.api)

const state = {
  files: [],          // {id, path, name, size, version:[M,m], label, status, result}
  blenders: [],       // {exe, version, label, name, managed}
  settings: {},
  busy: false,
  nextId: 1,
};

const STATUS = {
  ready: ["Siap", ""], export: ["Membaca file…", "run"], import: ["Membangun ulang…", "run"],
  verify: ["Mengecek hasil…", "run"], ok: ["Berhasil", "ok"], warn: ["Berhasil + catatan", "warn"],
  err: ["Gagal", "err"], skip: ["Tidak perlu", ""],
};

const cmp = (a, b) => (a[0] - b[0]) || (a[1] - b[1]) || ((a[2] || 0) - (b[2] || 0));
const mm = (v) => `${v[0]}.${v[1]}`;
const fmtSize = (n) => n > 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB";

// ---------------------------------------------------------------- log
function log(line, kind = "log") {
  const el = $("#log");
  const s = document.createElement("span");
  if (kind !== "log") s.className = kind;
  s.textContent = line + "\n";
  el.appendChild(s);
  el.scrollTop = el.scrollHeight;
  if (kind === "error" || kind === "warn") {
    const b = $("#logBadge"); b.hidden = false; b.textContent = (+b.textContent || 0) + 1;
  }
}
$("#logToggle").onclick = () => {
  const open = $("#logToggle").getAttribute("aria-expanded") !== "true";
  $("#logToggle").setAttribute("aria-expanded", open);
  $("#log").hidden = !open;
  if (open) $("#logBadge").hidden = true, $("#logBadge").textContent = "";
};

// ---------------------------------------------------------------- blenders
function sourceBlender() {
  const s = state.settings.sourceExe;
  if (s && s !== "auto") return state.blenders.find((b) => b.exe === s) || null;
  return state.blenders[0] || null; // terbaru
}
function targetBlender() {
  return state.blenders.find((b) => b.exe === $("#selTarget").value) || null;
}

function fillSelects() {
  const tSel = $("#selTarget"), sSel = $("#selSource");
  tSel.innerHTML = ""; sSel.innerHTML = "";
  const newest = state.blenders[0];
  const olds = state.blenders.filter((b) => !newest || cmp(b.version, newest.version) < 0);

  if (!olds.length) {
    const o = new Option("Belum ada Blender versi lama, unduh dulu", ""); o.disabled = true; o.selected = true;
    tSel.add(o);
  }
  for (const b of olds) tSel.add(new Option(`Blender ${b.label}${b.managed ? "  (diunduh app ini)" : ""}`, b.exe));
  let want = state.settings.targetExe;
  if (!olds.some((b) => b.exe === want)) {
    // default: versi lama paling baru di bawah 4.1
    want = (olds.find((b) => cmp(b.version, [4, 1]) < 0) || olds[0] || {}).exe;
  }
  if (want) tSel.value = want;

  sSel.add(new Option(newest ? `Otomatis (terbaru: Blender ${newest.label})` : "Tidak ditemukan", "auto"));
  for (const b of state.blenders) sSel.add(new Option(`Blender ${b.label}`, b.exe));
  sSel.value = state.settings.sourceExe && state.blenders.some((b) => b.exe === state.settings.sourceExe) ? state.settings.sourceExe : "auto";
  refresh();
}

async function loadBlenders() {
  $("#targetHint").textContent = "Mencari Blender yang terpasang…";
  state.blenders = await api.listBlenders();
  fillSelects();
  renderManage();
}

$("#selTarget").onchange = () => { api.setSettings({ targetExe: $("#selTarget").value }); state.settings.targetExe = $("#selTarget").value; refresh(); };
$("#selSource").onchange = () => { api.setSettings({ sourceExe: $("#selSource").value }); state.settings.sourceExe = $("#selSource").value; refresh(); };

// ---------------------------------------------------------------- files
async function addPaths(paths) {
  for (const p of paths) {
    if (!/\.blend$/i.test(p)) { log(`Dilewati (bukan .blend): ${p}`, "warn"); continue; }
    if (state.files.some((f) => f.path === p)) continue;
    try {
      const info = await api.probe(p);
      state.files.push({ id: state.nextId++, ...info, status: "ready", result: null });
    } catch (e) {
      log(`${p}: ${e.message.replace(/^Error invoking remote method '[^']+': (Error: )?/, "")}`, "error");
    }
  }
  render();
}

const drop = $("#drop");
["dragenter", "dragover"].forEach((ev) => document.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) => document.addEventListener(ev, (e) => {
  e.preventDefault();
  if (ev === "dragleave" && e.relatedTarget) return;
  drop.classList.remove("over");
}));
document.addEventListener("drop", (e) => {
  if (state.busy) return;
  const paths = [...e.dataTransfer.files].map((f) => api.pathFor(f)).filter(Boolean);
  addPaths(paths);
});
$("#btnPick").onclick = async () => addPaths(await api.pickFiles());
drop.onclick = async (e) => { if (e.target === drop && !state.busy) addPaths(await api.pickFiles()); };
$("#btnClear").onclick = () => { if (!state.busy) { state.files = []; render(); } };

function render() {
  const list = $("#list");
  list.innerHTML = "";
  const tgt = targetBlender();
  for (const f of state.files) {
    const li = $("#tplRow").content.firstElementChild.cloneNode(true);
    li.querySelector(".fname").textContent = f.name;
    li.querySelector(".fname").title = f.path;
    li.querySelector(".fsize").textContent = fmtSize(f.size) + (f.compressed ? " · terkompresi" : "");
    li.querySelector(".from").textContent = "Blender " + f.label;
    li.querySelector(".to").textContent = tgt ? "Blender " + mm(tgt.version) : "?";
    const [txt, cls] = STATUS[f.status] || STATUS.ready;
    const pill = li.querySelector(".pill");
    pill.textContent = txt; if (cls) pill.classList.add(cls);

    const det = li.querySelector(".r-detail");
    if (f.result) {
      det.hidden = false;
      if (f.result.ok) {
        const out = document.createElement("div"); out.className = "out"; out.textContent = f.result.out; det.appendChild(out);
        if (f.result.check) {
          const c = document.createElement("div");
          c.textContent = `Terbuka di Blender ${f.result.outVersion}: ${f.result.check.objects} obyek, ${f.result.check.meshes} mesh, ${f.result.check.materials} material`;
          det.appendChild(c);
        }
        if (f.result.warnings.length) {
          const ul = document.createElement("ul"); ul.className = "w";
          for (const w of f.result.warnings) { const x = document.createElement("li"); x.textContent = w; ul.appendChild(x); }
          det.appendChild(ul);
        }
        const bOpen = li.querySelector(".act-open"), bShow = li.querySelector(".act-show");
        bOpen.hidden = bShow.hidden = false;
        bOpen.textContent = `Buka di ${mm(f.result.target.version)}`;
        bOpen.onclick = () => api.openIn(f.result.target.exe, f.result.out);
        bShow.onclick = () => api.reveal(f.result.out);
      } else {
        const e = document.createElement("div"); e.className = "e"; e.textContent = f.result.error; det.appendChild(e);
      }
    } else if (tgt && cmp(f.version, tgt.version) <= 0) {
      det.hidden = false; det.textContent = `File ini sudah bisa dibuka langsung di Blender ${mm(tgt.version)}.`;
    }
    li.querySelector(".act-del").onclick = () => { if (!state.busy) { state.files = state.files.filter((x) => x !== f); render(); } };
    li.querySelector(".act-del").disabled = state.busy;
    list.appendChild(li);
  }
  drop.classList.toggle("compact", state.files.length > 0);
  $("#listHead").hidden = !state.files.length;
  $("#count").textContent = `${state.files.length} file`;
  refresh();
}

// ---------------------------------------------------------------- settings
function refresh() {
  const tgt = targetBlender(), src = sourceBlender();
  const th = $("#targetHint"); th.className = "hint";
  if (!state.blenders.length) { th.textContent = "Blender tidak ditemukan. Buka \"Kelola Blender\" untuk menambah atau mengunduh."; th.classList.add("bad"); }
  else if (!tgt) { th.textContent = "Unduh Blender versi lama lewat \"Kelola Blender\"."; th.classList.add("warnc"); }
  else th.textContent = `Hasil bisa dibuka di Blender ${mm(tgt.version)} dan biasanya juga versi ${tgt.version[0]}.x sebelumnya.`;

  const maxFile = state.files.reduce((m, f) => (!m || cmp(f.version, m) > 0 ? f.version : m), null);
  if (src && maxFile && cmp(src.version, maxFile) < 0) {
    th.textContent = `Ada file dari Blender ${mm(maxFile)}, lebih baru dari Blender pembuka (${src.label}). Akan dicoba; bila gagal, pasang Blender ${mm(maxFile)}+.`;
    th.className = "hint warnc";
  }
  const todo = state.files.filter((f) => f.status !== "ok" && f.status !== "warn");
  const btn = $("#btnConvert");
  btn.textContent = state.busy ? "Mengonversi…" : todo.length ? `Konversi ${todo.length} file` : "Konversi";
  btn.disabled = state.busy || !todo.length || !tgt || !src;
  $("#btnCancel").hidden = !state.busy;
  $("#outHint").textContent = state.settings.outDir || "Hasil: namafile_blenderX.Y.blend di samping file asli";
  $("#outSame").classList.toggle("on", !state.settings.outDir);
  $("#outPick").classList.toggle("on", !!state.settings.outDir);
}

$("#outSame").onclick = () => { state.settings.outDir = null; api.setSettings({ outDir: null }); refresh(); };
$("#outPick").onclick = async () => {
  const d = await api.pickFolder();
  if (d) { state.settings.outDir = d; api.setSettings({ outDir: d }); refresh(); }
};
for (const [id, key] of [["optApply", "apply"], ["optPack", "pack"], ["optVerify", "verify"]]) {
  $("#" + id).onchange = (e) => { state.settings[key] = e.target.checked; api.setSettings({ [key]: e.target.checked }); };
}

// ---------------------------------------------------------------- convert
api.on("job:log", ({ line, kind }) => log(line, kind));
api.on("job:status", ({ id, status, step }) => {
  const f = state.files.find((x) => x.id === id);
  if (f) { f.status = status; render(); }
  const total = state.queue || 1, idx = state.queueIdx || 0;
  const steps = $("#optVerify").checked ? 3 : 2;
  $("#prog").style.width = `${Math.round(((idx + (step - 1) / steps) / total) * 100)}%`;
});

$("#btnConvert").onclick = async () => {
  const tgt = targetBlender(), src = sourceBlender();
  const todo = state.files.filter((f) => f.status !== "ok" && f.status !== "warn");
  if (!todo.length || !tgt || !src) return;
  state.busy = true; state.queue = todo.length; state.queueIdx = 0;
  $("#progWrap").hidden = false; $("#prog").style.width = "0";
  render();
  const opts = {
    source: src, target: tgt, outDir: state.settings.outDir || null,
    keepModifiers: !$("#optApply").checked, pack: $("#optPack").checked, verify: $("#optVerify").checked,
  };
  let okN = 0;
  for (const f of todo) {
    if (!state.busy) break;
    $("#progText").textContent = `File ${state.queueIdx + 1} dari ${todo.length}: ${f.name}`;
    log(`\n=== ${f.name} (Blender ${f.label} → ${tgt.label}) ===`, "step");
    if (cmp(f.version, tgt.version) <= 0) {
      f.status = "skip"; f.result = { ok: false, error: `Tidak perlu dikonversi: file sudah versi ${f.label}.` };
    } else {
      const r = await api.convert({ id: f.id, path: f.path, version: f.version }, opts);
      f.result = { ...r, target: tgt };
      f.status = r.ok ? (r.warnings.length ? "warn" : "ok") : "err";
      if (r.ok) { okN++; log(`OK → ${r.out}`, "step"); } else log(`GAGAL: ${r.error}`, "error");
    }
    state.queueIdx++;
    render();
  }
  $("#prog").style.width = "100%";
  $("#progText").textContent = `Selesai: ${okN} dari ${todo.length} file berhasil.`;
  state.busy = false;
  render();
};
$("#btnCancel").onclick = () => { state.busy = false; api.cancel(); $("#progText").textContent = "Membatalkan…"; };

// ---------------------------------------------------------------- kelola Blender
const dlg = $("#dlgManage");
$("#btnManage").onclick = () => { renderManage(); dlg.showModal(); };

function renderManage() {
  const ul = $("#blenderList"); ul.innerHTML = "";
  if (!state.blenders.length) { const li = document.createElement("li"); li.className = "empty"; li.textContent = "Belum ada Blender yang terdeteksi."; ul.appendChild(li); }
  const custom = new Set(state.settings.customBlenders || []);
  for (const b of state.blenders) {
    const li = document.createElement("li");
    const badge = document.createElement("span"); badge.className = "badge"; badge.textContent = b.label;
    const p = document.createElement("span"); p.className = "p"; p.textContent = b.exe; p.title = b.exe;
    li.append(badge, p);
    if (b.managed || custom.has(b.exe)) {
      const del = document.createElement("button"); del.type = "button"; del.className = "ghost sm";
      del.textContent = b.managed ? "Hapus" : "Lepas";
      del.title = b.managed ? "Hapus Blender yang diunduh app ini" : "Lepas dari daftar (file tidak dihapus)";
      del.onclick = async () => {
        if (b.managed && !confirm(`Hapus Blender ${b.label} yang diunduh app ini dari disk?`)) return;
        state.blenders = await api.removeBlender(b.exe);
        state.settings = await api.getSettings(); fillSelects(); renderManage();
      };
      li.appendChild(del);
    }
    ul.appendChild(li);
  }
}

$("#btnAdd").onclick = async () => {
  try {
    const r = await api.addBlender();
    if (r) { state.blenders = r; state.settings = await api.getSettings(); fillSelects(); renderManage(); }
  } catch (e) { alert(e.message.replace(/^Error invoking remote method '[^']+': (Error: )?/, "")); }
};

async function resolveSeries() {
  const s = $("#selSeries").value;
  $("#dlInfo").textContent = "Mencari versi terbaru…";
  try {
    const r = await api.resolveRelease(s);
    $("#dlInfo").textContent = `Akan mengunduh Blender ${r.version} (${r.file})`;
  } catch (e) { $("#dlInfo").textContent = e.message.replace(/^Error invoking remote method '[^']+': (Error: )?/, ""); }
}
$("#selSeries").onchange = resolveSeries;

api.on("dl:progress", (p) => {
  const bar = $("#dlProg");
  if (p.phase === "start") $("#dlInfo").textContent = `Mengunduh Blender ${p.version}…`;
  if (p.phase === "download" && p.total) {
    bar.style.width = `${(p.got / p.total) * 100}%`;
    $("#dlInfo").textContent = `Mengunduh… ${(p.got / 1048576).toFixed(0)} / ${(p.total / 1048576).toFixed(0)} MB`;
  }
  if (p.phase === "install") { bar.style.width = "100%"; $("#dlInfo").textContent = "Memasang…"; }
  if (p.phase === "done") $("#dlInfo").textContent = `Blender ${p.version} terpasang.`;
});

$("#btnDownload").onclick = async () => {
  const s = $("#selSeries").value;
  $("#btnDownload").disabled = true; $("#btnDlCancel").hidden = false; $("#dlWrap").hidden = false; $("#dlProg").style.width = "0";
  try {
    await api.installBlender(s);
    state.blenders = await api.listBlenders();
    const fresh = state.blenders.find((b) => b.managed && mm(b.version) === s);
    if (fresh) { state.settings.targetExe = fresh.exe; api.setSettings({ targetExe: fresh.exe }); }
    fillSelects(); renderManage();
  } catch (e) {
    $("#dlInfo").textContent = "Gagal: " + e.message.replace(/^Error invoking remote method '[^']+': (Error: )?/, "");
  } finally {
    $("#btnDownload").disabled = false; $("#btnDlCancel").hidden = true;
  }
};
$("#btnDlCancel").onclick = () => api.cancel();

// ---------------------------------------------------------------- init
(async () => {
  state.settings = await api.getSettings();
  $("#optApply").checked = state.settings.apply ?? true;
  $("#optPack").checked = state.settings.pack ?? true;
  $("#optVerify").checked = state.settings.verify ?? true;
  for (const s of await api.series()) $("#selSeries").add(new Option(`Blender ${s}${["3.3", "3.6", "4.2", "4.5"].includes(s) ? " LTS" : ""}`, s));
  $("#selSeries").value = "3.6";
  refresh();
  await loadBlenders();
  resolveSeries();
})();
