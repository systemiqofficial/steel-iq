"use strict";
/* Run table: the picker every pane shows while nothing is picked, one row per run, with a details panel. */

function rtEl(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

const rtCount = (n, noun) => n + " " + noun + (n === 1 ? "" : "s");

// "2026-10-20T21:11:34" to the minute.
const runTableDate = run => run.started.slice(0, 16).replace("T", " ");

// One run's details, as a row under its own spanning the table: an overview of what sets the run apart, then the
// sections of set.settings_layout with a contents list beside them.
function runTableDetails(run, span, set) {
  const tr = rtEl("tr", "rt-details");
  const td = tr.insertCell();
  td.colSpan = span;
  const ctx = { settings: new Map(run.settings.map(s => [s.key, s])), placed: new Map(), sections: [], pinned: 0 };
  const sheet = rtEl("div", "rd-sheet");
  for (const section of set.settings_layout) {
    const before = ctx.placed.size;
    const node = rtEl("section", "rd-sec");
    if (section.tiles) node.appendChild(rdOverview(ctx, section, run));
    else {
      const groups = rtEl("div", "rd-groups");
      for (const g of section.groups) groups.appendChild(rdGroup(ctx, g));
      node.append(rtEl("h3", null, section.title), groups);
    }
    sheet.appendChild(node);
    ctx.sections.push({ title: section.title, node, states: [...ctx.placed.values()].slice(before).map(p => p.s) });
  }
  // A setting the layout does not name yet still shows.
  const other = run.settings.filter(s => !ctx.placed.has(s.key));
  if (other.length) {
    const node = rtEl("section", "rd-sec");
    const body = rtEl("div", "rd-body rd-cols");
    for (const s of other) body.appendChild(rdRow(ctx, [s.key, s.label, ""]));
    node.append(rtEl("h3", null, "Other"), body);
    sheet.appendChild(node);
    ctx.sections.push({ title: "Other", node, states: other });
  }
  rdDiff(ctx, set.runs.length > 1);
  const toc = rtEl("div", "rd-toc");
  for (const section of ctx.sections) {
    const b = rtEl("button");
    b.type = "button";
    const dots = rtEl("span", "rd-tdots");
    if (section.states.some(s => s.changed)) dots.appendChild(rtEl("i", "rd-c"));
    if (section.states.some(s => s.varies)) dots.appendChild(rtEl("i", "rd-v"));
    b.append(rtEl("span", null, section.title), dots);
    b.onclick = () => { rdPin(ctx, section); section.node.scrollIntoView({ behavior: "smooth", block: "start" }); };
    section.button = b;
    toc.appendChild(b);
  }
  const layout = rtEl("div", "rd-layout");
  layout.append(toc, sheet);
  const box = rtEl("div", "rd");
  box.appendChild(layout);
  td.appendChild(box);
  // The caller places the row; the contents follow the pane it lands in.
  requestAnimationFrame(() => rdFollow(ctx, tr));
  return tr;
}

function rdPlace(ctx, s, node) {
  if (!ctx.placed.has(s.key)) ctx.placed.set(s.key, { s, node });
}

// Blue for a change from the default, purple for a difference between the listed runs.
function rdDots(target, states) {
  const changed = states.some(s => s.changed), varies = states.some(s => s.varies);
  if (!changed && !varies) return;
  const dots = rtEl("span", "rd-dots");
  if (changed) dots.appendChild(rtEl("i", "rd-c"));
  if (varies) dots.appendChild(rtEl("i", "rd-v"));
  target.appendChild(dots);
}

// A value with its unit muted, a switch as a dot, a list as chips, and the default under a changed one.
function rdValue(s, unit) {
  const box = rtEl("span", "rd-val");
  const mark = s.changed ? " rd-hl" : "";
  if (s.items && s.items.length) {
    const chips = rtEl("span", "rd-chips" + mark);
    for (const item of s.items) chips.appendChild(rtEl("span", "rd-chip", item));
    box.appendChild(chips);
  } else if (s.bool !== undefined) {
    box.appendChild(rtEl("span", "rd-sw" + (s.bool ? "" : " rd-sw-off") + mark,
                         s.value.charAt(0).toUpperCase() + s.value.slice(1)));
  } else {
    const number = unit && s.value.endsWith(" " + unit) ? s.value.slice(0, -unit.length - 1) : s.value;
    box.appendChild(rtEl("span", (s.value === "none" ? "rd-none" : "") + mark, number));
    if (number !== s.value) box.appendChild(rtEl("small", null, unit));
  }
  if (s.changed) box.appendChild(rtEl("span", "rd-def", "default " + s.default));
  return box;
}

// One layout row, [key, label, unit]; null for a setting this run's config lacks (an older run).
function rdRow(ctx, [key, label, unit], lines) {
  const s = ctx.settings.get(key);
  if (!s) return null;
  const row = rtEl("div", lines ? "rd-line" : "rd-row" + (s.items && s.items.length ? " rd-stack" : ""));
  row.append(rtEl("span", "rd-key", label), rdValue(s, unit));
  rdDots(row, [s]);
  rdPlace(ctx, s, row);
  return row;
}

// A group of rows or of one drawn kind, greyed out while its switch is off.
function rdGroup(ctx, g) {
  const box = rtEl("div", "rd-group" + (g.wide ? " rd-wide" : ""));
  const sw = g.switch ? ctx.settings.get(g.switch) : null;
  if (g.title) {
    const head = rtEl("div", "rd-gh", g.title);
    if (sw) {
      head.appendChild(rdValue(sw));
      rdDots(head, [sw]);
      rdPlace(ctx, sw, head);
    }
    box.appendChild(head);
  }
  const off = sw && !sw.bool;
  if (off) box.classList.add("rd-off");
  const note = [off ? g.off : null, g.note].filter(Boolean).join(". ");
  if (note) box.appendChild(rtEl("div", "rd-note", note));
  const body = rtEl("div", "rd-body" + (g.columns ? " rd-cols" : ""));
  for (const row of g.rows) {
    const node = rdRow(ctx, row, g.lines);
    if (node) body.appendChild(node);
  }
  if (g.kind) body.appendChild(RD_KINDS[g.kind](ctx, g));
  box.appendChild(body);
  return box;
}

// When each technology is available, drawn against the run's years, and which never build greenfield.
function rdTechnologies(ctx, g) {
  const frag = document.createDocumentFragment();
  const start = +ctx.settings.get("start_year").value, end = +ctx.settings.get("end_year").value;
  const pos = y => (Math.min(Math.max(y, start), end + 1) - start) / (end + 1 - start) * 100;
  const decades = [];
  for (let y = Math.ceil((start + 3) / 10) * 10; y <= end - 3; y += 10) decades.push(y);
  const greenfield = ctx.settings.get(g.greenfield);
  const grid = rtEl("div", "rd-tech");
  const axis = rtEl("div", "rd-axis");
  for (const [y, cls] of [[start, "rd-first"], ...decades.map(y => [y, null]), [end, "rd-last"]]) {
    const tick = rtEl("span", cls, String(y));
    tick.style.left = (cls === "rd-last" ? 100 : pos(y)) + "%";
    axis.appendChild(tick);
  }
  grid.append(rtEl("div", "rd-th", "Technology"), rtEl("div", "rd-th", "Available"), axis,
              rtEl("div", "rd-th", "Greenfield"));
  const keys = [...ctx.settings.keys()].filter(k => k.startsWith(g.prefix));
  let family = null, unknown = false;
  for (const code of new Set(keys.map(k => k.slice(g.prefix.length).split(".")[0]))) {
    // CCS and CCU variants follow their base route.
    const base = code.replace(/CC[SU]$/, "");
    if (family !== null && base !== family) grid.appendChild(rtEl("div", "rd-gap"));
    family = base;
    const states = ["allowed", "from_year", "to_year"].map(f => ctx.settings.get(g.prefix + code + "." + f));
    const allowed = states[0].bool, from = +states[1].value;
    const to = /^\d+$/.test(states[2].value) ? +states[2].value : null;
    unknown = unknown || states.some(s => s.changed === null);
    const lo = Math.max(from, start), hi = to === null ? end : Math.min(to, end);
    const text = !allowed ? "Not allowed"
      : lo > hi ? (from > end ? "From " + from + ", after the run" : "Until " + to + ", before the run")
      : from <= start && to === null ? "Whole run"
      : to === null ? "From " + from
      : from <= start ? "Until " + to
      : from + " to " + to;
    const name = rtEl("div", "rd-code" + (allowed ? "" : " rd-no"), code);
    rdDots(name, states);
    const changed = states.filter(s => s.changed);
    const when = rtEl("div", "rd-win" + (allowed ? "" : " rd-no"));
    when.appendChild(rtEl("span", changed.length ? "rd-hl" : null, text));
    if (changed.length) when.title = changed.map(s => s.label + ": default " + s.default).join("\n");
    const track = rtEl("div", "rd-track");
    for (const y of decades) {
      const line = rtEl("s");
      line.style.left = pos(y) + "%";
      track.appendChild(line);
    }
    if (allowed && lo <= hi) {
      const fill = rtEl("b");
      fill.style.left = pos(lo) + "%";
      fill.style.width = pos(hi + 1) - pos(lo) + "%";
      track.appendChild(fill);
    } else if (allowed) track.classList.add("rd-out");
    const gf = rtEl("div");
    if (greenfield && greenfield.items.includes(code))
      gf.appendChild(rtEl("span", "rd-gf" + (greenfield.changed ? " rd-hl" : ""), "Excluded"));
    grid.append(name, when, track, gf);
    for (const s of states) rdPlace(ctx, s, name);
  }
  if (greenfield) rdPlace(ctx, greenfield, grid);
  if (unknown) frag.appendChild(rtEl("div", "rd-note", "Defaults unknown without the run's technologies.json"));
  frag.appendChild(grid);
  return frag;
}

// Land cover factors as bars from 1× to the largest.
function rdBars(ctx, g) {
  const states = [...ctx.settings.values()].filter(s => s.key.startsWith(g.prefix));
  const top = Math.max(2, ...states.map(s => parseFloat(s.value)));
  const list = rtEl("div", "rd-cols2");
  for (const s of states) {
    const row = rtEl("div", "rd-bar");
    const track = rtEl("div", "rd-track");
    const fill = rtEl("b");
    fill.style.left = "0";
    fill.style.width = Math.max(2, (parseFloat(s.value) - 1) / (top - 1) * 100) + "%";
    track.appendChild(fill);
    row.append(rtEl("span", "rd-key", s.key.slice(g.prefix.length)), track,
               rtEl("span", "rd-num" + (s.changed ? " rd-hl" : ""), s.value));
    if (s.changed) row.title = "default " + s.default;
    rdDots(row, [s]);
    rdPlace(ctx, s, row);
    list.appendChild(row);
  }
  return list;
}

// Each region with the regions it imports hydrogen from.
function rdRegions(ctx, g) {
  const list = rtEl("div", "rd-cols2");
  for (const s of ctx.settings.values()) {
    if (!s.key.startsWith(g.prefix)) continue;
    const row = rtEl("div", "rd-region");
    row.append(rtEl("span", "rd-key", s.key.slice(g.prefix.length)), rdValue(s));
    rdDots(row, [s]);
    rdPlace(ctx, s, row);
    list.appendChild(row);
  }
  return list;
}

const RD_KINDS = { technologies: rdTechnologies, bars: rdBars, regions: rdRegions };

// The run's facts and scenario tiles; rdDiff fills the lists below them once every setting is placed.
function rdOverview(ctx, section, run) {
  const box = rtEl("div");
  const link = rtEl("a", null, run.dir + " ↗");
  link.href = run.link;
  link.target = "_blank";
  link.rel = "noopener";
  const meta = rtEl("div", "rd-meta");
  meta.append("Input data ", rtEl("b", null, run.input_data), " · Made in ", rtEl("b", null, run.made_in),
              " · Started ", rtEl("b", null, runTableDate(run)), " · Folder ", link);
  const tiles = rtEl("div", "rd-tiles");
  for (const [label, keys] of section.tiles) {
    const states = keys.map(k => ctx.settings.get(k)).filter(Boolean);
    if (!states.length) continue;
    const tile = rtEl("div", "rd-tile");
    const name = rtEl("div", "rd-tile-k", label);
    rdDots(name, states);
    tile.append(name, rtEl("div", "rd-tile-v" + (states.some(s => s.changed) ? " rd-hl" : ""),
                           states.map(s => s.value).join(" → ")));
    for (const s of states) rdPlace(ctx, s, tile);
    tiles.appendChild(tile);
  }
  ctx.diff = rtEl("div", "rd-diff");
  box.append(meta, tiles, ctx.diff);
  return box;
}

// What differs from the defaults and, with more than one run listed, between the runs; each item jumps to its row.
function rdDiff(ctx, several) {
  const placed = [...ctx.placed.values()];
  const line = (title, mark, entries, describe) => {
    const head = rtEl("div", "rd-dl");
    head.append(rtEl("i", mark), title + " (" + entries.length + ")");
    const list = rtEl("div", "rd-jumps");
    for (const e of entries) {
      const b = rtEl("button", "rd-jump");
      b.type = "button";
      b.append(rtEl("i", mark), ...describe(e.s));
      b.onclick = () => rdJump(ctx, e);
      list.appendChild(b);
    }
    if (!entries.length) list.appendChild(rtEl("span", "rd-none", "None"));
    ctx.diff.append(head, list);
  };
  line("Changed from the default", "rd-c", placed.filter(e => e.s.changed),
       s => [s.label, rtEl("b", null, s.value), rtEl("span", null, "default " + s.default)]);
  if (several)
    line("Varies across the listed runs", "rd-v", placed.filter(e => e.s.varies),
         s => [s.label, rtEl("b", null, s.value)]);
}

function rdJump(ctx, entry) {
  rdPin(ctx, ctx.sections.find(s => s.node.contains(entry.node)));
  entry.node.scrollIntoView({ behavior: "smooth", block: "center" });
  entry.node.classList.remove("rd-flash");
  void entry.node.offsetWidth;   // restarts the flash
  entry.node.classList.add("rd-flash");
}

// The contents mark the section under the table header; a clicked section holds while the smooth scroll passes the
// ones before it, until the reader scrolls by hand.
function rdFollow(ctx, tr) {
  const pane = tr.closest(".scenario-overview");
  if (!pane) return;
  ctx.pane = pane;
  const release = () => { ctx.pinned = 0; };
  const hand = ["wheel", "touchstart", "keydown", "mousedown"];
  const onScroll = () => {
    if (tr.isConnected) { rdMark(ctx); return; }
    pane.removeEventListener("scroll", onScroll);
    for (const type of hand) pane.removeEventListener(type, release);
  };
  pane.addEventListener("scroll", onScroll, { passive: true });
  for (const type of hand) pane.addEventListener(type, release, { passive: true });
  rdMark(ctx);
}

function rdMark(ctx) {
  let current = ctx.sections[0];
  if (Date.now() < ctx.pinned) current = ctx.pinnedSection;
  else {
    const line = ctx.pane.getBoundingClientRect().top + 60;
    for (const s of ctx.sections) if (s.node.getBoundingClientRect().top <= line) current = s;
  }
  for (const s of ctx.sections) s.button.classList.toggle("rd-on", s === current);
}

function rdPin(ctx, section) {
  ctx.pinnedSection = section;
  ctx.pinned = Date.now() + 1200;
  if (ctx.pane) rdMark(ctx);
}

// The scenario behind each row, for applyRunTableFilters.
const runTableRowScenario = new WeakMap();

function applyRunTableFilters(box, set, filters) {
  for (const tr of box.querySelectorAll(".rt-row")) {
    const sc = runTableRowScenario.get(tr);
    const out = set.dimensions.filter(d => filters[d.key] != null && filters[d.key] !== sc.dims[d.key]);
    tr.classList.toggle("rt-filtered", out.length > 0);
    tr.title = (out.length ? "Filtered out in the sidebar by " + out.map(d => dimPair(d, filters[d.key])).join(", ")
      + "\n\n" : "") + tr.dataset.title;
  }
}

// mode: "pick", "pair" (an A/B toggle), "multi" (ticks up to max, then Show) or "side" (a split side).
function renderRunTable(box, set, scenarios, opts) {
  const mode = opts.mode;
  const picked = new Set(opts.picked || []);
  const otherMark = mode === "side" && opts.other ? (opts.side === "A" ? "B" : "A") : null;
  box.replaceChildren();
  if (mode !== "side") {
    const variant = set.dimensions.find(d => d.key === opts.variantKey);
    const seeds = variant ? variant.values : [];
    box.append(rtEl("h2", "rt-title", set.label),
      rtEl("div", "rt-counts", rtCount(set.runs.length, "run") + " · " + rtCount(scenarios.length, "scenario")
        + (seeds.length ? " · seed" + (seeds.length > 1 ? "s " : " ") + seeds.join(", ") : "")));
  }

  // Split-view picking ("pair" mode): the A pick waits on its row until B is clicked.
  let pairing = false, pickA = null;
  const modeButtons = [];
  if (mode === "pair") {
    const modeRow = rtEl("div", "rt-mode pills");
    modeRow.appendChild(rtEl("span", "rt-mode-label", "Click a run to"));
    for (const [on, text] of [[false, "explore it"], [true, "compare two side by side"]]) {
      const b = rtEl("button", null, text);
      b.onclick = () => { pairing = on; markA(null); renderHint(); };
      modeRow.appendChild(b);
      modeButtons.push([on, b]);
    }
    box.appendChild(modeRow);
  }
  const hint = rtEl("div", "rt-hint");
  box.appendChild(hint);
  function markA(row) {
    if (pickA) { pickA.tr.classList.remove("rt-a"); pickA.tr.querySelector(".rt-ab").remove(); }
    pickA = row;
    if (row) { row.tr.classList.add("rt-a"); row.tr.cells[0].appendChild(rtEl("span", "rt-ab", "A")); }
  }
  function renderHint() {
    for (const [on, b] of modeButtons) b.classList.toggle("active", on === pairing);
    if (mode === "side") {
      hint.replaceChildren(rtEl("span", "ab-chip", opts.side),
                           "Pick scenario " + opts.side + ": click a run, here or in the sidebar.");
      return;
    }
    hint.textContent = mode === "multi"
      ? "Click runs to pick their scenarios, up to " + opts.max + ", then press Show. Details lists a run's settings."
      : pairing && pickA ? "Run A is " + pickA.run.name + ". Now click run B (click A again to unpick it)."
      : pairing ? "Click run A, then run B, to open them side by side."
      : (opts.pickHint || "Click a run, or a scenario in the sidebar, to explore its plots")
        + ". Details lists a run's settings.";
  }

  const table = rtEl("table", "rt-table");
  const head = table.createTHead().insertRow();
  for (const text of ["#", "Name", "Started", "Input data", ""]) head.appendChild(rtEl("th", null, text));
  const tbody = table.createTBody();
  const rows = [];
  for (const run of set.runs) {
    const sc = scenarios.find(x => x.runs.includes(run));
    const tr = tbody.insertRow();
    tr.className = "rt-row";
    tr.append(rtEl("td", "rt-tag", scenarioTag(sc)), rtEl("td", "rt-name", run.name),
              rtEl("td", "rt-date", runTableDate(run)), rtEl("td", "rt-input", run.input_data));
    const more = rtEl("a", "rt-more", "details");
    more.title = "Show this run's settings";
    const moreCell = rtEl("td");
    moreCell.appendChild(more);
    tr.appendChild(moreCell);
    tr.title = tr.dataset.title = scenarioLabel(set, sc) + "\n" + run.dir;
    runTableRowScenario.set(tr, sc);
    if (otherMark && sc === opts.other) {
      tr.classList.add("rt-" + otherMark.toLowerCase());
      tr.cells[0].appendChild(rtEl("span", "rt-ab", otherMark));
    }
    let details = null;
    more.onclick = e => {
      e.stopPropagation();
      if (details) details.remove();
      details = details ? null : runTableDetails(run, tr.cells.length, set);
      if (details) tr.after(details);
      more.textContent = details ? "hide" : "details";
    };
    const row = { tr, sc, run };
    tr.onclick = () => {
      if (!tr.classList.contains("rt-filtered") || tr.classList.contains("rt-picked")) onRow(row);
    };
    rows.push(row);
  }
  box.appendChild(table);

  let update = () => {};
  const markPicked = () => { for (const r of rows) r.tr.classList.toggle("rt-picked", picked.has(r.sc)); };
  function onRow(row) {
    if (mode === "multi") {
      if (picked.has(row.sc)) picked.delete(row.sc);
      else if (picked.size < opts.max) picked.add(row.sc);
      markPicked();
      update();
    } else if (pairing) {
      if (!pickA) markA(row);
      else if (pickA.run === row.run) markA(null);
      else { const a = pickA.run; markA(null); opts.onPickPair(a, row.run); }
      renderHint();
    } else opts.onPick(row.sc, row.run);
  }

  if (mode === "multi") {
    const bar = rtEl("div", "rt-showbar");
    const count = rtEl("span", "rt-showcount");
    const clear = rtEl("a", "rt-clear", "clear");
    clear.title = "Unpick every scenario";
    const show = rtEl("button", "rt-show", "Show");
    bar.append(count, clear, show);
    update = () => {
      const full = picked.size >= opts.max;
      count.textContent = picked.size + " of " + opts.max + " picked" + (full ? " (unpick one to pick another)" : "");
      clear.hidden = show.disabled = !picked.size;
      box.classList.toggle("rt-full", full);
    };
    clear.onclick = () => { picked.clear(); markPicked(); update(); };
    show.onclick = () => opts.onShow(scenarios.filter(sc => picked.has(sc)));
    box.appendChild(bar);
    markPicked();
  }
  update();
  renderHint();
}
