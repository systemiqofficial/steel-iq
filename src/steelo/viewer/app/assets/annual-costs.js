"use strict";
/* Annual costs pane: the stacked cost of a tonne of iron or crude steel for the picked scenarios.
   Values are production-weighted M USD totals, so any selection averages exactly. */

const ANNUAL_COSTS_FILE = "annual_costs.csv";

// Stack order follows the COMPARE_PALETTE slots; a prefix finds the members in the data.
const ANNUAL_COST_GROUPS = [
  { id: "materials", label: "Metallic charge", color: COMPARE_PALETTE[0], prefix: "material_",
    note: "delivered cost of scrap, hot metal, pig iron, DRI/HBI and ore by grade, incl. transport and tariffs" },
  { id: "energy", label: "Energy & reductants", color: COMPARE_PALETTE[1],
    cols: ["coking_coal", "pci", "bio_pci", "hydrogen", "natural_gas", "coal", "electricity", "steam"],
    note: "all carriers of the process, chosen reductant included" },
  { id: "fluxes", label: "Fluxes", color: COMPARE_PALETTE[2], cols: ["burnt_lime", "burnt_dolomite", "olivine"],
    note: "flux purchases" },
  { id: "byproducts", label: "By-products & CCUS", color: COMPARE_PALETTE[3],
    cols: ["ironmaking_slag", "steelmaking_slag", "bf_gas", "bof_gas", "cog", "ccu_products", "co2_stored", "electrolytic_iron"],
    note: "slag credits / disposal, process-gas credits, CO2 storage cost and CCU credits" },
  { id: "fopex", label: "Fixed opex", color: COMPARE_PALETTE[4], cols: ["fopex"],
    note: "per tonne of production, spread over max(utilisation, 10 %)" },
  { id: "financing", label: "Debt", color: COMPARE_PALETTE[5], cols: ["debt", "debt_legacy"],
    note: "principal + interest on the debt-financed share of capex (80 % for the plants the data starts with; 1 - the run's equity share for the new plants and expansions it builds), straight-line over the run's plant lifetime: the current technology's loan plus a previous technology's loan still being repaid after an early switch (split to see them apart)" },
  { id: "carbon", label: "Carbon cost", color: COMPARE_PALETTE[7], cols: ["carbon"],
    note: "the plant country's carbon price x direct emissions in the run's carbon cost emissions boundary" },
];
const ANNUAL_COST_COLUMN_LABELS = {
  coking_coal: "Coking coal", pci: "PCI", bio_pci: "Bio-PCI", hydrogen: "Hydrogen", natural_gas: "Natural gas",
  coal: "Coal", electricity: "Electricity", steam: "Steam", burnt_lime: "Burnt lime", burnt_dolomite: "Burnt dolomite",
  olivine: "Olivine", ironmaking_slag: "Ironmaking slag", steelmaking_slag: "Steelmaking slag", bf_gas: "BF gas",
  bof_gas: "BOF gas", cog: "Coke-oven gas", ccu_products: "CCU products", co2_stored: "CO2 stored",
  electrolytic_iron: "Electrolytic iron", carbon: "Carbon cost", fopex: "Fixed opex", debt: "Debt service",
  debt_legacy: "Legacy debt",
  scrap: "Scrap", hot_metal: "Hot metal", liquid_iron: "Liquid iron", pig_iron: "Pig iron",
};
// Feedstock order for the metallic charge split; unknown feedstocks sort after.
const ANNUAL_COST_MATERIAL_ORDER = ["scrap", "hot_metal", "liquid_iron", "pig_iron", "dri_high", "dri_mid", "dri_low",
                             "hbi_high", "hbi_mid", "hbi_low", "electrolytic_iron", "io_high", "io_mid", "io_low", "bio_pci"];
const ANNUAL_COST_TEXT_COLUMNS = new Set(["geo_key", "product", "technology", "reductant"]);
const ANNUAL_COST_PANEL_HEIGHT = 250;
const ANNUAL_COST_SURFACE = "#ffffff";
const ANNUAL_COST_HEADING_SIZE = 11.5;
const ANNUAL_COST_HEADING_LINE = 20;   // px a heading line takes in the band above a panel row
// A scenario name wrapping to more lines than this shows as its #n tag, named in a key above.
const ANNUAL_COST_HEADING_LINES = 3;

// A name broken at its " · " separators into lines no wider than `width` px of bold heading text.
const headingMeasure = document.createElement("canvas").getContext("2d");
function wrapHeading(text, width) {
  headingMeasure.font = `bold ${ANNUAL_COST_HEADING_SIZE}px -apple-system, "Segoe UI", sans-serif`;
  const lines = [];
  for (const part of text.split(" · ")) {
    const joined = lines.length ? lines[lines.length - 1] + " · " + part : null;
    if (joined && headingMeasure.measureText(joined).width <= width) lines[lines.length - 1] = joined;
    else lines.push(part);
  }
  return lines;
}

function annualCostMemberLabel(col) {
  if (col.startsWith("material_")) {
    const f = col.slice(9);
    const m = f.match(/^(io|dri|hbi)_(low|mid|high)$/);
    if (m) return { io: "Iron ore", dri: "DRI", hbi: "HBI" }[m[1]] + " (" + m[2] + ")";
    return ANNUAL_COST_COLUMN_LABELS[f] || f.charAt(0).toUpperCase() + f.slice(1).replace(/_/g, " ");
  }
  return ANNUAL_COST_COLUMN_LABELS[col] || col;
}

// Lightness steps of one hue for a split group's members, so the hue never moves.
function rampShades(hex, n) {
  if (n <= 1) return [hex];
  const rgb = hex.match(/\w\w/g).map(h => parseInt(h, 16) / 255);
  const lin = c => c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  const oklabL = ([r, g, b]) => {
    const [lr, lg, lb] = [r, g, b].map(lin);
    const l = Math.cbrt(0.4122214708 * lr + 0.5363325363 * lg + 0.0514459929 * lb);
    const m = Math.cbrt(0.2119034982 * lr + 0.6806995451 * lg + 0.1073969566 * lb);
    const s = Math.cbrt(0.0883024619 * lr + 0.2817188376 * lg + 0.6299787005 * lb);
    return 0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s;
  };
  const luminance = ([r, g, b]) => 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
  const contrast = c => 1.05 / (luminance(c) + 0.05);
  // k in [-1, 1]: -1 = black, 0 = the base colour, 1 = white; lightness rises with k.
  const mix = k => rgb.map(c => k >= 0 ? c + (1 - c) * k : c * (1 + k));
  const toHex = c => "#" + c.map(v => Math.round(Math.max(0, Math.min(1, v)) * 255).toString(16).padStart(2, "0")).join("");
  let lo = 0, hi = 1;
  for (let i = 0; i < 30; i++) { const k = (lo + hi) / 2; if (contrast(mix(k)) >= 2.1) lo = k; else hi = k; }
  const lLight = oklabL(mix(lo));
  let step = 0.075;
  const lMin = 0.22;
  if (lLight - (n - 1) * step < lMin) step = (lLight - lMin) / (n - 1);
  const atL = target => {
    let a = -1, b = 1;
    for (let i = 0; i < 40; i++) { const k = (a + b) / 2; if (oklabL(mix(k)) < target) a = k; else b = k; }
    return toHex(mix((a + b) / 2));
  };
  return Array.from({ length: n }, (_, i) => atL(lLight - i * step));
}

function createAnnualCostsPane(container, set, opts) {
  const variantKey = opts && opts.variantKey;
  const variantDim = set.dimensions.find(d => d.key === variantKey);

  container.replaceChildren();
  const header = document.createElement("header");
  const viewRow = document.createElement("div");
  viewRow.className = "menurow catrow";
  const compRow = document.createElement("div");
  compRow.className = "menurow";
  const redRow = document.createElement("div");
  redRow.className = "menurow";
  const techRow = document.createElement("div");
  techRow.className = "menurow";
  const geoRow = document.createElement("div");
  geoRow.className = "menurow";
  header.append(viewRow, compRow, redRow, techRow, geoRow);
  const body = document.createElement("div");
  body.className = "compare-body";
  const title = document.createElement("div");
  title.className = "compare-title";
  const subtitle = document.createElement("div");
  subtitle.className = "compare-sub";
  const key = document.createElement("div");
  key.className = "cost-key";
  key.hidden = true;
  const message = document.createElement("div");
  message.className = "compare-message";
  const chart = document.createElement("div");
  chart.className = "compare-chart";
  const note = document.createElement("div");
  note.className = "compare-note";
  const tableBox = document.createElement("details");
  tableBox.className = "compare-table";
  const tableSummary = document.createElement("summary");
  tableSummary.textContent = "Data table";
  const exportBtn = document.createElement("button");
  exportBtn.textContent = "Export CSV";
  tableSummary.appendChild(exportBtn);
  tableBox.appendChild(tableSummary);
  body.append(title, subtitle, key, message, chart, note, tableBox);
  const controls = attachBarControls(container, header, "annualCostsBarCollapsed");
  container.append(header, controls.hresizer, controls.collapseRow, body);

  const state = {
    scenarios: [],
    view: "stack",             // "stack" (breakdown over time) | "year" (one-year breakdown)
    overTime: "combined",      // stack view: "combined" (one stack of everything selected) | "panels"
    product: "iron",
    year: 2040,
    groupsOff: new Set(),
    split: new Set(),          // group ids drawn as their members
    techsOff: new Set(),       // deselected technologies (new ones arrive selected)
    reductantsOff: new Set(),  // deselected reductant routes (iron only)
  };
  const cache = new Map();     // run.dir -> Promise<{rows} | {error}>
  let geoFilter = null, geoKeysSig = "";
  let lastTable = null;
  let plotted = false;
  let token = 0;
  let wrappedAt = 0, rewrap = null;   // chart width the panel headings were wrapped to (0 = none wrapped)

  const geoConfig = fetch("geo.json")
    .then(res => { if (!res.ok) throw new Error(res.status); return res.json(); })
    .catch(() => ({}));

  new ResizeObserver(() => {
    if (!plotted || !window.Plotly || !chart.clientWidth) return;
    window.Plotly.Plots.resize(chart);
    if (wrappedAt && Math.abs(chart.clientWidth - wrappedAt) > 20) {
      clearTimeout(rewrap);
      rewrap = setTimeout(redraw, 200);
    }
  }).observe(chart);

  const defaultRun = sc =>
    (variantDim && sc.runs.find(r => r.dims[variantKey] === variantDim.values[0])) || sc.runs[0];

  function dataFor(run) {
    if (!cache.has(run.dir)) {
      const url = set.base.replace(/\/$/, "") + "/" + run.dir + "/viewer/" + ANNUAL_COSTS_FILE;
      cache.set(run.dir, fetch(url)
        .then(res => { if (!res.ok) throw new Error(res.status + " " + res.statusText); return res.text(); })
        .then(t => ({ rows: parseTimeseries(t) }))
        .catch(e => ({ error: e.message })));
    }
    return cache.get(run.dir);
  }

  /* ---- selection semantics ---- */
  const comboKey = (tech, reductant) => tech + (reductant ? " · " + routeLabel(reductant) : "");
  const routeRank = r => { const i = ROUTE_ORDER.indexOf(r); return i < 0 ? ROUTE_ORDER.length : i; };
  const comboSort = (a, b) => a.tech.localeCompare(b.tech) || routeRank(a.reductant) - routeRank(b.reductant)
    || a.reductant.localeCompare(b.reductant);
  const activeGroups = () => ANNUAL_COST_GROUPS.filter(g => !state.groupsOff.has(g.id));

  // Member columns of a group present in the loaded data.
  function membersOf(g, columns) {
    if (g.prefix) {
      const rank = c => { const i = ANNUAL_COST_MATERIAL_ORDER.indexOf(c.slice(g.prefix.length)); return i < 0 ? 99 : i; };
      return columns.filter(c => c.startsWith(g.prefix)).sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
    }
    return g.cols.filter(c => columns.includes(c));
  }
  // A group as one segment, or when split one segment per member.
  function segmentsFor(groups, columns) {
    const out = [];
    for (const g of groups) {
      const members = membersOf(g, columns);
      if (state.split.has(g.id) && members.length > 1) {
        const shades = rampShades(g.color, members.length);
        members.forEach((c, i) => out.push({ id: c, label: annualCostMemberLabel(c), group: g, color: shades[i], cols: [c] }));
      } else out.push({ id: g.id, label: g.label, group: g, color: g.color, cols: members });
    }
    return out;
  }
  const segName = s => s.id !== s.group.id ? `${s.label} (${s.group.label})` : s.label;
  const segColumn = s => segName(s) + " (USD/t)";   // table headers carry the unit, so exports do too

  // One run's rows by combo key and year; with combine, every selected combination is "all".
  function aggregate(rows, sel, comboKeys, combine) {
    const out = new Map();
    for (const r of rows) {
      if (r.product !== state.product || !sel.has(r.geo_key)) continue;
      let key = comboKey(r.technology, r.reductant);
      if (!comboKeys.has(key)) continue;
      if (combine) key = "all";
      if (!out.has(key)) out.set(key, new Map());
      const byYear = out.get(key);
      let a = byYear.get(r.year);
      if (!a) { a = { prod: 0, cap: 0, nfg: 0, ninf: 0, cols: {} }; byYear.set(r.year, a); }
      a.prod += r.production_kt; a.cap += r.capacity_kt; a.nfg += r.n_fg; a.ninf += r.n_fg_inferred;
      for (const k in r) if (!ANNUAL_COST_TEXT_COLUMNS.has(k) && k !== "year") a.cols[k] = (a.cols[k] || 0) + r[k];
    }
    return out;
  }
  const perT = (a, cols) => a.prod ? 1e3 * cols.reduce((s, c) => s + (a.cols[c] || 0), 0) / a.prod : null;
  const segValue = (a, s) => perT(a, s.cols);
  const totalValue = (a, segments) => segments.reduce((s, seg) => s + (segValue(a, seg) || 0), 0);

  /* ---- controls ---- */
  const pill = (row, label, active, onclick, disabled) => {
    const b = document.createElement("button");
    b.textContent = label;
    b.classList.toggle("active", !!active);
    b.disabled = !!disabled;
    b.onclick = onclick;
    row.appendChild(b);
    return b;
  };
  const rowlabel = (row, text) => {
    const s = document.createElement("span");
    s.className = "rowlabel";
    s.textContent = text;
    row.appendChild(s);
  };
  const small = (row, text, onclick, cls) => {
    const b = document.createElement("button");
    b.className = cls || "small";
    b.textContent = text;
    b.onclick = onclick;
    row.appendChild(b);
    return b;
  };

  function renderViewRow() {
    viewRow.replaceChildren();
    controls.setSummary(state.view === "stack" ? "Breakdown over time" : "Year breakdown");
    rowlabel(viewRow, "Product");
    for (const p of ["iron", "steel"])
      pill(viewRow, p[0].toUpperCase() + p.slice(1), state.product === p, () => { state.product = p; redraw(); });
    rowlabel(viewRow, "View");
    for (const [id, label] of [["stack", "Breakdown over time"], ["year", "Year breakdown"]])
      pill(viewRow, label, state.view === id, () => { state.view = id; redraw(); });
    if (state.view === "stack") {
      rowlabel(viewRow, "Combinations");
      const combos = [["combined", "Combined", "one stack: the production-weighted average of every selected technology × reductant combination"],
                      ["panels", "One panel each", "a stacked panel per combination, a column per scenario"]];
      for (const [id, label, tip] of combos)
        pill(viewRow, label, state.overTime === id, () => { state.overTime = id; redraw(); }).title = tip;
    }
    if (state.view === "year") {
      rowlabel(viewRow, "Year");
      const range = document.createElement("input");
      range.type = "range"; range.min = 2025; range.max = 2060; range.step = 1; range.value = state.year;
      const val = document.createElement("span");
      val.className = "yearval"; val.textContent = state.year;
      range.oninput = () => { state.year = +range.value; val.textContent = state.year; };
      range.onchange = () => redraw();
      viewRow.append(range, val);
    }
  }

  // A reductant no selected technology runs on is greyed, keeping its on/off state.
  function renderTechRows(techs, reductants, combos) {
    techRow.replaceChildren();
    techRow.hidden = !techs.length;
    rowlabel(techRow, "Technology");
    small(techRow, "All", () => { techs.forEach(t => state.techsOff.delete(t)); redraw(); });
    small(techRow, "None", () => { techs.forEach(t => state.techsOff.add(t)); redraw(); });
    for (const t of techs) {
      const routes = combos.filter(c => c.tech === t && c.reductant).map(c => routeLabel(c.reductant));
      pill(techRow, t, !state.techsOff.has(t), () => {
        state.techsOff.has(t) ? state.techsOff.delete(t) : state.techsOff.add(t);
        redraw();
      }).title = routes.length ? "runs on " + routes.join(", ") : "";
    }
    redRow.replaceChildren();
    redRow.hidden = !reductants.length;
    if (!reductants.length) return;
    rowlabel(redRow, "Reductant");
    small(redRow, "All", () => { reductants.forEach(r => state.reductantsOff.delete(r)); redraw(); });
    small(redRow, "None", () => { reductants.forEach(r => state.reductantsOff.add(r)); redraw(); });
    for (const r of reductants) {
      const users = combos.filter(c => c.reductant === r && !state.techsOff.has(c.tech)).map(c => c.tech);
      pill(redRow, routeLabel(r), !state.reductantsOff.has(r), () => {
        state.reductantsOff.has(r) ? state.reductantsOff.delete(r) : state.reductantsOff.add(r);
        redraw();
      }, !users.length).title = users.length ? "used by " + users.join(", ")
        : "no selected technology runs on " + routeLabel(r);
    }
  }

  function renderCompRow(columns) {
    compRow.replaceChildren();
    compRow.hidden = !columns;
    if (!columns) return;
    rowlabel(compRow, "Cost components");
    small(compRow, "All", () => { state.groupsOff.clear(); redraw(); });
    small(compRow, "None", () => { ANNUAL_COST_GROUPS.forEach(g => state.groupsOff.add(g.id)); redraw(); });
    for (const g of ANNUAL_COST_GROUPS) {
      const b = pill(compRow, "", !state.groupsOff.has(g.id), () => {
        state.groupsOff.has(g.id) ? state.groupsOff.delete(g.id) : state.groupsOff.add(g.id);
        redraw();
      });
      b.title = g.note;
      const sw = document.createElement("span");
      sw.className = "swatch";
      sw.style.background = g.color;
      b.append(sw, g.label);
      const members = membersOf(g, columns);
      if (members.length > 1) {
        const on = state.split.has(g.id);
        small(compRow, on ? "▾ merge" : "▸ split",
          () => { on ? state.split.delete(g.id) : state.split.add(g.id); redraw(); }, "split")
          .title = on ? "Show " + g.label.toLowerCase() + " as one component"
                      : "Split into " + members.map(annualCostMemberLabel).join(", ");
      }
    }
  }

  /* ---- output ---- */
  function showMessage(text) {
    message.className = "compare-message";
    message.textContent = text;
    message.hidden = !text;
    const on = !text;
    title.hidden = subtitle.hidden = chart.hidden = !on;
    if (!on) { note.textContent = ""; renderTable([], []); key.hidden = true; wrappedAt = 0; }
  }
  const fmt0 = v => v == null ? "" : v.toLocaleString("en-GB", { maximumFractionDigits: 0 });
  const fmtCell = v => v == null ? "" : typeof v === "number"
    ? (Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 10 ? v.toFixed(1) : v.toFixed(2)) : v;
  const yTitle = () => "USD per tonne of " + (state.product === "iron" ? "iron" : "crude steel");
  // Ink for a value label inside a fill: dark on light shades, white on dark ones.
  const textOn = hex => {
    const lin = c => c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    const [r, g, b] = hex.match(/\w\w/g).map(h => lin(parseInt(h, 16) / 255));
    return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.4 ? "#1f2733" : "#ffffff";
  };

  function renderTable(header, rows) {
    tableBox.hidden = !header.length;
    const old = tableBox.querySelector("table");
    if (old) old.remove();
    lastTable = header.length ? [header, ...rows] : null;
    if (!header.length) return;
    const table = document.createElement("table");
    const head = table.insertRow();
    for (const h of header) {
      const th = document.createElement("th");
      th.textContent = h;
      head.appendChild(th);
    }
    for (const r of rows) {
      const tr = table.insertRow();
      r.forEach((v, i) => {
        const td = tr.insertCell();
        td.textContent = i ? fmtCell(v) : v;
        if (header[i] === "#") td.className = "scn";
      });
    }
    tableBox.appendChild(table);
  }
  exportBtn.onclick = e => {
    e.preventDefault();
    if (!lastTable) return;
    const csv = lastTable.map(r => r.map(v => typeof v === "number" ? v.toFixed(2)
      : `"${String(v == null ? "" : v).replace(/"/g, '""')}"`).join(",")).join("\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    const d = new Date(), two = n => String(n).padStart(2, "0");
    const stamp = `${d.getFullYear()}${two(d.getMonth() + 1)}${two(d.getDate())}-${two(d.getHours())}${two(d.getMinutes())}${two(d.getSeconds())}`;
    const view = state.view === "year" ? "year" + state.year : "over_time";
    a.download = `annual_costs_${state.product}_${view}_${stamp}.csv`;
    a.click();
  };

  function baseLayout() {
    return {
      margin: { l: 60, r: 20, t: 10, b: 40 },
      hovermode: "x unified",
      hoverlabel: { font: { family: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace", size: 11.5 } },
      paper_bgcolor: ANNUAL_COST_SURFACE, plot_bgcolor: ANNUAL_COST_SURFACE,
      font: { family: '-apple-system, "Segoe UI", sans-serif', size: 12.5, color: "#1f2733" },
      xaxis: { gridcolor: "#eceef2", zeroline: false },
      yaxis: { title: { text: yTitle(), font: { size: 12 } }, gridcolor: "#eceef2",
               zeroline: true, zerolinecolor: "#c9ced6", tickformat: ",.0f" },
      legend: { orientation: "h", y: 1, yanchor: "bottom", traceorder: "normal" },
    };
  }
  const PLOT_CONFIG = { responsive: true, displaylogo: false,
                        modeBarButtonsToRemove: ["select2d", "lasso2d", "autoScale2d"] };

  async function redraw() {
    const t = ++token;
    renderViewRow();
    const scs = state.scenarios;
    if (!scs.length) {
      techRow.hidden = redRow.hidden = geoRow.hidden = compRow.hidden = true;
      showMessage("Pick scenarios in the sidebar to see their annual costs.");
      if (opts.renderEmpty) {
        message.className = "scenario-overview";
        opts.renderEmpty(message);
      }
      return;
    }
    const runs = scs.map(defaultRun);
    const [geo, ...loaded] = await Promise.all([geoConfig, ...runs.map(dataFor)]);
    if (t !== token) return;
    const okIdx = loaded.map((l, i) => l.rows ? i : -1).filter(i => i >= 0);
    if (!okIdx.length) {
      techRow.hidden = redRow.hidden = geoRow.hidden = compRow.hidden = true;
      showMessage("No annual-cost data found for the picked scenarios (missing " + ANNUAL_COSTS_FILE
        + " — run steelo-viewer regenerate on their runs).");
      return;
    }
    const columns = [...new Set(okIdx.flatMap(i => Object.keys(loaded[i].rows[0] || {})))];
    renderCompRow(columns);

    // Filter vocabulary from the loaded runs: the combinations that actually occur.
    const keys = new Set(), comboMap = new Map();
    for (const i of okIdx) for (const r of loaded[i].rows) {
      if (r.product !== state.product) continue;
      keys.add(r.geo_key);
      const key = comboKey(r.technology, r.reductant);
      if (!comboMap.has(key)) comboMap.set(key, { key, tech: r.technology, reductant: r.reductant });
    }
    const combos = [...comboMap.values()].sort(comboSort);
    const techs = [...new Set(combos.map(c => c.tech))].sort();
    const reductants = [...new Set(combos.map(c => c.reductant).filter(Boolean))]
      .sort((a, b) => routeRank(a) - routeRank(b) || a.localeCompare(b));
    renderTechRows(techs, reductants, combos);
    geoRow.hidden = !keys.size;
    if (keys.size) {
      if (!geoFilter) geoFilter = createGeoFilter(geoRow, { geo, onChange: redraw });
      const sig = [...keys].sort().join(",");
      if (sig !== geoKeysSig) { geoKeysSig = sig; geoFilter.setGeoKeys([...keys]); }
    }
    const sel = geoFilter ? geoFilter.selectedGeos() : new Set();
    const combosOn = combos.filter(c => !state.techsOff.has(c.tech) && (!c.reductant || !state.reductantsOff.has(c.reductant)));
    const groups = activeGroups();
    const productName = state.product === "iron" ? "iron" : "steel";
    const productTitle = productName[0].toUpperCase() + productName.slice(1);
    if (!combos.length) { showMessage("No " + productName + " production in the picked scenarios."); return; }
    if (!sel.size) { showMessage("No geographies selected."); return; }
    if (!combosOn.length) {
      showMessage(reductants.length
        ? "No technology × reductant combination selected — pick at least one technology and a reductant it runs on."
        : "No technology selected.");
      return;
    }
    if (!groups.length) { showMessage("Select at least one cost component."); return; }

    const comboKeys = new Set(combosOn.map(c => c.key));
    const combined = state.view === "stack" && state.overTime === "combined";
    const aggs = okIdx.map(i => aggregate(loaded[i].rows, sel, comboKeys, combined));
    const years = [...new Set(okIdx.flatMap(i => loaded[i].rows.map(r => r.year)))].sort((a, b) => a - b);
    let nfg = 0, ninf = 0;
    aggs.forEach(m => m.forEach(byYear => byYear.forEach(a => { nfg += a.nfg; ninf += a.ninf; })));
    const shown = okIdx.map(i => scs[i]);
    const { differing, common } = splitDims(set, variantKey, shown.length > 1 ? shown : scs);
    // Chart text leads each scenario name with its greyed #n tag.
    const scName = sc => seriesLabel(set, sc, differing);
    // Compact value-only form for axis ticks (the table carries the full labels).
    const scTick = sc => scenarioTagHtml(sc) + " " + (differing.length ? differing.map(d => sc.dims[d.key]).join(" · ") : scName(sc));
    const multi = shown.length > 1;
    const compNote = groups.length === ANNUAL_COST_GROUPS.length ? "all cost components"
      : groups.map(g => g.label.toLowerCase()).join(" + ");
    const comboNote = reductants.length
      ? combosOn.length + " of " + combos.length + " technology × reductant combinations"
      : combosOn.length + " of " + combos.length + " technologies";
    const subtitleBits = [
      compNote, comboNote, geoFilter.note(),
      "production-weighted average of operating furnace groups",
      "capex inferred from the debt schedule for " + (nfg ? (100 * ninf / nfg).toFixed(1) : "0") + " % of furnace-group-years",
      ...(multi ? common.map(d => dimPair(d, shown[0].dims[d.key])) : [scenarioLabel(set, shown[0])]),
      variantDim && scs.some(sc => sc.runs.length > 1)
        ? dimPrefix(variantDim.label) + " " + variantDim.values[0] + " runs" : null,
    ].filter(Boolean);
    const notes = [];
    const failed = loaded.map((l, i) => l.error ? runs[i].dir : null).filter(Boolean);
    if (failed.length) notes.push("No data for: " + failed.join(", "));
    const allSegments = segmentsFor(groups, columns);
    // Drop all-zero segments, then re-step each split group's ramp over the survivors.
    const liveSegments = cells => {
      const live = allSegments.filter(s => cells.some(a => Math.abs(segValue(a, s) || 0) > 1e-9));
      for (const g of groups) {
        const members = live.filter(s => s.group === g && s.id !== g.id);
        const shades = rampShades(g.color, members.length);
        members.forEach((s, i) => { s.color = shades[i]; });
      }
      return live;
    };
    const Plotly = await loadPlotly().catch(e => { showMessage(e.message); return null; });
    if (!Plotly || t !== token) return;

    if (state.view === "year") {
      const cells = [];  // one bar per combo x scenario with production in the year
      combosOn.forEach(combo => aggs.forEach((m, k) => {
        const a = m.get(combo.key) && m.get(combo.key).get(state.year);
        if (a && a.prod > 0) cells.push({ combo, sc: shown[k], a });
      }));
      if (!cells.length) { showMessage("No " + productName + " production in " + state.year + " for this selection."); return; }
      showMessage("");
      key.hidden = true;
      wrappedAt = 0;
      title.textContent = "Annual costs of " + productName + " in " + state.year + " (USD/t)";
      subtitle.textContent = subtitleBits.join(" · ");
      const segments = liveSegments(cells.map(c => c.a));
      // Past eight bars the labels no longer fit side by side, so the bars go horizontal.
      const horizontal = cells.length > 8;
      const cats = multi ? [cells.map(c => c.combo.key), cells.map(c => scTick(c.sc))] : cells.map(c => c.combo.key);
      const totals = cells.map(c => totalValue(c.a, segments));
      const tops = cells.map(c => segments.reduce((s, seg) => s + Math.max(0, segValue(c.a, seg) || 0), 0));
      const span = Math.max(...tops, 1);
      // Value labels only where the segment is big enough to hold them.
      const label = v => v != null && Math.abs(v) >= 0.06 * span ? fmt0(v) : "";
      const catKey = horizontal ? "y" : "x", valKey = horizontal ? "x" : "y";
      const traces = segments.map(s => {
        const values = cells.map(c => segValue(c.a, s));
        return {
          type: "bar", name: segName(s), orientation: horizontal ? "h" : "v",
          [catKey]: cats, [valKey]: values,
          marker: { color: s.color, line: { width: 2, color: ANNUAL_COST_SURFACE } },
          text: values.map(label), textposition: "inside", insidetextanchor: "middle", textangle: 0,
          constraintext: "none", textfont: { size: 11, color: textOn(s.color) },
          hovertemplate: "%{" + valKey + ":,.0f} USD/t<extra>" + segName(s) + "</extra>",
        };
      });
      traces.push({ type: "scatter", mode: "text", [catKey]: cats, [valKey]: tops, text: totals.map(fmt0),
        textposition: horizontal ? "middle right" : "top center",
        textfont: { size: 11, color: "#1f2733" }, hoverinfo: "skip", showlegend: false, cliponaxis: false });
      const layout = baseLayout();
      layout.hovermode = "closest";
      layout.barmode = "relative";
      layout.bargap = multi ? 0.15 : 0.35;
      const catAxis = { type: multi ? "multicategory" : "category", tickangle: 0, automargin: true,
                        gridcolor: "#eceef2", zeroline: false };
      const valAxis = { ...layout.yaxis, rangemode: "tozero" };
      if (horizontal) {
        layout.yaxis = { ...catAxis, autorange: "reversed" };
        layout.xaxis = valAxis;
        layout.height = 110 + cells.length * 28;
        layout.margin.r = 60;
      } else {
        layout.xaxis = catAxis;
        layout.yaxis = valAxis;
      }
      chart.classList.toggle("facets", horizontal);
      chart.style.height = "";
      Plotly.react(chart, traces, layout, PLOT_CONFIG);
      plotted = true;
      // Tables name scenarios in full, whatever the chart abbreviates them to.
      renderTable(["Year", "Product", "Technology", "#", "Scenario", ...segments.map(segColumn), "Total (USD/t)",
                   "Production (Mt)", "Utilisation (%)", "Capex (USD/t capacity)", "Subsidies embedded (USD/t)"],
        cells.map((c, i) => [state.year, productTitle, c.combo.key, scenarioTag(c.sc), scenarioLabel(set, c.sc),
          ...segments.map(s => segValue(c.a, s)), totals[i], c.a.prod / 1e3, 100 * c.a.prod / c.a.cap,
          1e3 * (c.a.cols.capex || 0) / c.a.cap, perT(c.a, ["subsidy"])]));
      notes.push("Negative segments are credits (slag, process gases, CCU products); the label is the net total. "
        + "Costs are net of subsidies (the embedded subsidy per tonne is in the data table). "
        + "Split members that are zero for every bar are not drawn.");
      note.textContent = notes.join(" ");
      return;
    }

    // Breakdown over time: one stack per scenario, or a panel per combination and scenario.
    const combinedLabel = combosOn.length === combos.length
      ? (reductants.length ? "all technologies and reductants" : "all technologies")
      : combosOn.map(c => c.key).join(" + ");
    const comboRows = combined ? [{ key: "all", label: combinedLabel }]
      : combosOn.filter(c => aggs.some(m => m.has(c.key)));
    if (!comboRows.some(c => aggs.some(m => m.has(c.key)))) {
      showMessage("No " + productName + " production for this selection.");
      return;
    }
    const slots = [];  // grid order; byYear null = empty slot
    if (multi) comboRows.forEach(combo => aggs.forEach((m, k) => slots.push({ combo, sc: shown[k], byYear: m.get(combo.key) || null })));
    else comboRows.forEach(combo => slots.push({ combo, sc: shown[0], byYear: aggs[0].get(combo.key) }));
    const cols = multi ? aggs.length : Math.min(3, slots.length);
    const nrows = Math.ceil(slots.length / cols);
    const single = slots.length === 1;
    showMessage("");
    title.textContent = "Annual costs of " + productName + (combined ? " from " + combinedLabel : "") + " over time (USD/t)";
    subtitle.textContent = subtitleBits.join(" · ");
    // Headings wrap to their panel's width: cell = step × (1 - xgap), step = width / (cols - xgap).
    const XGAP = 0.08;
    const margin = baseLayout().margin;
    const panelW = (chart.clientWidth - margin.l - margin.r) * (1 - XGAP) / (cols - XGAP);
    const nameLines = new Map(shown.map(sc => [sc, wrapHeading(scenarioTag(sc) + " " + scName(sc), panelW)]));
    const keyed = multi && Math.max(...[...nameLines.values()].map(l => l.length)) > ANNUAL_COST_HEADING_LINES;
    const scHeading = sc => keyed ? scenarioTag(sc)
      : nameLines.get(sc).join("<br>").replace(/^#\d+/, scenarioTagHtml(sc));
    const headLines = !multi ? 1 : (combined ? 0 : 1) + (keyed ? 1 : Math.max(...[...nameLines.values()].map(l => l.length)));
    key.hidden = !keyed;
    key.replaceChildren(...(keyed ? shown.map(sc => {
      const line = document.createElement("div");
      const tag = document.createElement("span");
      tag.className = "scn";
      tag.textContent = scenarioTag(sc);
      line.append(tag, " " + scName(sc));
      return line;
    }) : []));
    wrappedAt = multi && !single ? chart.clientWidth : 0;
    const segments = liveSegments(slots.flatMap(p => p.byYear ? [...p.byYear.values()] : []));
    const traces = [], annotations = [], tableRows = [];
    let legendDone = false;
    slots.forEach((p, i) => {
      const ax = i ? String(i + 1) : "";
      const heading = combined ? "<b>" + scHeading(p.sc) + "</b>"
        : "<b>" + p.combo.key + "</b>" + (multi ? "<br>" + scHeading(p.sc) : "");
      if (!single) annotations.push({ xref: "x" + ax + " domain", yref: "y" + ax + " domain", x: 0, y: 1,
        xanchor: "left", yanchor: "bottom", yshift: 4, showarrow: false, align: "left",
        font: { size: ANNUAL_COST_HEADING_SIZE, color: "#1f2733" }, text: heading });
      if (!p.byYear) {
        // An axis without a trace is not laid out, so the slot gets an empty one.
        traces.push({ type: "scatter", mode: "lines", x: [years[0]], y: [null], xaxis: "x" + ax, yaxis: "y" + ax,
          showlegend: false, hoverinfo: "skip" });
        annotations.push({ xref: "x" + ax + " domain", yref: "y" + ax + " domain", x: 0.5, y: 0.5, showarrow: false,
          font: { size: 12, color: "#66707d" }, text: "no production in this scenario" });
        return;
      }
      const firstSign = s => { for (const y of years) { const a = p.byYear.get(y); const v = a && segValue(a, s); if (v) return v > 0 ? "pos" : "neg"; } return "pos"; };
      const series = s => years.map(y => { const a = p.byYear.get(y); return a ? segValue(a, s) : null; });
      for (const s of segments)
        traces.push({ type: "scatter", mode: "lines", name: segName(s), legendgroup: s.id, showlegend: !legendDone,
          x: years, y: series(s), stackgroup: firstSign(s) + ax, xaxis: "x" + ax, yaxis: "y" + ax,
          line: { width: 1, color: ANNUAL_COST_SURFACE }, fillcolor: s.color, connectgaps: false,
          hovertemplate: "%{y:,.0f} USD/t<extra>" + segName(s) + "</extra>" });
      const total = years.map(y => { const a = p.byYear.get(y); return a ? totalValue(a, segments) : null; });
      traces.push({ type: "scatter", mode: "lines", name: "Total", legendgroup: "total", showlegend: !legendDone,
        x: years, y: total, xaxis: "x" + ax, yaxis: "y" + ax,
        line: { width: 2, color: "#1f2733" }, connectgaps: false, hovertemplate: "%{y:,.0f} USD/t<extra>Total</extra>" });
      legendDone = true;
      const label = [productTitle, combined ? combinedLabel : p.combo.key, scenarioTag(p.sc), scenarioLabel(set, p.sc)];
      const values = segments.map(series);
      years.forEach((y, yi) => tableRows.push([y, ...label, ...values.map(v => v[yi]), total[yi]]));
    });
    const layout = baseLayout();
    layout.annotations = annotations;
    if (!single) {
      // Extra heading lines widen the row gaps, so the plots keep their size.
      const extra = headLines - 1;
      layout.height = nrows * (ANNUAL_COST_PANEL_HEIGHT + ANNUAL_COST_HEADING_LINE * Math.max(0, extra - 1)) + 120;
      const plotH = layout.height - layout.margin.t - layout.margin.b;   // what the grid domain is a fraction of
      layout.grid = { rows: nrows, columns: cols, pattern: "independent", roworder: "top to bottom",
                      xgap: XGAP, ygap: 0.28 + 0.08 * extra,
                      domain: { x: [0, 1], y: [0, 1 - (28 + ANNUAL_COST_HEADING_LINE * extra) / plotH] } };
    }
    const yBase = { ...layout.yaxis, title: undefined, matches: "y" };
    const xBase = { ...layout.xaxis, dtick: 5, range: [years[0], years[years.length - 1]] };
    slots.forEach((p, i) => {
      const ax = i ? String(i + 1) : "";
      layout["yaxis" + ax] = { ...yBase, visible: !!p.byYear };
      layout["xaxis" + ax] = { ...xBase, visible: !!p.byYear };
      if (i % cols === 0) layout["yaxis" + ax].title = { text: yTitle(), font: { size: single ? 12 : 11 } };
    });
    chart.classList.toggle("facets", !single);
    Plotly.react(chart, traces, layout, PLOT_CONFIG);
    plotted = true;
    renderTable(["Year", "Product", "Technology", "#", "Scenario", ...segments.map(segColumn), "Total (USD/t)"], tableRows);
    notes.push(combined
      ? "One stack for the whole selection: the production-weighted average across the selected technology × reductant combinations, so the mix shifts as technologies enter and leave."
        + (multi ? " One panel per scenario; panels share the y-axis." : "")
      : "One panel per technology × reductant combination" + (multi ? ", one column per scenario" : "") + "; panels share the y-axis.");
    notes.push("A component that changes sign over time is stacked on the side of its first non-zero year.");
    note.textContent = notes.join(" ");
  }

  renderViewRow();
  techRow.hidden = redRow.hidden = geoRow.hidden = compRow.hidden = true;
  showMessage("Pick scenarios in the sidebar to see their annual costs.");

  return { show(scenarios) { state.scenarios = scenarios; redraw(); } };
}
