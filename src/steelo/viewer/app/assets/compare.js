"use strict";
/* Compare pane: one measure charted across the picked scenarios, each from its default-variant run. */

const COMPARE_CATEGORIES = [
  { id: "capprod", label: "Capacity & production", live: true },
  { id: "supply", label: "Supply & demand", live: true },
  { id: "charge", label: "Metallic charge use", live: true },
  { id: "reductant", label: "Reductant use", live: true },
  { id: "emissions", label: "Emissions", live: true },
  { id: "greenfield", label: "Greenfield", live: true },
  { id: "trade", label: "Trade", live: true },
];

// Trade products as in trade.csv; iron ore grades are pooled.
const TRADE_PRODUCTS = [
  { key: "steel", label: "Steel" },
  { key: "iron", label: "Iron", note: "DRI + HBI + hot metal + pig iron" },
  { key: "scrap", label: "Scrap" },
  { key: "ore", label: "Iron ore", note: "all grades (io_high + io_mid + io_low)" },
];

// Net subtracts the reverse direction, so flows with both ends inside From and To cancel exactly.
const TRADE_FLOWS = [
  { key: "all", label: "All flows", title: "trade + local deliveries" },
  { key: "cross", label: "Cross-border", title: "trade" },
  { key: "local", label: "Local (origin = destination)", title: "local deliveries" },
  { key: "net", label: "Net", title: "net flows" },
];

// Column prefixes in timeseries.csv per emissions boundary, labelled as on the per-run emissions page.
const COMPARE_BOUNDARIES = [
  { id: "rs", label: "rs-inspired" },
  { id: "wsn", label: "worldsteel no opt credits" },
  { id: "wso", label: "worldsteel opt credits" },
];

// Use and availability count at different geographies per commodity (`where` goes in the subtitle).
const SUPPLY_COMMODITIES = [
  { key: "steel", label: "Steel", useLabel: "Consumption", availLabel: "Demand",
    useTitle: "Steel consumption", availTitle: "Steel demand",
    utilLabel: "Demand served", utilTitle: "Steel demand served",
    where: "counted at consuming countries" },
  { key: "scrap", label: "Scrap", useTitle: "Scrap use", availTitle: "Scrap availability",
    utilTitle: "Scrap utilisation", where: "counted at source countries" },
  { key: "ore", label: "Iron ore", grades: true, useTitle: "Iron ore use",
    availTitle: "Iron ore availability", utilTitle: "Iron ore utilisation",
    where: "counted at source countries" },
  // Biomass budgets belong to whole regions: one counts in full once any member country is selected.
  { key: "bio", label: "Bio-PCI", useTitle: "Bio-PCI use", availTitle: "Biomass availability",
    utilTitle: "Biomass utilisation", regionAvail: true,
    where: "counted at consuming countries" },
  { key: "co2", label: "CO2 storage", useLabel: "Stored", availLabel: "Storage limit",
    useTitle: "CO2 stored", availTitle: "CO2 storage limit",
    utilTitle: "CO2 storage utilisation", where: "counted at consuming countries" },
];
const ORE_GRADES = ["io_high", "io_mid", "io_low"];

// Only the chosen reductant's carriers count, so auxiliary energy of the same process is excluded.
const REDUCTANT_CARRIERS = [
  { key: "coking_coal", label: "Coking coal", col: "coking_coal_mt", unit: "Mt" },
  { key: "pci", label: "PCI", col: "pci_mt", unit: "Mt" },
  { key: "bio_pci", label: "Bio-PCI", col: "bio_pci_mt", unit: "Mt" },
  { key: "hydrogen", label: "Hydrogen", col: "hydrogen_mt", unit: "Mt" },
  { key: "natural_gas", label: "Natural gas", col: "natural_gas_twh", unit: "TWh" },
  { key: "coal", label: "Coal", col: "coal_twh", unit: "TWh" },
  { key: "electricity", label: "Electricity", col: "electricity_twh", unit: "TWh" },
];

// Reductant routes in the per-run page's palette order; routes not listed sort after, alphabetically.
const ROUTE_ORDER = ["coke+pci", "coke+bio_pci", "coke+pci+h2", "coke+bio_pci+h2", "pci",
                     "coal", "bio_pci", "charcoal", "natural_gas", "hydrogen", "electricity", "none"];
const ROUTE_COMPONENT_LABELS = { pci: "PCI", bio_pci: "bio-PCI", h2: "H2",
                                 natural_gas: "natural gas", none: "no reductant" };
const routeLabel = route => route.split("+").map(c => ROUTE_COMPONENT_LABELS[c] || c).join("+");

// Metallic charge families in the per-run page's stacking order.
const CHARGE_FAMILIES = [
  { id: "scrap", label: "Scrap", members: ["scrap"] },
  { id: "hot_metal", label: "Hot metal", members: ["hot_metal"] },
  { id: "liquid_iron", label: "Liquid iron", members: ["liquid_iron"] },
  { id: "pig_iron", label: "Pig iron", members: ["pig_iron"] },
  { id: "dri", label: "DRI", members: ["dri_high", "dri_mid", "dri_low"] },
  { id: "hbi", label: "HBI", members: ["hbi_high", "hbi_mid", "hbi_low"] },
  { id: "electrolytic_iron", label: "Electrolytic iron", members: ["electrolytic_iron"] },
  { id: "io", label: "Iron ore", members: ["io_high", "io_mid", "io_low"] },
];

// A scenario keeps its palette slot while picked, so unpicking one never recolours the rest.
const COMPARE_PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100",
                         "#e87ba4", "#008300", "#4a3aa7", "#e34948",
                         "#4f9fd9", "#8f8a00", "#7e6bd9", "#a71d13",
                         "#00a3c4", "#5f7d1f", "#7c2bab", "#009475",
                         "#155bab", "#c05a1e", "#9a4292", "#86a80f"];

let plotlyLoader = null;
function loadPlotly() {
  if (!plotlyLoader) plotlyLoader = new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = "assets/vendor/plotly.min.js";
    s.onload = () => resolve(window.Plotly);
    s.onerror = () => reject(new Error("could not load assets/vendor/plotly.min.js"));
    document.head.appendChild(s);
  });
  return plotlyLoader;
}

const TEXT_COLUMNS = new Set(["geo_key", "product", "technology", "charge", "reductant",
                              "commodity", "grade", "origin", "destination"]);
function parseTimeseries(text) {
  const lines = text.trim().split("\n");
  const head = lines[0].split(",");
  const rows = [];
  for (let i = 1; i < lines.length; i++) {
    const c = lines[i].split(",");
    const row = {};
    for (let j = 0; j < head.length; j++) row[head[j]] = TEXT_COLUMNS.has(head[j]) ? c[j] : +c[j];
    rows.push(row);
  }
  return rows;
}

function createComparePane(container, set, opts) {
  const variantKey = opts && opts.variantKey;
  const variantDim = set.dimensions.find(d => d.key === variantKey);

  container.replaceChildren();
  const header = document.createElement("header");
  const catRow = document.createElement("div");
  catRow.className = "menurow catrow";
  const ctrlRow = document.createElement("div");
  ctrlRow.className = "menurow";
  const techRow = document.createElement("div");
  techRow.className = "menurow";
  const geoRow = document.createElement("div");
  geoRow.className = "menurow";
  // Budget-region selector, replacing the country filter for a regional commodity's availability.
  const bioRow = document.createElement("div");
  bioRow.className = "menurow";
  bioRow.hidden = true;
  // Origin and destination filters, replacing the country filter for trade.
  const fromRow = document.createElement("div");
  fromRow.className = "menurow";
  fromRow.hidden = true;
  const toRow = document.createElement("div");
  toRow.className = "menurow";
  toRow.hidden = true;
  header.append(catRow, ctrlRow, techRow, geoRow, fromRow, toRow, bioRow);
  const body = document.createElement("div");
  body.className = "compare-body";
  const title = document.createElement("div");
  title.className = "compare-title";
  const subtitle = document.createElement("div");
  subtitle.className = "compare-sub";
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
  tableBox.appendChild(tableSummary);
  body.append(title, subtitle, message, chart, note, tableBox);
  const controls = attachBarControls(container, header, "comparebarCollapsed");
  container.append(header, controls.hresizer, controls.collapseRow, body);
  // Viewport coordinates, so a header shrunk to a scrolling strip cannot clip the panels.
  const placePanel = (toggle, panel) => {
    if (panel.hidden) return;
    const r = toggle.getBoundingClientRect();
    panel.style.position = "fixed";
    panel.style.left = r.left + "px";
    panel.style.top = (r.bottom + 4) + "px";
  };

  const state = {
    scenarios: [],
    category: "capprod",
    metric: "production",       // capprod: "production" | "capacity"
    product: "steel",           // capprod: "steel" | "iron"
    charge: "scrap",            // charge: a CHARGE_FAMILIES id
    gradesOff: new Set(),       // charge: deselected grades of the picked family
    carrier: "coking_coal",     // reductant: a REDUCTANT_CARRIERS key (energy metric)
    rmetric: "routes",          // reductant: "routes" | "energy"
    routesOff: new Set(),       // reductant: deselected routes (new ones arrive selected)
    commodity: "steel",         // supply: a SUPPLY_COMMODITIES key
    smetric: "use",             // supply: "use" | "avail" | "util"
    gmetric: "plants",          // greenfield: "plants" | "capacity"
    gcumulative: false,         // greenfield: builds per year vs running total
    tproduct: "steel",          // trade: a TRADE_PRODUCTS key
    tflow: "all",               // trade: a TRADE_FLOWS key
    oreOff: new Set(),          // supply: deselected ore grades
    bioRegionsOff: new Set(),   // supply: deselected biomass budget regions
    boundary: "rs",
    direct: true, indirect: true, biogenic: false, cumulative: false,
    techsOff: new Set(),        // deselected technologies (new ones arrive selected)
  };
  const slots = new Map();      // scenario.key -> palette slot, stable while picked
  const cache = new Map();      // run.dir + file -> Promise<{rows} | {error}>
  let geoFilter = null;
  let geoKeysSig = "";
  let tradeFrom = null, tradeTo = null;
  let tradeKeysSig = "";
  let regionMembers = null;   // "region:X" -> member ISO3s, from geo.json
  let countryRegion = null;   // ISO3 -> its "region:X" budget key
  let plotted = false;
  let token = 0;

  const geoConfig = fetch("geo.json")
    .then(res => { if (!res.ok) throw new Error(res.status); return res.json(); })
    .catch(() => ({}));

  new ResizeObserver(() => {
    if (plotted && window.Plotly && chart.clientWidth) window.Plotly.Plots.resize(chart);
  }).observe(chart);

  const defaultRun = sc =>
    (variantDim && sc.runs.find(r => r.dims[variantKey] === variantDim.values[0])) || sc.runs[0];

  const dataFile = () => state.category === "charge" ? "charge.csv"
    : state.category === "reductant" ? "reductant.csv"
    : state.category === "supply" ? "supply.csv"
    : state.category === "greenfield" ? "greenfield.csv"
    : state.category === "trade" ? "trade.csv" : "timeseries.csv";

  function dataFor(run, file) {
    const key = run.dir + "/" + file;
    if (!cache.has(key)) {
      const url = set.base.replace(/\/$/, "") + "/" + run.dir + "/viewer/" + file;
      cache.set(key, fetch(url)
        .then(res => { if (!res.ok) throw new Error(res.status + " " + res.statusText); return res.text(); })
        .then(t => ({ rows: parseTimeseries(t) }))
        .catch(e => ({ error: e.message })));
    }
    return cache.get(key);
  }

  function assignSlots(scs) {
    for (const key of [...slots.keys()]) if (!scs.some(sc => sc.key === key)) slots.delete(key);
    for (const sc of scs) {
      if (slots.has(sc.key)) continue;
      const used = new Set(slots.values());
      let s = 0;
      while (used.has(s)) s++;
      if (s < COMPARE_PALETTE.length) slots.set(sc.key, s);
    }
  }

  /* ---- measure semantics ---- */
  // As on the per-run emissions page: "incl. biogenic" swaps the direct accounting rather than adding.
  function emissionColumns() {
    const cols = [];
    if (state.direct) cols.push(state.boundary + (state.biogenic ? "_direct_bio" : "_direct"));
    if (state.indirect) cols.push(state.boundary + "_indirect");
    return cols;
  }

  function scopeTitle() {
    const directLabel = "Direct" + (state.indirect ? "" : " emissions")
      + " (" + (state.biogenic ? "incl." : "excl.") + " biogenic)";
    return state.direct && state.indirect ? directLabel + " + Indirect emissions"
      : state.direct ? directLabel : "Indirect emissions";
  }

  const chargeFamily = () => CHARGE_FAMILIES.find(f => f.id === state.charge);
  const supplyCommodity = () => SUPPLY_COMMODITIES.find(c => c.key === state.commodity);
  const gradeOf = c => c.slice(c.lastIndexOf("_") + 1);
  const oreMembers = () => new Set(ORE_GRADES.filter(g => !state.oreOff.has(gradeOf(g))));
  // The charges the current family + grade selection sums over.
  function chargeMembers() {
    const fam = chargeFamily();
    return new Set(fam.members.filter(c =>
      fam.members.length === 1 || !state.gradesOff.has(gradeOf(c))));
  }

  function measure() {
    if (state.category === "capprod") {
      const col = state.metric === "production" ? "production_kt" : "capacity_kt";
      return {
        title: (state.product === "steel" ? "Steel " : "Iron ") + state.metric + " (Mt)",
        unit: "Mt", fmt: ".1f",
        value: r => r.product === state.product ? r[col] / 1e3 : 0,
      };
    }
    if (state.category === "supply") {
      const c = supplyCommodity();
      const members = oreMembers();
      const match = r => r.commodity === c.key && (!c.grades || members.has(r.grade));
      const on = ORE_GRADES.map(gradeOf).filter(g => !state.oreOff.has(g));
      const grade = c.grades && on.length < ORE_GRADES.length
        ? " (" + on.join(" + ") + " grade" + (on.length > 1 ? "s" : "") + ")" : "";
      // A regional commodity's availability splits by whole budget regions only.
      const off = state.bioRegionsOff;
      const geoTest = c.regionAvail && state.smetric !== "use"
        ? r => {
            if (r.geo_key.startsWith("region:")) return !off.has(r.geo_key);
            if (!countryRegion) return true;
            const rk = countryRegion.get(r.geo_key) || r.geo_key;
            return !off.has(rk);
          }
        : undefined;
      if (state.smetric === "util") return {
        title: c.utilTitle + grade + " (%)", unit: "%", fmt: ".1f", geoTest,
        value: r => match(r) ? r.use_mt : 0,
        value2: r => match(r) ? r.avail_mt : 0,
      };
      const avail = state.smetric === "avail";
      return {
        title: (avail ? c.availTitle : c.useTitle) + grade + " (Mt)",
        unit: "Mt", fmt: ".1f", geoTest,
        value: r => match(r) ? (avail ? r.avail_mt : r.use_mt) : 0,
      };
    }
    if (state.category === "reductant") {
      // Routes are mutually exclusive per furnace group, so any selection sums exactly.
      if (state.rmetric === "routes") return {
        title: "Iron production by reductant (Mt)",
        unit: "Mt", fmt: ".1f",
        value: r => state.routesOff.has(r.reductant) ? 0 : r.production_mt,
      };
      const c = REDUCTANT_CARRIERS.find(c => c.key === state.carrier);
      return {
        title: c.label + " use as reductant (" + c.unit + ")",
        unit: c.unit, fmt: c.unit === "Mt" ? ".2f" : ".1f",
        value: r => r[c.col],
      };
    }
    if (state.category === "greenfield") {
      // Builds count at completion, the first operating year.
      const plants = state.gmetric === "plants";
      const cum = state.gcumulative;
      const what = "new " + state.product + (plants ? " plants" : " capacity");
      return {
        title: cum ? "Cumulative " + what + (plants ? "" : " (Mt)")
          : what[0].toUpperCase() + what.slice(1) + (plants ? " (per year)" : " (Mt/yr)"),
        unit: plants ? "plants" : cum ? "Mt" : "Mt/yr",
        fmt: plants ? ".0f" : ".1f",
        cumulative: cum,
        value: r => r.product === state.product ? (plants ? r.n_plants : r.capacity_kt / 1e3) : 0,
      };
    }
    if (state.category === "trade") {
      const p = TRADE_PRODUCTS.find(p => p.key === state.tproduct);
      const flow = state.tflow;
      const from = tradeFrom && tradeFrom.selectedGeos();
      const to = tradeTo && tradeTo.selectedGeos();
      const inFwd = r => (!from || from.has(r.origin)) && (!to || to.has(r.destination));
      if (flow === "net") {
        // From -> To minus To -> From: a flow inside both selections cancels itself exactly.
        const inRev = r => (!from || from.has(r.destination)) && (!to || to.has(r.origin));
        return {
          title: p.label + " net flows (Mt)", unit: "Mt", fmt: ".1f",
          geoTest: r => inFwd(r) || inRev(r),
          value: r => r.product !== state.tproduct ? 0
            : (inFwd(r) ? r.volume_mt : 0) - (inRev(r) ? r.volume_mt : 0),
        };
      }
      return {
        title: p.label + " " + TRADE_FLOWS.find(f => f.key === flow).title + " (Mt)",
        unit: "Mt", fmt: ".1f",
        geoTest: r => inFwd(r)
          && (flow === "all" || (flow === "local") === (r.origin === r.destination)),
        value: r => r.product === state.tproduct ? r.volume_mt : 0,
      };
    }
    if (state.category === "charge") {
      const fam = chargeFamily();
      const members = chargeMembers();
      const grades = fam.members.map(gradeOf).filter(g => !state.gradesOff.has(g));
      const grade = fam.members.length > 1 && grades.length < fam.members.length
        ? " (" + grades.join(" + ") + " grade" + (grades.length > 1 ? "s" : "") + ")" : "";
      return {
        title: fam.label + grade + " use (Mt)",
        unit: "Mt", fmt: ".1f",
        value: r => members.has(r.charge) ? r.use_kt / 1e3 : 0,
      };
    }
    const cols = emissionColumns();
    const cumulative = state.cumulative;
    return {
      title: (cumulative ? "Cumulative emissions" : "Emissions")
        + " (" + (cumulative ? "Gt" : "Mt") + " CO2e" + (cumulative ? "" : "/yr") + ")",
      unit: cumulative ? "Gt CO2e" : "Mt CO2e/yr", fmt: cumulative ? ".2f" : ".1f",
      cumulative, cumScale: 1e3,
      value: r => cols.reduce((v, c) => v + r[c], 0) / 1e3,
    };
  }

  function seriesFor(rows, years, m) {
    // A geoTest measure ignores the shared filter, which trade rows never build.
    const sel = m.geoTest ? null : geoFilter.selectedGeos();
    const byYear = new Map();
    const denom = m.value2 ? new Map() : null;
    for (const r of rows) {
      if (state.techsOff.has(r.technology)) continue;
      if (m.geoTest ? !m.geoTest(r, sel) : !sel.has(r.geo_key)) continue;
      byYear.set(r.year, (byYear.get(r.year) || 0) + m.value(r));
      if (denom) denom.set(r.year, (denom.get(r.year) || 0) + m.value2(r));
    }
    let sum = 0;
    return years.map(y => {
      const v = byYear.get(y) || 0;
      if (denom) {
        const d = denom.get(y) || 0;
        return d ? 100 * v / d : 0;
      }
      return m.cumulative ? (sum += v) / (m.cumScale || 1) : v;
    });
  }

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
  const toggle = (row, label, prop, disabled) =>
    pill(row, label, state[prop], () => { state[prop] = !state[prop]; redraw(); }, disabled);

  // Grade toggle pills (All/None + one per member's suffix) driving offSet.
  function gradePills(members, offSet) {
    rowlabel(ctrlRow, "Grades");
    const all = document.createElement("button");
    all.className = "small";
    all.textContent = "All";
    all.onclick = () => { offSet.clear(); redraw(); };
    const none = document.createElement("button");
    none.className = "small";
    none.textContent = "None";
    none.onclick = () => { members.forEach(m => offSet.add(gradeOf(m))); redraw(); };
    ctrlRow.append(all, none);
    for (const m of members) {
      const g = gradeOf(m);
      pill(ctrlRow, g, !offSet.has(g),
        () => { offSet.has(g) ? offSet.delete(g) : offSet.add(g); redraw(); });
    }
  }

  function renderCatRow() {
    catRow.replaceChildren();
    controls.setSummary(COMPARE_CATEGORIES.find(c => c.id === state.category).label);
    for (const c of COMPARE_CATEGORIES) {
      const b = pill(catRow, c.label, c.id === state.category,
        () => { state.category = c.id; redraw(); }, !c.live);
      if (!c.live) b.title = "Coming soon";
    }
  }

  function renderCtrlRow() {
    ctrlRow.replaceChildren();
    ctrlRow.hidden = false;
    if (state.category === "capprod") {
      rowlabel(ctrlRow, "Metric");
      for (const m of ["production", "capacity"])
        pill(ctrlRow, m[0].toUpperCase() + m.slice(1), state.metric === m,
          () => { state.metric = m; redraw(); });
      rowlabel(ctrlRow, "Product");
      for (const p of ["steel", "iron"])
        pill(ctrlRow, p[0].toUpperCase() + p.slice(1), state.product === p,
          () => { state.product = p; redraw(); });
    } else if (state.category === "supply") {
      rowlabel(ctrlRow, "Commodity");
      for (const c of SUPPLY_COMMODITIES)
        pill(ctrlRow, c.label, state.commodity === c.key,
          () => { state.commodity = c.key; redraw(); });
      const c = supplyCommodity();
      rowlabel(ctrlRow, "Metric");
      for (const [m, label] of [["use", c.useLabel || "Use"],
                                ["avail", c.availLabel || "Availability"],
                                ["util", c.utilLabel || "Utilisation"]])
        pill(ctrlRow, label, state.smetric === m, () => { state.smetric = m; redraw(); });
      if (c.grades) gradePills(ORE_GRADES, state.oreOff);
    } else if (state.category === "reductant") {
      rowlabel(ctrlRow, "Metric");
      for (const [m, label] of [["routes", "Reductant use"], ["energy", "Energy use"]])
        pill(ctrlRow, label, state.rmetric === m,
          () => { state.rmetric = m; redraw(); });
      if (state.rmetric === "energy") {
        rowlabel(ctrlRow, "Energy carrier");
        for (const c of REDUCTANT_CARRIERS)
          pill(ctrlRow, c.label, state.carrier === c.key,
            () => { state.carrier = c.key; redraw(); });
      }
      // Route pills are appended by renderRoutePills once the data is loaded.
    } else if (state.category === "greenfield") {
      rowlabel(ctrlRow, "Metric");
      for (const [m, label] of [["plants", "New plants"], ["capacity", "New capacity"]])
        pill(ctrlRow, label, state.gmetric === m, () => { state.gmetric = m; redraw(); });
      rowlabel(ctrlRow, "Product");
      for (const p of ["steel", "iron"])
        pill(ctrlRow, p[0].toUpperCase() + p.slice(1), state.product === p,
          () => { state.product = p; redraw(); });
      toggle(ctrlRow, "Cumulative", "gcumulative").classList.add("gap");
    } else if (state.category === "trade") {
      rowlabel(ctrlRow, "Product");
      for (const p of TRADE_PRODUCTS)
        pill(ctrlRow, p.label, state.tproduct === p.key,
          () => { state.tproduct = p.key; redraw(); });
      rowlabel(ctrlRow, "Flows");
      for (const f of TRADE_FLOWS)
        pill(ctrlRow, f.label, state.tflow === f.key, () => { state.tflow = f.key; redraw(); });
      const swap = pill(ctrlRow, "From ⇄ To", false, () => {
        if (!tradeFrom) return;
        const from = tradeFrom.selectedGeos();
        tradeFrom.setSelected(tradeTo.selectedGeos());
        tradeTo.setSelected(from);
        redraw();
      });
      swap.className = "small gap";
      swap.title = "Swap the From and To selections";
    } else if (state.category === "charge") {
      rowlabel(ctrlRow, "Charge");
      for (const f of CHARGE_FAMILIES)
        pill(ctrlRow, f.label, state.charge === f.id,
          () => { state.charge = f.id; state.gradesOff.clear(); redraw(); });
      const fam = chargeFamily();
      if (fam.members.length > 1) gradePills(fam.members, state.gradesOff);
    } else {  // emissions
      rowlabel(ctrlRow, "Emissions boundary");
      const select = document.createElement("select");
      for (const b of COMPARE_BOUNDARIES) {
        const o = document.createElement("option");
        o.value = b.id;
        o.textContent = b.label;
        select.appendChild(o);
      }
      select.value = state.boundary;
      select.onchange = () => { state.boundary = select.value; redraw(); };
      ctrlRow.appendChild(select);
      rowlabel(ctrlRow, "Scope");
      toggle(ctrlRow, "Direct", "direct");
      toggle(ctrlRow, "Indirect", "indirect");
      toggle(ctrlRow, "incl. biogenic", "biogenic", !state.direct);
      toggle(ctrlRow, "Cumulative", "cumulative").classList.add("gap");
    }
  }

  // Returns the ordered routes, or null when the metric is not showing.
  function renderRoutePills(okRows) {
    if (state.category !== "reductant" || state.rmetric !== "routes") return null;
    const order = r => { const i = ROUTE_ORDER.indexOf(r); return i < 0 ? ROUTE_ORDER.length : i; };
    const routes = [...new Set(okRows.map(r => r.reductant))]
      .sort((a, b) => order(a) - order(b) || a.localeCompare(b));
    rowlabel(ctrlRow, "Reductant");
    const all = document.createElement("button");
    all.className = "small";
    all.textContent = "All";
    all.onclick = () => { state.routesOff.clear(); redraw(); };
    const none = document.createElement("button");
    none.className = "small";
    none.textContent = "None";
    none.onclick = () => { routes.forEach(rt => state.routesOff.add(rt)); redraw(); };
    ctrlRow.append(all, none);
    const flip = rt => {
      state.routesOff.has(rt) ? state.routesOff.delete(rt) : state.routesOff.add(rt);
      redraw();
    };
    for (const rt of routes) pill(ctrlRow, routeLabel(rt), !state.routesOff.has(rt), () => flip(rt));
    return routes;
  }

  // Built once per key set, so the panel stays open while toggling.
  const bioRegionLabel = k => k.startsWith("region:") ? k.slice(7) : k;
  const bioUI = { keys: [], items: new Map(), toggle: null, panel: null, chips: null, sig: "" };

  function buildBioRegionRow(keys) {
    bioRow.replaceChildren();
    bioUI.keys = keys;
    bioUI.items.clear();
    rowlabel(bioRow, "Geography");
    const all = document.createElement("button");
    all.className = "small";
    all.textContent = "All";
    all.onclick = () => { state.bioRegionsOff.clear(); redraw(); };
    const none = document.createElement("button");
    none.className = "small";
    none.textContent = "None";
    none.onclick = () => { keys.forEach(k => state.bioRegionsOff.add(k)); redraw(); };
    bioRow.append(all, none);
    const dd = document.createElement("span");
    dd.className = "dd";
    bioUI.toggle = document.createElement("button");
    bioUI.toggle.className = "dd-toggle";
    bioUI.panel = document.createElement("div");
    bioUI.panel.className = "dd-panel";
    bioUI.panel.hidden = true;
    bioUI.toggle.onclick = () => { bioUI.panel.hidden = !bioUI.panel.hidden; placePanel(bioUI.toggle, bioUI.panel); };
    dd.append(bioUI.toggle, bioUI.panel);
    const flip = k => {
      state.bioRegionsOff.has(k) ? state.bioRegionsOff.delete(k) : state.bioRegionsOff.add(k);
      redraw();
    };
    for (const k of keys) {
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.onchange = () => flip(k);
      const label = document.createElement("label");
      label.append(cb, bioRegionLabel(k));
      bioUI.panel.appendChild(label);
      bioUI.items.set(k, cb);
    }
    bioUI.chips = document.createElement("span");
    bioUI.chips.className = "geo-chips";
    bioRow.append(dd, bioUI.chips);
  }

  function updateBioRegionRow() {
    const on = bioUI.keys.filter(k => !state.bioRegionsOff.has(k));
    for (const [k, cb] of bioUI.items) cb.checked = !state.bioRegionsOff.has(k);
    bioUI.toggle.textContent = on.length + " of " + bioUI.keys.length + " budget regions ▾";
    bioUI.chips.replaceChildren();
    if (!on.length || on.length === bioUI.keys.length) {
      const m = document.createElement("span");
      m.className = "muted";
      m.textContent = on.length ? "all budget regions selected" : "no budget regions selected";
      bioUI.chips.appendChild(m);
      return;
    }
    for (const k of on) {
      const x = document.createElement("button");
      x.textContent = "×";
      x.title = "Remove";
      x.onclick = () => { state.bioRegionsOff.add(k); redraw(); };
      const chip = document.createElement("span");
      chip.className = "geo-chip";
      chip.append(bioRegionLabel(k), x);
      bioUI.chips.appendChild(chip);
    }
  }

  function renderBioRegionRow(okRows) {
    const c = state.category === "supply" && supplyCommodity();
    if (!c || !c.regionAvail || state.smetric === "use") {
      bioRow.hidden = true;
      return null;
    }
    const keys = [...new Set(okRows.filter(r => r.commodity === c.key && r.avail_mt > 0)
      .map(r => r.geo_key))].sort((a, b) => bioRegionLabel(a).localeCompare(bioRegionLabel(b)));
    bioRow.hidden = !keys.length;
    if (!keys.length) return null;
    const sig = keys.join(",");
    if (sig !== bioUI.sig) { bioUI.sig = sig; buildBioRegionRow(keys); }
    updateBioRegionRow();
    return keys;
  }

  function bioRegionNote(keys) {
    const on = keys.filter(k => !state.bioRegionsOff.has(k));
    if (on.length === keys.length) return "all biomass budget regions";
    if (on.length && on.length <= 2) return on.map(bioRegionLabel).join(", ") + " only";
    return on.length + " of " + keys.length + " biomass budget regions";
  }

  // Built once per key set, so the selections survive product and category switches.
  function renderTradeRows(okRows, geo) {
    if (state.category !== "trade") {
      fromRow.hidden = toRow.hidden = true;
      return;
    }
    const fromKeys = [...new Set(okRows.map(r => r.origin))].sort();
    const toKeys = [...new Set(okRows.map(r => r.destination))].sort();
    fromRow.hidden = !fromKeys.length;
    toRow.hidden = !toKeys.length;
    if (!toKeys.length) return;
    if (!tradeFrom) {
      tradeFrom = createGeoFilter(fromRow, { geo, label: "From", onChange: redraw });
      tradeTo = createGeoFilter(toRow, { geo, label: "To", onChange: redraw });
    }
    const sig = fromKeys.join(",") + "|" + toKeys.join(",");
    if (sig !== tradeKeysSig) {
      tradeKeysSig = sig;
      tradeFrom.setGeoKeys(fromKeys);
      tradeTo.setGeoKeys(toKeys);
    }
  }

  function routeNote(routes) {
    const on = routes.filter(rt => !state.routesOff.has(rt));
    if (on.length === routes.length) return "all reductants";
    if (on.length && on.length <= 2) return on.map(routeLabel).join(", ") + " only";
    return on.length + " of " + routes.length + " reductants";
  }

  // Beyond this many technologies the pills become a checkbox dropdown.
  const MAX_TECH_PILLS = 10;
  let techSig = "";
  const techUI = { techs: [], items: new Map(), toggle: null, panel: null, chips: null };
  document.addEventListener("click", e => {
    if (techUI.panel && !techRow.contains(e.target)) techUI.panel.hidden = true;
    if (bioUI.panel && !bioRow.contains(e.target)) bioUI.panel.hidden = true;
  });

  // groups: [{name: "steel"|"iron", techs}], steelmakers first.
  function buildTechRow(groups) {
    techRow.replaceChildren();
    const techs = groups.flatMap(g => g.techs);
    techUI.techs = techs;
    techUI.items.clear();
    techUI.toggle = techUI.panel = techUI.chips = null;
    rowlabel(techRow, "Technology");
    const all = document.createElement("button");
    all.className = "small";
    all.textContent = "All";
    all.onclick = () => { state.techsOff.clear(); redraw(); };
    const none = document.createElement("button");
    none.className = "small";
    none.textContent = "None";
    none.onclick = () => { techs.forEach(t => state.techsOff.add(t)); redraw(); };
    techRow.append(all, none);
    const flip = t => { state.techsOff.has(t) ? state.techsOff.delete(t) : state.techsOff.add(t); redraw(); };
    if (techs.length <= MAX_TECH_PILLS) {
      groups.forEach((g, gi) => {
        if (gi) {
          const sep = document.createElement("span");
          sep.className = "tech-sep";
          sep.textContent = "|";
          techRow.appendChild(sep);
        }
        for (const t of g.techs) techUI.items.set(t, pill(techRow, t, true, () => flip(t)));
      });
      return;
    }
    const dd = document.createElement("span");
    dd.className = "dd";
    techUI.toggle = document.createElement("button");
    techUI.toggle.className = "dd-toggle";
    techUI.panel = document.createElement("div");
    techUI.panel.className = "dd-panel";
    techUI.panel.hidden = true;
    techUI.toggle.onclick = () => { techUI.panel.hidden = !techUI.panel.hidden; placePanel(techUI.toggle, techUI.panel); };
    dd.append(techUI.toggle, techUI.panel);
    groups.forEach((g, gi) => {
      if (gi) {
        const div = document.createElement("div");
        div.className = "dd-sep";
        techUI.panel.appendChild(div);
      }
      for (const t of g.techs) {
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.onchange = () => flip(t);
        const label = document.createElement("label");
        label.append(cb, t);
        techUI.panel.appendChild(label);
        techUI.items.set(t, cb);
      }
    });
    techUI.chips = document.createElement("span");
    techUI.chips.className = "geo-chips";
    techRow.append(dd, techUI.chips);
  }

  function updateTechRow(relevant) {
    const on = t => !state.techsOff.has(t);
    for (const [t, el] of techUI.items) {
      if (el.tagName === "BUTTON") el.classList.toggle("active", on(t));
      else el.checked = on(t);
      el.disabled = !relevant.has(t);
    }
    if (!techUI.toggle) return;
    const sel = techUI.techs.filter(on);
    techUI.toggle.textContent = sel.length + " of " + techUI.techs.length + " technologies ▾";
    techUI.chips.replaceChildren();
    if (!sel.length || sel.length === techUI.techs.length) {
      const m = document.createElement("span");
      m.className = "muted";
      m.textContent = sel.length ? "all technologies selected" : "no technologies selected";
      techUI.chips.appendChild(m);
      return;
    }
    for (const t of sel) {
      const x = document.createElement("button");
      x.textContent = "×";
      x.title = "Remove";
      x.onclick = () => { state.techsOff.add(t); redraw(); };
      const chip = document.createElement("span");
      chip.className = "geo-chip";
      chip.append(t, x);
      techUI.chips.appendChild(chip);
    }
  }

  function renderTechRow(loaded) {
    const rows = loaded.flatMap(l => l.rows || []);
    const techs = [...new Set(rows.map(r => r.technology))].filter(Boolean).sort();
    techRow.hidden = !techs.length;
    if (techRow.hidden) return;
    // Steelmakers first, then ironmakers (a tech making both counts as steel).
    const steel = new Set(rows.filter(r => r.product === "steel").map(r => r.technology));
    const groups = [
      { name: "steel", techs: techs.filter(t => steel.has(t)) },
      { name: "iron", techs: techs.filter(t => !steel.has(t)) },
    ].filter(g => g.techs.length);
    // Techs absent from the chosen product / charge selection are moot.
    let relevant = new Set(techs);
    if (state.category === "capprod" || state.category === "greenfield")
      relevant = new Set(rows.filter(r => r.product === state.product).map(r => r.technology));
    else if (state.category === "charge") {
      const members = chargeMembers();
      relevant = new Set(rows.filter(r => members.has(r.charge)).map(r => r.technology));
    } else if (state.category === "reductant" && state.rmetric === "energy") {
      const col = REDUCTANT_CARRIERS.find(c => c.key === state.carrier).col;
      relevant = new Set(rows.filter(r => r[col] > 0).map(r => r.technology));
    } else if (state.category === "reductant") {
      relevant = new Set(rows.filter(r => !state.routesOff.has(r.reductant)).map(r => r.technology));
    }
    const sig = groups.map(g => g.name + ":" + g.techs.join(",")).join(";");
    if (sig !== techSig) { techSig = sig; buildTechRow(groups); }
    updateTechRow(relevant);
    return { techs, relevant };
  }

  function techNote(techs, relevant) {
    const on = [...relevant].filter(t => !state.techsOff.has(t)).length;
    return on === relevant.size ? "all technologies" : on + " of " + relevant.size + " technologies";
  }

  // Scenario columns are headed by the #n tag (its own grey element) and the name.
  function renderTable(labels, years, series, ints, tags) {
    tableBox.hidden = !labels.length;
    const old = tableBox.querySelector("table");
    if (old) old.remove();
    if (!labels.length) return;
    const table = document.createElement("table");
    const head = table.insertRow();
    const yearTh = document.createElement("th");
    yearTh.textContent = "Year";
    head.appendChild(yearTh);
    labels.forEach((h, i) => {
      const th = document.createElement("th");
      const tag = document.createElement("span");
      tag.className = "scn";
      tag.textContent = tags[i];
      th.append(tag, " " + h);
      head.appendChild(th);
    });
    const fmt = v => ints || v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2);
    years.forEach((y, i) => {
      const tr = table.insertRow();
      tr.insertCell().textContent = y;
      for (const s of series) tr.insertCell().textContent = fmt(s[i]);
    });
    tableBox.appendChild(table);
  }

  function showMessage(text) {
    message.className = "compare-message";
    message.textContent = text;
    message.hidden = !text;
    const on = !text;
    title.hidden = subtitle.hidden = chart.hidden = !on;
    if (!on) { note.textContent = ""; renderTable([], [], []); }
  }

  async function redraw() {
    const t = ++token;
    renderCatRow();
    const category = COMPARE_CATEGORIES.find(c => c.id === state.category);
    if (!category.live) {
      ctrlRow.hidden = techRow.hidden = geoRow.hidden = bioRow.hidden =
        fromRow.hidden = toRow.hidden = true;
      showMessage(category.label + " comparison is coming soon.");
      return;
    }
    renderCtrlRow();
    const scs = state.scenarios;
    if (!scs.length) {
      techRow.hidden = geoRow.hidden = bioRow.hidden =
        fromRow.hidden = toRow.hidden = true;
      showMessage("Pick scenarios in the sidebar to compare them.");
      if (opts.renderEmpty) {
        message.className = "scenario-overview";
        opts.renderEmpty(message);
      }
      return;
    }
    assignSlots(scs);
    const shown = scs.filter(sc => slots.has(sc.key));
    const runs = shown.map(defaultRun);
    const file = dataFile();
    const [geo, ...loaded] = await Promise.all([geoConfig, ...runs.map(r => dataFor(r, file))]);
    if (t !== token) return;

    const okIdx = loaded.map((l, i) => l.rows ? i : -1).filter(i => i >= 0);
    const okRows = okIdx.flatMap(i => loaded[i].rows);
    const routes = renderRoutePills(okRows);
    const bioRegions = renderBioRegionRow(okRows);
    renderTradeRows(okRows, geo);
    const techInfo = renderTechRow(loaded);
    // Hidden before any guard can return early and leave a stale row showing.
    geoRow.hidden = !okRows.length || state.category === "trade";
    if (!okIdx.length) {
      showMessage("No comparison data found for the picked scenarios (missing " + file
        + " — run steelo-viewer regenerate on their runs).");
      return;
    }
    // Trade rows carry no geo_key and leave the shared filter untouched.
    const geoKeys = [...new Set(okRows.map(r => r.geo_key))]
      .filter(g => g && !g.startsWith("region:")).sort();
    if (geoKeys.length) {
      if (!geoFilter) geoFilter = createGeoFilter(geoRow, { geo, onChange: redraw });
      const sig = geoKeys.join(",");
      if (sig !== geoKeysSig) { geoKeysSig = sig; geoFilter.setGeoKeys(geoKeys); }
    }
    if (!regionMembers && geo.regionBudgets)
      regionMembers = new Map(Object.entries(geo.regionBudgets));

    const m = measure();
    if (state.category === "emissions" && !emissionColumns().length) {
      showMessage("Tick direct and/or indirect emissions.");
      return;
    }
    if (state.category === "charge" && !chargeMembers().size) {
      showMessage("Select at least one grade.");
      return;
    }
    if (routes && routes.every(rt => state.routesOff.has(rt))) {
      showMessage("Select at least one reductant.");
      return;
    }
    if (state.category === "supply" && supplyCommodity().grades && !oreMembers().size) {
      showMessage("Select at least one grade.");
      return;
    }
    if (bioRegions && bioRegions.every(k => state.bioRegionsOff.has(k))) {
      showMessage("Select at least one region.");
      return;
    }
    if (state.category === "trade" && tradeFrom
        && (!tradeFrom.selectedGeos().size || !tradeTo.selectedGeos().size)) {
      showMessage("No origin / destination geographies selected.");
      return;
    }
    if (m.geoTest) geoRow.hidden = true;
    else if (!geoFilter.selectedGeos().size) {
      showMessage("No geographies selected.");
      return;
    }

    const years = [...new Set(okRows.map(r => r.year))].sort((a, b) => a - b);
    const { differing, common } = splitDims(set, variantKey, shown.length > 1 ? shown : scs);
    let series = okIdx.map(i => seriesFor(loaded[i].rows, years, m));
    // Ordered by final-year value, so legend and hover read like the chart's right edge.
    const order = [...series.keys()]
      .sort((a, b) => series[b][series[b].length - 1] - series[a][series[a].length - 1]);
    const sortedIdx = order.map(j => okIdx[j]);
    series = order.map(j => series[j]);
    const names = sortedIdx.map(i => seriesLabel(set, shown[i], differing));
    const tags = sortedIdx.map(i => scenarioTag(shown[i]));
    const tagsHtml = sortedIdx.map(i => scenarioTagHtml(shown[i]));
    const colors = sortedIdx.map(i => COMPARE_PALETTE[slots.get(shown[i].key)]);

    showMessage("");
    title.textContent = m.title;
    subtitle.textContent = [
      state.category === "emissions"
        ? COMPARE_BOUNDARIES.find(b => b.id === state.boundary).label + " · " + scopeTitle() : null,
      state.category !== "reductant" ? null
        : routes ? routeNote(routes)
        : "chosen-reductant components only, auxiliary energy excluded",
      state.category === "supply" ? supplyCommodity().where : null,
      state.category === "greenfield" ? "counted at completion (first operating year)" : null,
      state.category === "trade" ? (TRADE_PRODUCTS.find(p => p.key === state.tproduct).note || null) : null,
      state.category === "trade" && state.tflow === "net"
        ? "From → To minus To → From" : null,
      state.category === "trade" && tradeFrom
        ? "from " + tradeFrom.note() + " · to " + tradeTo.note() : null,
      m.geoTest ? null : geoFilter.note(),
      bioRegions ? bioRegionNote(bioRegions) : null,
      techInfo ? techNote(techInfo.techs, techInfo.relevant) : null,
      ...(shown.length > 1 ? common.map(d => dimPair(d, shown[0].dims[d.key])) : []),
      variantDim && scs.some(sc => sc.runs.length > 1)
        ? dimPrefix(variantDim.label) + " " + variantDim.values[0] + " runs" : null,
    ].filter(Boolean).join(" · ");

    const notes = [];
    if (shown.length < scs.length)
      notes.push("Showing the first " + shown.length + " of " + scs.length
        + " picked scenarios — unpick some to see the rest.");
    const failed = loaded.map((l, i) => l.error ? runs[i].dir : null).filter(Boolean);
    if (failed.length) notes.push("No data for: " + failed.join(", "));
    if (series.every(s => s.every(v => v === 0)))
      notes.push("All values are zero for this selection."
        + (state.category === "trade" && state.tflow === "net"
           ? " Symmetric From/To selections net to zero — narrow one side, e.g. From to one country."
           : ""));
    note.textContent = notes.join(" ");

    // Hover rows in columns, padded with non-breaking spaces under a monospace font.
    const fmtVal = v => v.toFixed(m.fmt === ".2f" ? 2 : m.fmt === ".0f" ? 0 : 1);
    const valueTexts = series.map(s => s.map(fmtVal));
    const valueWidth = Math.max(...valueTexts.flat().map(t => t.length));
    const nameWidth = Math.max(...names.map(l => l.length));
    const tagWidth = Math.max(...tags.map(t => t.length));
    const traces = series.map((s, i) => ({
      x: years, y: s, name: tagsHtml[i] + " " + names[i], mode: "lines",
      line: { width: 2, color: colors[i] },
      customdata: valueTexts[i].map(t => tagsHtml[i] + "\u00a0".repeat(tagWidth - tags[i].length + 1) + names[i]
        + "\u00a0".repeat(nameWidth - names[i].length + 2 + valueWidth - t.length) + t),
      hovertemplate: "%{customdata} " + m.unit + "<extra></extra>",
    }));
    // End-of-line labels only while few series, nudged apart where line ends coincide.
    let annotations = [];
    if (traces.length > 1 && traces.length <= 4) {
      const dataMax = Math.max(...series.flat(), 0);
      const gap = 0.05 * dataMax;
      let floor = -Infinity;
      annotations = traces
        .map((tr, i) => ({
          end: tr.y[tr.y.length - 1],
          // Compact value-only form; the legend carries the full labels.
          text: tagsHtml[i] + " " + (differing.length
            ? differing.map(d => shown[sortedIdx[i]].dims[d.key]).join(" · ") : names[i]),
        }))
        .sort((a, b) => a.end - b.end)
        .map(({ end, text }) => {
          const y = Math.max(end, floor + gap);
          floor = y;
          return {
            x: years[years.length - 1], y, xref: "x", yref: "y",
            text, showarrow: false, xanchor: "left", xshift: 8,
            font: { size: 11.5, color: "#1f2733" },
          };
        });
    }

    const Plotly = await loadPlotly().catch(e => { showMessage(e.message); return null; });
    if (!Plotly || t !== token) return;
    Plotly.react(chart, traces, {
      margin: { l: 60, r: annotations.length ? 150 : 20, t: 10, b: 40 },
      hovermode: "x unified",
      hoverlabel: { font: { family: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace", size: 11.5 } },
      paper_bgcolor: "#ffffff", plot_bgcolor: "#ffffff",
      font: { family: '-apple-system, "Segoe UI", sans-serif', size: 12.5, color: "#1f2733" },
      xaxis: { dtick: 5, gridcolor: "#eceef2", zeroline: false,
               range: [years[0] - 0.5, years[years.length - 1] + 0.5] },
      yaxis: { title: { text: m.unit, font: { size: 12 } }, gridcolor: "#eceef2",
               zeroline: false, rangemode: "tozero" },
      legend: { orientation: "h", y: 1, yanchor: "bottom" },
      showlegend: traces.length > 1,
      annotations,
    }, { responsive: true, displaylogo: false,
         modeBarButtonsToRemove: ["select2d", "lasso2d", "autoScale2d"] });
    plotted = true;
    // The table names scenarios in full, whatever the chart abbreviates them to.
    renderTable(sortedIdx.map(i => scenarioLabel(set, shown[i])), years, series, m.fmt === ".0f", tags);
  }

  renderCatRow();
  renderCtrlRow();
  techRow.hidden = geoRow.hidden = true;
  showMessage("Pick scenarios in the sidebar to compare them.");

  return { show(scenarios) { state.scenarios = scenarios; redraw(); } };
}
