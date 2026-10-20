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

// A null flag: no technologies.json for a technology setting, or a field without a default.
function runTableChanged(s) {
  if (s.changed === null) return s.key.startsWith("technology_settings.") ? "defaults unknown" : "no default";
  return s.changed ? "changed" : null;
}

// One run's details, as a row under its own spanning the table.
function runTableDetails(run, span) {
  const tr = rtEl("tr", "rt-details");
  const td = tr.insertCell();
  td.colSpan = span;
  const facts = rtEl("dl", "rt-facts");
  for (const [label, value] of [["Name", run.name], ["Folder", run.dir], ["Made in", run.made_in],
                                ["Started", runTableDate(run)], ["Input data", run.input_data]])
    facts.append(rtEl("dt", null, label), rtEl("dd", null, value));
  const link = rtEl("a", null, run.link);
  link.href = run.link;
  link.target = "_blank";
  link.rel = "noopener";
  const linkCell = rtEl("dd");
  linkCell.appendChild(link);
  facts.append(rtEl("dt", null, "Run folder"), linkCell);
  const legend = rtEl("div", "rt-legend");
  legend.append(rtEl("span", "rt-mark rt-changed", "changed"), " from the default · ",
                rtEl("span", "rt-mark rt-varies", "varies"), " across the listed runs · ",
                rtEl("span", "rt-mark rt-unknown", "defaults unknown"), " without the run's technologies.json · ",
                rtEl("span", "rt-mark rt-unknown", "no default"), " for a setting without one");
  const list = rtEl("ul", "rt-settings");
  for (const s of run.settings) {
    const li = rtEl("li");
    li.append(rtEl("span", "rt-set-label", s.label + ": "), rtEl("span", null, s.value));
    const changed = runTableChanged(s);
    if (changed) li.appendChild(rtEl("span", "rt-mark " + (s.changed ? "rt-changed" : "rt-unknown"), changed));
    if (s.varies) li.appendChild(rtEl("span", "rt-mark rt-varies", "varies"));
    list.appendChild(li);
  }
  td.append(facts, legend, list);
  return tr;
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
      hint.replaceChildren(rtEl("span", "ab-chip", opts.side), "Pick scenario " + opts.side + ": click a run, here or in the sidebar.");
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
      details = details ? null : runTableDetails(run, tr.cells.length);
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
