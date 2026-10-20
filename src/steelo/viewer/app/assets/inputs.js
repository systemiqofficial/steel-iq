"use strict";
/* View data pane: the runs' input data by topic, a row that differs followed by one tagged row per variant. */

const INPUTS_FILE = "inputs.json";
const INPUTS_PAGE = 500;   // table rows per page
const INPUTS_YEARS = [2025, 2060];   // year columns shown by default, clamped to the sheet's
const INPUTS_TOTALS = new Set(["demand", "scrap"]);   // year topics whose rows add up
const INPUTS_MAX_LINES = 300;   // lines charted at most; the rest waits for a narrower view
const INPUTS_CHART_SURFACE = "#ffffff";

function createInputsPane(container, set, opts) {
  const topicsBox = opts.topicsBox;
  const onChange = opts.onChange || (() => {});

  container.replaceChildren();
  const header = document.createElement("header");
  const viewRow = document.createElement("div");
  viewRow.className = "menurow catrow";
  const filterRow = document.createElement("div");
  filterRow.className = "menurow";
  const legendRow = document.createElement("div");
  legendRow.className = "menurow";
  header.append(viewRow, filterRow, legendRow);
  const body = document.createElement("div");
  body.className = "compare-body inputs-body";
  const title = document.createElement("div");
  title.className = "compare-title";
  const subtitle = document.createElement("div");
  subtitle.className = "compare-sub";
  const message = document.createElement("div");
  message.className = "compare-message";
  const factsBox = document.createElement("div");
  factsBox.className = "inputs-table inputs-facts";
  factsBox.hidden = true;
  const tableBox = document.createElement("div");
  tableBox.className = "inputs-table";
  const chartBox = document.createElement("div");
  chartBox.className = "compare-chart";
  chartBox.hidden = true;
  const pager = document.createElement("div");
  pager.className = "inputs-pager";
  const note = document.createElement("div");
  note.className = "compare-note";
  body.append(title, subtitle, message, factsBox, tableBox, chartBox, pager, note);
  let plotted = false, chartToken = 0;
  new ResizeObserver(() => {
    if (plotted && !chartBox.hidden && window.Plotly && chartBox.clientWidth) window.Plotly.Plots.resize(chartBox);
  }).observe(chartBox);
  const controls = attachBarControls(container, header, "databarCollapsed");
  container.append(header, controls.hresizer, controls.collapseRow, body);

  const state = { topic: null, selection: null, changedOnly: false, search: "", page: 0,
                  sort: null,        // { col: index over key then value columns, dir: 1 | -1 }
                  colFilters: {},    // key column index -> Set of kept values (absent = all)
                  years: null,       // [from, to] of the year columns shown (year topics)
                  view: "table" };   // "table" | "lines" | "total" (chartable topics)
  const distinctCache = new Map();   // topic id -> per key column, its sorted distinct values
  let data = null, loadError = null;
  const loading = fetch(INPUTS_FILE)
    .then(async res => { if (!res.ok) throw new Error(await res.text() || res.status + " " + res.statusText); return res.json(); })
    .then(d => { data = d; })
    .catch(e => { loadError = e.message; });

  const dimLabel = key => (data.dims.find(d => d.key === key) || { label: key }).label;
  const condText = cond => Object.entries(cond).map(([k, vals]) => dimLabel(k) + " = " + vals.join(" / ")).join(" · ");
  const baseText = () => data.dims.map(d => dimLabel(d.key) + " " + data.base[d.key]).join(" · ");
  const topicOf = id => data.topics.find(t => t.id === id) || null;
  const titleOf = t => t.label + (t.unit ? " (" + t.unit + ")" : "");

  /* ---- which variants are in view ---- */
  // A tagged variant applies on every listed value; one the diff could not explain lists its runs.
  const applies = (v, sc) => v.cond ? Object.entries(v.cond).every(([k, vals]) => vals.includes(sc.dims[k]))
    : (v.runs || []).some(dir => sc.runs.some(r => r.dir === dir));
  const variantFor = (r, sc) => r.v.slice(1).find(v => applies(v, sc)) || r.v[0];
  // The variants the matching scenarios use (all of them before the sidebar reports a selection).
  const variantsFor = r => {
    if (!state.selection) return r.v;
    const used = new Set(state.selection.scenarios.map(sc => variantFor(r, sc)));
    return r.v.filter(v => used.has(v));
  };
  const differs = r => variantsFor(r).some(v => v !== r.v[0]);
  const nDiffer = t => t.rows.filter(differs).length;
  const filtered = () => !!state.selection && Object.values(state.selection.filters).some(v => v != null);
  const filtersText = () => Object.entries(state.selection.filters).filter(([, v]) => v != null)
    .map(([k, v]) => dimLabel(k) + " " + v).join(" · ");

  const rowlabel = (row, text) => {
    const s = document.createElement("span");
    s.className = "rowlabel";
    s.textContent = text;
    row.appendChild(s);
  };
  const pill = (row, label, active, onclick) => {
    const b = document.createElement("button");
    b.textContent = label;
    b.classList.toggle("active", !!active);
    b.onclick = onclick;
    row.appendChild(b);
    return b;
  };

  function renderTopics() {
    topicsBox.replaceChildren();
    const head = document.createElement("div");
    head.className = "list-head";
    const lab = document.createElement("span");
    lab.className = "section-title";
    lab.textContent = "Input data";
    head.appendChild(lab);
    const list = document.createElement("div");
    list.className = "runs";
    topicsBox.append(head, list);
    if (!data) {
      const d = document.createElement("div");
      d.className = "empty";
      d.textContent = loadError ? "No input data for these runs (" + loadError + ")." : "Loading…";
      list.appendChild(d);
      return;
    }
    for (const t of data.topics) {
      const b = document.createElement("button");
      const name = document.createElement("span");
      name.className = "scn-name";
      name.textContent = t.label;
      const n = nDiffer(t);
      const count = document.createElement("span");
      count.className = "scn topic-count";
      count.textContent = n ? n.toLocaleString("en-GB") + " differ" : filtered() ? "as base" : "same in all";
      b.append(name, count);
      b.classList.toggle("active", state.topic === t.id);
      b.onclick = () => { state.search = ""; state.page = 0; show(t.id); };
      list.appendChild(b);
    }
  }

  function renderViewRow(t) {
    viewRow.replaceChildren();
    controls.setSummary(t ? titleOf(t) : "View data");
    if (!t) { filterRow.hidden = true; legendRow.replaceChildren(); return; }
    rowlabel(viewRow, "Rows");
    pill(viewRow, "All", !state.changedOnly, () => { state.changedOnly = false; state.page = 0; redraw(); });
    pill(viewRow, "Differing only", state.changedOnly, () => { state.changedOnly = true; state.page = 0; redraw(); });
    if (chartable(t)) {
      rowlabel(viewRow, "Show");
      const views = [["table", "Table", "the rows in view as a table"],
                     ["lines", "Chart: lines", "one line per row in view (dashed: a scenario variant)"]];
      if (additive(t)) views.push(["total", "Chart: total", "the rows in view summed, one line per scenario setting in view"]);
      for (const [id, label, tip] of views)
        pill(viewRow, label, state.view === id, () => { state.view = id; renderViewRow(t); renderTable(t); }).title = tip;
    }
    rowlabel(viewRow, "Find");
    const search = document.createElement("input");
    search.type = "search";
    search.className = "inputs-search";
    search.placeholder = t.key_columns.join(" / ");
    search.value = state.search;
    search.oninput = () => { state.search = search.value; state.page = 0; renderTable(t); };
    viewRow.appendChild(search);
    const exportBtn = document.createElement("button");
    exportBtn.className = "small gap";
    exportBtn.textContent = "Export CSV";
    exportBtn.onclick = () => exportCsv(t);
    viewRow.appendChild(exportBtn);

    renderFilterRow(t);
    legendRow.replaceChildren();
    const sw = document.createElement("span");
    sw.className = "inputs-legend";
    const swatch = document.createElement("span");
    swatch.className = "swatch changed";
    sw.append(swatch, "differs between scenarios — the tag names the filter setting that activates the row; "
      + "untagged rows are the base (" + baseText() + "). The sidebar filters keep only the variants "
      + "the matching scenarios use.");
    legendRow.appendChild(sw);
  }

  /* ---- per-column filters ---- */
  function distinctKeys(t) {
    if (!distinctCache.has(t.id))
      distinctCache.set(t.id, t.key_columns.map((_, i) =>
        [...new Set(t.rows.map(r => r.k[i]))].sort((a, b) => cmpValues(a, b))));
    return distinctCache.get(t.id);
  }
  const keyText = v => v == null ? "(blank)" : String(v);

  function filterDropdown(t, i, values) {
    const root = document.createElement("span");
    root.className = "dd";
    const toggle = document.createElement("button");
    toggle.className = "dd-toggle";
    const panel = document.createElement("div");
    panel.className = "dd-panel";
    panel.hidden = true;
    root.append(toggle, panel);
    const label = t.key_columns[i];
    const kept = () => state.colFilters[i];   // undefined = all
    const updateToggle = () => {
      toggle.textContent = (kept() ? label + ": " + kept().size + " of " + values.length : label) + " ▾";
      toggle.classList.toggle("active", !!kept());
    };
    const cbs = new Map();
    const sync = () => { for (const [v, cb] of cbs) cb.checked = !kept() || kept().has(v); updateToggle(); };
    const apply = () => { state.page = 0; sync(); renderTable(t); };
    const setAll = on => { if (on) delete state.colFilters[i]; else state.colFilters[i] = new Set(); apply(); };
    const flip = (v, on) => {
      const s = kept() || (state.colFilters[i] = new Set(values));
      on ? s.add(v) : s.delete(v);
      if (s.size === values.length) delete state.colFilters[i];
      apply();
    };
    const tools = document.createElement("div");
    tools.className = "dd-tools";
    for (const [text, on] of [["All", true], ["None", false]]) {
      const b = document.createElement("button");
      b.className = "small";
      b.textContent = text;
      b.onclick = () => setAll(on);
      tools.appendChild(b);
    }
    panel.appendChild(tools);
    const list = document.createElement("div");
    if (values.length > 12) {
      const find = document.createElement("input");
      find.type = "search";
      find.className = "dd-search";
      find.placeholder = "find…";
      find.oninput = () => {
        const q = find.value.trim().toLowerCase();
        for (const [v, cb] of cbs) cb.parentElement.hidden = !!q && !keyText(v).toLowerCase().includes(q);
      };
      panel.appendChild(find);
    }
    for (const v of values) {
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.onchange = () => flip(v, cb.checked);
      const row = document.createElement("label");
      row.append(cb, keyText(v));
      list.appendChild(row);
      cbs.set(v, cb);
    }
    panel.appendChild(list);
    toggle.onclick = () => {
      const open = panel.hidden;
      filterRow.querySelectorAll(".dd-panel").forEach(p => { p.hidden = true; });
      panel.hidden = !open;
      if (open) {
        const r = toggle.getBoundingClientRect();
        panel.style.position = "fixed";
        panel.style.left = r.left + "px";
        panel.style.top = (r.bottom + 4) + "px";
      }
    };
    sync();
    return root;
  }
  document.addEventListener("click", e => {
    if (!filterRow.contains(e.target)) filterRow.querySelectorAll(".dd-panel").forEach(p => { p.hidden = true; });
  });

  /* ---- year range ---- */
  const yearsOf = t => t.value_columns.map(c => /^\d{4}$/.test(c) ? +c : null);
  const yearSpan = t => { const ys = yearsOf(t).filter(Boolean); return ys.length > 1 ? [Math.min(...ys), Math.max(...ys)] : null; };
  const defaultYears = span => {
    const lo = Math.max(INPUTS_YEARS[0], span[0]), hi = Math.min(INPUTS_YEARS[1], span[1]);
    return lo <= hi ? [lo, hi] : [...span];
  };
  // Value columns in view: every non-year column, and the years inside the range.
  function visibleColumns(t) {
    const ys = yearsOf(t);
    if (!state.years) return t.value_columns.map((_, j) => j);
    return t.value_columns.map((_, j) => j).filter(j => ys[j] == null || (ys[j] >= state.years[0] && ys[j] <= state.years[1]));
  }

  // Two range inputs on one track; only their thumbs take the pointer.
  function yearSlider(t, span) {
    const wrap = document.createElement("span");
    wrap.className = "yrange";
    const [min, max] = span;
    const box = () => { const i = document.createElement("input"); i.type = "number"; i.min = min; i.max = max; i.step = 1; return i; };
    const from = box(), to = box();
    const track = document.createElement("span");
    track.className = "track";
    const rail = document.createElement("span"); rail.className = "rail";
    const band = document.createElement("span"); band.className = "band";
    const slider = () => { const i = document.createElement("input"); i.type = "range"; i.min = min; i.max = max; i.step = 1; return i; };
    const lo = slider(), hi = slider();
    track.append(rail, band, lo, hi);
    const lab = document.createElement("span");
    lab.className = "rowlabel";
    lab.textContent = "Years";
    wrap.append(lab, from, track, to);
    const paint = () => {
      const [a, b] = state.years;
      lo.value = a; hi.value = b; from.value = a; to.value = b;
      band.style.left = (100 * (a - min) / (max - min)) + "%";
      band.style.width = (100 * (b - a) / (max - min)) + "%";
      // The thumb last moved sits on top so both stay reachable at the ends.
      lo.style.zIndex = a === max ? 3 : 1; hi.style.zIndex = a === max ? 1 : 2;
    };
    const clamp = (v, fallback) => Number.isFinite(v) ? Math.min(max, Math.max(min, v)) : fallback;
    const set = (a, b, redrawTable) => {
      a = clamp(a, state.years[0]); b = clamp(b, state.years[1]);
      state.years = [Math.min(a, b), Math.max(a, b)];
      paint();
      if (redrawTable) renderTable(t);
    };
    lo.oninput = () => set(Math.min(+lo.value, state.years[1]), state.years[1], false);
    hi.oninput = () => set(state.years[0], Math.max(+hi.value, state.years[0]), false);
    lo.onchange = hi.onchange = () => set(state.years[0], state.years[1], true);
    from.onchange = () => set(+from.value, state.years[1], true);
    to.onchange = () => set(state.years[0], +to.value, true);
    paint();
    return wrap;
  }

  function renderFilterRow(t) {
    filterRow.replaceChildren();
    const distinct = distinctKeys(t);
    const dds = t.key_columns.map((_, i) => distinct[i].length > 1 ? filterDropdown(t, i, distinct[i]) : null).filter(Boolean);
    const span = yearSpan(t);
    filterRow.hidden = !dds.length && !span;
    if (filterRow.hidden) return;
    if (dds.length) { rowlabel(filterRow, "Filter"); filterRow.append(...dds); }
    if (span) filterRow.appendChild(yearSlider(t, span));
  }

  /* ---- sorting ---- */
  // Numbers before text, blanks last; text compares with numeric awareness.
  function cmpValues(a, b) {
    if (a == null || b == null) return a == null ? (b == null ? 0 : 1) : -1;
    const na = typeof a === "number", nb = typeof b === "number";
    if (na && nb) return a - b;
    if (na !== nb) return na ? -1 : 1;
    return String(a).localeCompare(String(b), "en", { numeric: true, sensitivity: "base" });
  }
  // A value column sorts on the row's first variant in view, so variants stay under their row.
  function sortRows(t, rows) {
    if (!state.sort) return rows;
    const { col, dir } = state.sort;
    const nk = t.key_columns.length;
    const keyOf = col < nk ? r => r.k[col]
      : r => { const v = variantsFor(r).find(v => v.vals); return v ? v.vals[col - nk] : null; };
    return rows.map((r, i) => [r, i, keyOf(r)])
      .sort((x, y) => {
        const c = cmpValues(x[2], y[2]);
        return (x[2] == null || y[2] == null ? c : c * dir) || x[1] - y[1];
      })
      .map(x => x[0]);
  }

  const fmt = v => v == null ? "–" : typeof v !== "number" ? String(v)
    : Number.isInteger(v) || Math.abs(v) >= 100 ? Math.round(v).toLocaleString("en-GB")
    : Math.abs(v) >= 1 ? v.toFixed(2) : Number(v.toPrecision(3)).toString();

  // A base row absent from the base masters is skipped.
  function visibleRows(t) {
    const q = state.search.trim().toLowerCase();
    const filters = Object.entries(state.colFilters).map(([i, set]) => [+i, set]);
    const rows = sortRows(t, t.rows.filter(r => (!state.changedOnly || differs(r))
      && filters.every(([i, set]) => set.has(r.k[i]))
      && (!q || r.k.some(k => k != null && String(k).toLowerCase().includes(q)))));
    const out = [];
    for (const r of rows) for (const v of variantsFor(r)) if (v.vals || v.cond) out.push([r, v]);
    return out;
  }

  const chartable = t => !!yearSpan(t);
  const additive = t => INPUTS_TOTALS.has(t.id);

  /* ---- charts ---- */
  const rowName = (r, v) => r.k.filter(k => k != null).join(" · ") + (v.cond ? " · " + condText(v.cond) : "");
  // Rows carry their own unit when the topic has none and a key column names it.
  const unitColumn = t => t.unit ? -1 : t.key_columns.findIndex(c => /^unit/i.test(c));
  function chartTraces(t, rows, cols) {
    const years = cols.map(j => yearsOf(t)[j]);
    const num = v => typeof v === "number" && Number.isFinite(v) ? v : null;
    // The totals view's unified hover heads its box with the year.
    const trace = (name, y, i, variant, few, unit) => ({
      type: "scatter", mode: few ? "lines+markers" : "lines", name, x: years, y,
      hovertemplate: (state.view === "lines" ? "%{x} · " : "") + "%{y:,.2~f} " + (unit || "") + "<extra>%{fullData.name}</extra>",
      marker: { size: 4 },
      line: { width: variant ? 1.5 : 2, color: COMPARE_PALETTE[i % COMPARE_PALETTE.length], dash: variant ? "dash" : "solid" },
    });
    if (state.view === "lines") {
      const drawn = rows.filter(([, v]) => v.vals).slice(0, INPUTS_MAX_LINES);
      const uc = unitColumn(t);
      const few = drawn.length <= 12;
      // One panel per unit: traces are tagged with their panel's axes.
      const panels = [];
      return drawn.map(([r, v], i) => {
        const unit = uc >= 0 ? (r.k[uc] == null ? "" : String(r.k[uc])) : t.unit;
        let p = panels.indexOf(unit);
        if (p < 0) { p = panels.length; panels.push(unit); }
        const ax = p ? String(p + 1) : "";
        return { ...trace(rowName(r, v), cols.map(j => num(v.vals[j])), i, !!v.cond, few, unit), xaxis: "x" + ax, yaxis: "y" + ax, _panel: unit };
      });
    }
    // Scenarios using the same variants share one totals line, named by the settings involved.
    const keyRows = [...new Set(rows.map(([r]) => r))];
    const scenarios = state.selection ? state.selection.scenarios : [];
    const groups = new Map();
    for (const sc of scenarios) {
      const picks = keyRows.map(r => variantFor(r, sc));
      const sig = picks.map(v => v.cond ? condText(v.cond) : "").join("|");
      if (!groups.has(sig)) {
        const settings = [...new Set(picks.filter(v => v.cond).map(v => condText(v.cond)))];
        groups.set(sig, { name: "Total" + (settings.length ? " · " + settings.join(" · ") : " (base)"), picks, variant: settings.length > 0 });
      }
    }
    return [...groups.values()].map((g, i) =>
      trace(g.name, cols.map(j => g.picks.reduce((s, v) => s + (v.vals ? num(v.vals[j]) || 0 : 0), 0)), i, g.variant, true, t.unit));
  }

  async function renderChart(t, rows) {
    const token = ++chartToken;
    const cols = visibleColumns(t).filter(j => yearsOf(t)[j] != null);
    const traces = chartTraces(t, rows, cols);
    const Plotly = await loadPlotly().catch(e => { note.textContent = e.message; return null; });
    if (!Plotly || token !== chartToken) return;
    const many = traces.length > 12;
    const panels = [...new Set(traces.map(tr => tr._panel == null ? t.unit : tr._panel))];
    const xaxis = { gridcolor: "#eceef2", zeroline: false, dtick: cols.length > 40 ? 10 : 5 };
    const yaxis = unit => ({ title: { text: unit || "unit per row", font: { size: 12 } }, gridcolor: "#eceef2",
                             zeroline: true, zerolinecolor: "#c9ced6", tickformat: ",.2~f", rangemode: "tozero" });
    const layout = {
      margin: { l: 70, r: 20, t: 10, b: 40 },
      hovermode: state.view === "lines" ? "closest" : "x unified",
      paper_bgcolor: INPUTS_CHART_SURFACE, plot_bgcolor: INPUTS_CHART_SURFACE,
      font: { family: '-apple-system, "Segoe UI", sans-serif', size: 12.5, color: "#1f2733" },
      xaxis, yaxis: yaxis(panels[0]),
      legend: { orientation: "v", x: 1.01, y: 1, font: { size: many ? 10.5 : 12 } },
      showlegend: true,
    };
    // A panel per unit, stacked, each with its own y-axis and a heading.
    if (panels.length > 1) {
      layout.grid = { rows: panels.length, columns: 1, pattern: "independent", roworder: "top to bottom", ygap: 0.25 };
      layout.annotations = [];
      panels.forEach((unit, p) => {
        const ax = p ? String(p + 1) : "";
        layout["xaxis" + ax] = { ...xaxis };
        layout["yaxis" + ax] = yaxis(unit);
        layout.annotations.push({ xref: "x" + ax + " domain", yref: "y" + ax + " domain", x: 0, y: 1, xanchor: "left",
          yanchor: "bottom", yshift: 4, showarrow: false, font: { size: 12.5 }, text: "<b>" + (unit || "no unit") + "</b>" });
      });
      layout.height = panels.length * 260 + 60;
      layout.margin.t = 30;   // room for the first panel's heading
    }
    chartBox.classList.toggle("facets", panels.length > 1);
    Plotly.react(chartBox, traces.map(({ _panel, ...tr }) => tr), layout,
                 { responsive: true, displaylogo: false, modeBarButtonsToRemove: ["select2d", "lasso2d", "autoScale2d"] });
    plotted = true;
    const span = yearSpan(t);
    const inView = rows.filter(([, v]) => v.vals).length;
    note.textContent = (state.view === "lines"
      ? (inView > traces.length
          ? "The first " + traces.length + " of " + inView.toLocaleString("en-GB") + " rows in view, one line each — narrow the filters to see the rest"
          : traces.length + " line" + (traces.length === 1 ? "" : "s") + ", one per row in view")
        + (panels.length > 1 ? ", one panel per unit" : "") + "; dashed lines are scenario variants. "
      : traces.length + " total" + (traces.length === 1 ? "" : "s") + " over the rows in view, one per scenario setting the view contains"
        + " (a variant row replaces its base row in its own total). ")
      + "Years " + state.years[0] + "–" + state.years[1] + " of " + span[0] + "–" + span[1] + " in the sheet"
      + (t.unit ? ", " + t.unit : "") + ".";
  }

  // Fixed rows of a year topic: key columns and the one value, no years.
  function renderFacts(t, rows) {
    factsBox.replaceChildren();
    factsBox.hidden = !rows.length;
    if (!rows.length) return;
    const table = document.createElement("table");
    const head = table.createTHead().insertRow();
    for (const h of [...t.key_columns, "Scenario setting", "Value"]) {
      const th = document.createElement("th");
      th.textContent = h;
      head.appendChild(th);
    }
    const tbody = table.createTBody();
    for (const [r, v] of rows) {
      const tr = tbody.insertRow();
      if (v.cond) tr.className = "changed";
      r.k.forEach(k => { const td = tr.insertCell(); td.textContent = k == null ? "" : k; td.className = "key"; });
      const tag = tr.insertCell();
      tag.className = "tag";
      if (v.cond) {
        const chip = document.createElement("span");
        chip.className = "cond";
        chip.textContent = condText(v.cond);
        tag.appendChild(chip);
      }
      tr.insertCell().textContent = fmt(v.vals ? v.vals[0] : null);
    }
    factsBox.appendChild(table);
  }

  function renderTable(t) {
    tableBox.replaceChildren();
    const all = visibleRows(t);
    renderFacts(t, all.filter(([r]) => r.fixed));
    const rows = all.filter(([r]) => !r.fixed);
    const chart = chartable(t) && state.view !== "table";
    tableBox.hidden = chart;
    chartBox.hidden = !chart;
    if (chart) { pager.hidden = true; renderChart(t, rows); return; }
    const table = document.createElement("table");
    const head = table.createTHead().insertRow();
    const nk = t.key_columns.length;
    const cols = visibleColumns(t);
    // Column index over keys then values (the tag column in between is not sortable).
    const sortable = [...t.key_columns.map((h, i) => [h, i]), ["Scenario setting", null],
                      ...cols.map(j => [t.value_columns[j], nk + j])];
    for (const [h, col] of sortable) {
      const th = document.createElement("th");
      const on = state.sort && state.sort.col === col;
      th.textContent = h + (on ? (state.sort.dir > 0 ? " ▲" : " ▼") : "");
      if (col != null) {
        th.className = "sortable" + (on ? " sorted" : "");
        th.title = on ? "Sort " + (state.sort.dir > 0 ? "descending" : "ascending") : "Sort by " + h;
        th.onclick = () => {
          state.sort = on ? { col, dir: -state.sort.dir } : { col, dir: 1 };
          state.page = 0;
          renderTable(t);
        };
      }
      head.appendChild(th);
    }
    const pages = Math.max(1, Math.ceil(rows.length / INPUTS_PAGE));
    state.page = Math.min(state.page, pages - 1);
    const start = state.page * INPUTS_PAGE;
    const tbody = table.createTBody();
    for (const [r, v] of rows.slice(start, start + INPUTS_PAGE)) {
      const tr = tbody.insertRow();
      if (v.cond) tr.className = "changed";
      r.k.forEach(k => { const td = tr.insertCell(); td.textContent = k == null ? "" : k; td.className = "key"; });
      const tag = tr.insertCell();
      tag.className = "tag";
      if (v.cond) {
        const chip = document.createElement("span");
        chip.className = "cond";
        chip.textContent = condText(v.cond);
        tag.appendChild(chip);
        if (!v.vals) tag.append(" row absent");
      } else if (!v.vals) tag.textContent = "absent in the base";
      for (const j of cols) {
        const td = tr.insertCell();
        td.textContent = fmt(v.vals ? v.vals[j] : null);
      }
    }
    tableBox.appendChild(table);
    tableBox.scrollTop = 0;
    renderPager(t, rows.length, pages, start);
    const span = yearSpan(t);
    note.textContent = (span ? "Years " + state.years[0] + "–" + state.years[1] + " shown of " + span[0] + "–" + span[1]
      + " in the sheet. " : "")
      + "Values as in the master workbook" + (t.unit ? " (" + t.unit + ")" : "") + ".";
  }

  // Prev / Next, a page number to type, and the rows on show.
  function renderPager(t, total, pages, start) {
    pager.replaceChildren();
    pager.hidden = pages <= 1;
    if (pages <= 1) return;
    const n = v => v.toLocaleString("en-GB");
    const go = page => { state.page = Math.min(Math.max(0, page), pages - 1); renderTable(t); };
    const btn = (label, page, disabled) => {
      const b = document.createElement("button");
      b.textContent = label;
      b.disabled = disabled;
      b.onclick = () => go(page);
      pager.appendChild(b);
    };
    const span = text => { const s = document.createElement("span"); s.textContent = text; pager.appendChild(s); };
    span("Rows " + n(start + 1) + "–" + n(Math.min(start + INPUTS_PAGE, total)) + " of " + n(total));
    btn("‹ Prev", state.page - 1, state.page === 0);
    span("Page");
    const input = document.createElement("input");
    input.type = "number"; input.min = 1; input.max = pages; input.value = state.page + 1;
    input.onchange = () => go(+input.value - 1);
    pager.appendChild(input);
    span("of " + n(pages));
    btn("Next ›", state.page + 1, state.page === pages - 1);
  }

  function exportCsv(t) {
    const rows = visibleRows(t);
    const cols = visibleColumns(t);
    const header = [...t.key_columns, "Scenario setting", ...cols.map(j => t.value_columns[j])];
    const esc = v => typeof v === "number" ? String(v) : `"${String(v == null ? "" : v).replace(/"/g, '""')}"`;
    const lines = [header.map(esc).join(",")];
    for (const [r, v] of rows)
      lines.push([...r.k, (v.cond ? condText(v.cond) + (v.vals ? "" : " (absent)") : "base") + (r.fixed ? " (fixed value)" : ""),
                  ...cols.map(j => v.vals ? v.vals[j] : null)].map(esc).join(","));
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/csv" }));
    a.download = "inputs_" + t.id + "_" + set.id + ".csv";
    a.click();
  }

  function showMessage(text) {
    message.className = "compare-message";
    message.textContent = text;
    message.hidden = !text;
    title.hidden = subtitle.hidden = tableBox.hidden = !!text;
    if (text) { note.textContent = ""; chartBox.hidden = true; pager.hidden = true; factsBox.hidden = true; }
  }

  async function redraw() {
    await loading;
    renderTopics();
    if (!data) {
      renderViewRow(null);
      showMessage("No input data found for these runs (" + loadError + ").");
      return;
    }
    const t = topicOf(state.topic);
    if (!t) {
      state.topic = null;
      renderViewRow(null);
      showMessage("Pick an input data sheet in the sidebar to see its rows.");
      if (opts.renderEmpty) {
        message.className = "scenario-overview";
        opts.renderEmpty(message);
      }
      return;
    }
    const span = yearSpan(t);
    if (span && !state.years) state.years = defaultYears(span);
    // A chart view carries over between year topics; totals only where rows add up.
    if (!chartable(t)) state.view = "table";
    else if (state.view === "total" && !additive(t)) state.view = "lines";
    renderTopics();
    renderViewRow(t);
    showMessage("");
    title.textContent = titleOf(t);
    const n = nDiffer(t);
    const sel = state.selection;
    subtitle.textContent = [
      !sel ? null : filtered()
        ? filtersText() + " (" + sel.scenarios.length + " of " + sel.total + " scenarios)"
        : "all " + sel.total + " scenarios",
      t.note, "sheet “" + t.sheet + "”",
      t.rows.length.toLocaleString("en-GB") + " rows, " + (n
        ? n.toLocaleString("en-GB") + " differ between " + (filtered() ? "the scenarios in view" : "scenarios")
        : filtered() ? "all as in the base" : "identical in every scenario"),
    ].filter(Boolean).join(" · ");
    renderTable(t);
  }

  // The topic is only known once the data is: the change fires after the draw.
  function show(topicId, selection) {
    if (topicId && topicId !== state.topic) { state.sort = null; state.colFilters = {}; state.years = null; }
    if (topicId) state.topic = topicId;
    if (selection !== undefined) state.selection = selection;
    return redraw().then(onChange);
  }

  renderTopics();
  showMessage("Pick an input data sheet in the sidebar to see its rows.");
  return {
    show,
    clearTopic() { state.topic = null; return redraw().then(onChange); },
    setSelection: sel => { state.page = 0; return show(undefined, sel); },
    getTopic() { const t = data && state.topic && topicOf(state.topic); return t ? { id: t.id, label: t.label } : null; },
    getTopics: () => data ? data.topics.map(t => t.label) : [],
  };
}
