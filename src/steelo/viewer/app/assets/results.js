"use strict";
/* Download results pane: one run's result tables by folder, as gzipped CSV downloads or paged tables. */

const RESULTS_PAGE = 500;   // table rows per page; a whole number of pages fits a chunk

function createResultsPane(container, set, opts = {}) {
  container.replaceChildren();
  const body = document.createElement("div");
  body.className = "compare-body results-body";
  const title = document.createElement("div");
  title.className = "compare-title";
  const subtitle = document.createElement("div");
  subtitle.className = "compare-sub";
  const message = document.createElement("div");
  message.className = "compare-message";
  const actions = document.createElement("div");
  actions.className = "results-actions";
  const folders = document.createElement("div");
  folders.className = "results-folders";
  const viewTitle = document.createElement("div");
  viewTitle.className = "results-view-title";
  const tableBox = document.createElement("div");
  tableBox.className = "inputs-table results-table";
  const pager = document.createElement("div");
  pager.className = "inputs-pager";
  body.append(title, subtitle, message, actions, folders, viewTitle, tableBox, pager);
  container.append(body);

  const base = set.base.replace(/\/$/, "");
  const state = { run: null, index: null, folder: null, file: null, page: 0, token: 0, chunks: new Map(),
                  folded: new Set() };   // folder ids folded away; kept across scenarios
  const n = v => v.toLocaleString("en-GB");
  const mb = b => (b / 1e6).toFixed(b < 1e6 ? 2 : 1) + " MB";
  const runUrl = path => base + "/" + state.run.dir + "/viewer/results/" + path;

  function clearView() {
    state.file = null;
    state.chunks.clear();
    viewTitle.hidden = tableBox.hidden = pager.hidden = true;
    tableBox.replaceChildren();
  }

  function showMessage(text) {
    message.className = "compare-message";
    message.hidden = false;
    message.textContent = text;
    actions.hidden = folders.hidden = true;
    clearView();
  }

  // The given run, else the open one while it belongs to the scenario, else the scenario's first.
  function show(scenario, given) {
    const run = !scenario ? null : given && scenario.runs.includes(given) ? given
      : scenario.runs.includes(state.run) ? state.run : scenario.runs[0];
    if (run && run === state.run && state.index) return;   // back from another view: keep the open table
    const token = ++state.token;
    state.run = run;
    state.index = null;
    title.textContent = scenario ? scenarioLabel(set, scenario) : "";
    subtitle.textContent = run ? "Run folder " + run.dir : "";
    if (!scenario) {
      showMessage("Pick a scenario in the sidebar to download or view its result tables.");
      if (opts.renderEmpty) {
        message.className = "scenario-overview";
        opts.renderEmpty(message);
      }
      return;
    }
    showMessage("Loading…");
    fetch(runUrl("index.json"))
      .then(r => { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(index => { if (token === state.token) { state.index = index; renderFolders(); } })
      .catch(() => { if (token === state.token) showMessage("No result tables for this set's runs."); });
  }

  function renderFolders() {
    message.hidden = true;
    actions.hidden = folders.hidden = false;
    const files = state.index.folders.flatMap(d => d.files);
    subtitle.textContent = "Run folder " + state.run.dir + " · " + files.length + " tables, "
      + mb(files.reduce((s, f) => s + f.bytes, 0))
      + " as gzipped CSV (.csv.gz: double-click to unpack; Windows may need 7-Zip).";
    actions.replaceChildren();
    const all = document.createElement("button");
    all.className = "results-button primary";
    all.textContent = "Download all (.zip)";
    const progress = document.createElement("span");
    progress.className = "results-progress";
    all.onclick = () => downloadAll(all, progress);
    actions.append(all, progress);

    folders.replaceChildren();
    for (const d of state.index.folders) {
      const box = document.createElement("section");
      box.className = "results-folder" + (state.folded.has(d.id) ? " folded" : "");
      const h = document.createElement("button");
      h.className = "results-folder-head section-title";
      h.setAttribute("aria-expanded", String(!state.folded.has(d.id)));
      h.innerHTML = '<svg class="chevron" width="12" height="12" viewBox="0 0 24 24" fill="none" '
        + 'stroke="currentColor" stroke-width="2.4"><path d="M6 9l6 6 6-6"/></svg>';
      const count = document.createElement("span");
      count.className = "results-meta";
      count.textContent = d.files.length + " tables";
      h.append(d.label, count);
      h.onclick = () => {
        const folded = box.classList.toggle("folded");
        if (folded) state.folded.add(d.id); else state.folded.delete(d.id);
        h.setAttribute("aria-expanded", String(!folded));
      };
      const table = document.createElement("table");
      for (const f of d.files) {
        const tr = table.insertRow();
        tr.className = "results-file" + (state.file === f ? " active" : "");
        tr.dataset.key = d.id + "/" + f.id;
        tr.insertCell().textContent = f.name;
        const meta = tr.insertCell();
        meta.className = "results-meta";
        meta.textContent = n(f.rows) + " rows · " + f.columns.length + " columns · " + mb(f.bytes);
        const cell = tr.insertCell();
        cell.className = "results-buttons";
        const view = document.createElement("button");
        view.className = "results-button";
        view.textContent = "View";
        view.onclick = () => openTable(d, f);
        const get = document.createElement("button");
        get.className = "results-button";
        get.textContent = "Download";
        get.onclick = () => downloadOne(d, f, get);
        cell.append(view, get);
      }
      box.append(h, table);
      folders.append(box);
    }
    if (!state.file) clearView();
  }

  // A file's bytes as stored: gzip, whether or not a server unpacked it on the way.
  async function gzBytes(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error(url + ": " + r.status);
    const bytes = new Uint8Array(await r.arrayBuffer());
    if (bytes[0] === 0x1f && bytes[1] === 0x8b) return bytes;
    const packed = new Blob([bytes]).stream().pipeThrough(new CompressionStream("gzip"));
    return new Uint8Array(await new Response(packed).arrayBuffer());
  }

  async function gunzipJson(url) {
    const bytes = await gzBytes(url);
    const text = await new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"))).text();
    return JSON.parse(text);
  }

  function save(blob, name) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 10000);
  }

  async function busy(button, text, work) {
    const label = button.textContent;
    button.disabled = true;
    button.textContent = text;
    try { await work(); } catch (e) { alert("Download failed: " + e.message); }
    button.disabled = false;
    button.textContent = label;
  }

  function downloadOne(d, f, button) {
    const run = state.run;
    busy(button, "Downloading…", async () =>
      save(new Blob([await gzBytes(runUrl(d.id + "/" + f.file))], { type: "application/gzip" }),
           run.dir + "__" + f.file));
  }

  function downloadAll(button, progress) {
    const run = state.run, folderList = state.index.folders;
    const total = folderList.reduce((s, d) => s + d.files.length, 0);
    busy(button, "Preparing…", async () => {
      const entries = [];
      for (const d of folderList)
        for (const f of d.files) {
          progress.textContent = "Fetching " + (entries.length + 1) + " of " + total + ": " + f.name;
          entries.push({ name: d.label + "/" + f.file, data: await gzBytes(runUrl(d.id + "/" + f.file)) });
        }
      save(zipStore(entries), run.dir + "__results.zip");
    }).finally(() => { progress.textContent = ""; });
  }

  function openTable(d, f) {
    state.file = f;
    state.folder = d;
    state.page = 0;
    state.chunks.clear();
    for (const tr of folders.querySelectorAll(".results-file"))
      tr.classList.toggle("active", tr.dataset.key === d.id + "/" + f.id);
    viewTitle.hidden = false;
    viewTitle.textContent = d.label + " / " + f.name;
    renderPage();
  }

  // One JSON chunk holds chunk_rows rows; cached per open table.
  function chunk(i) {
    if (!state.chunks.has(i))
      state.chunks.set(i, gunzipJson(runUrl(state.folder.id + "/" + state.file.pages + "/" + i + ".json.gz"))
        .catch(e => { state.chunks.delete(i); throw e; }));
    return state.chunks.get(i);
  }

  async function renderPage() {
    const f = state.file, token = state.token;
    const pages = Math.max(1, Math.ceil(f.rows / RESULTS_PAGE));
    state.page = Math.min(Math.max(0, state.page), pages - 1);
    const start = state.page * RESULTS_PAGE;
    const per = f.chunk_rows / RESULTS_PAGE;
    tableBox.hidden = false;
    renderPager(f, pages, start);
    let rows;
    try { rows = await chunk(Math.floor(state.page / per)); }
    catch (e) { tableBox.textContent = "Could not load this page (" + e.message + ")."; return; }
    if (token !== state.token || f !== state.file) return;
    const offset = start - Math.floor(state.page / per) * f.chunk_rows;
    const table = document.createElement("table");
    const head = table.createTHead().insertRow();
    for (const c of f.columns) {
      const th = document.createElement("th");
      th.textContent = c;
      head.appendChild(th);
    }
    const tbody = table.createTBody();
    for (const r of rows.slice(offset, offset + RESULTS_PAGE)) {
      const tr = tbody.insertRow();
      for (const v of r) {
        const td = tr.insertCell();
        td.textContent = v;
        if (v !== "" && isNaN(v)) td.className = "key";
      }
    }
    tableBox.replaceChildren(table);
    tableBox.scrollTop = 0;
  }

  function renderPager(f, pages, start) {
    pager.replaceChildren();
    pager.hidden = false;
    const go = page => { state.page = page; renderPage(); };
    const btn = (label, page, disabled) => {
      const b = document.createElement("button");
      b.textContent = label;
      b.disabled = disabled;
      b.onclick = () => go(page);
      pager.appendChild(b);
    };
    const span = text => { const s = document.createElement("span"); s.textContent = text; pager.appendChild(s); };
    span("Rows " + n(Math.min(start + 1, f.rows)) + "–" + n(Math.min(start + RESULTS_PAGE, f.rows)) + " of " + n(f.rows));
    btn("« First", 0, state.page === 0);
    btn("‹ Prev", state.page - 1, state.page === 0);
    span("Page");
    const input = document.createElement("input");
    input.type = "number"; input.min = 1; input.max = pages; input.value = state.page + 1;
    input.onchange = () => go(+input.value - 1);
    pager.appendChild(input);
    span("of " + n(pages));
    btn("Next ›", state.page + 1, state.page === pages - 1);
    btn("Last »", pages - 1, state.page === pages - 1);
  }

  show(null);
  return { show };
}

// A zip of already-compressed files, stored as they are (method 0), with UTF-8 names.
function zipStore(entries) {
  const crcTable = zipStore.table || (zipStore.table = Array.from({ length: 256 }, (_, n) => {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    return c >>> 0;
  }));
  const crc32 = data => {
    let c = 0xffffffff;
    for (let i = 0; i < data.length; i++) c = crcTable[(c ^ data[i]) & 0xff] ^ (c >>> 8);
    return (c ^ 0xffffffff) >>> 0;
  };
  const now = new Date();
  const time = (now.getHours() << 11) | (now.getMinutes() << 5) | (now.getSeconds() >> 1);
  const date = ((now.getFullYear() - 1980) << 9) | ((now.getMonth() + 1) << 5) | now.getDate();
  const enc = new TextEncoder();
  const parts = [], central = [];
  let offset = 0;
  for (const e of entries) {
    const name = enc.encode(e.name), crc = crc32(e.data), size = e.data.length;
    const local = new DataView(new ArrayBuffer(30));
    [[0, 0x04034b50, 4], [4, 20, 2], [6, 0x0800, 2], [8, 0, 2], [10, time, 2], [12, date, 2], [14, crc, 4],
     [18, size, 4], [22, size, 4], [26, name.length, 2], [28, 0, 2]]
      .forEach(([at, v, len]) => len === 4 ? local.setUint32(at, v, true) : local.setUint16(at, v, true));
    parts.push(local, name, e.data);
    const cd = new DataView(new ArrayBuffer(46));
    [[0, 0x02014b50, 4], [4, 20, 2], [6, 20, 2], [8, 0x0800, 2], [10, 0, 2], [12, time, 2], [14, date, 2],
     [16, crc, 4], [20, size, 4], [24, size, 4], [28, name.length, 2], [30, 0, 2], [32, 0, 2], [34, 0, 2],
     [36, 0, 2], [38, 0, 4], [42, offset, 4]]
      .forEach(([at, v, len]) => len === 4 ? cd.setUint32(at, v, true) : cd.setUint16(at, v, true));
    central.push(cd, name);
    offset += 30 + name.length + size;
  }
  const cdSize = central.reduce((s, p) => s + p.byteLength, 0);
  const end = new DataView(new ArrayBuffer(22));
  [[0, 0x06054b50, 4], [4, 0, 2], [6, 0, 2], [8, entries.length, 2], [10, entries.length, 2], [12, cdSize, 4],
   [16, offset, 4], [20, 0, 2]]
    .forEach(([at, v, len]) => len === 4 ? end.setUint32(at, v, true) : end.setUint16(at, v, true));
  return new Blob([...parts, ...central, end], { type: "application/zip" });
}
