"use strict";
/* Guided tour: a spotlight and a card per step, switching views as it goes and restoring the page after.
   src/steelo/viewer/scripts/check_tour.mjs walks every step against the running page. */

// "a, b and c" from the app's own labels, escaped for the card's HTML.
const tourEsc = t => String(t).replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
function tourList(items) {
  const xs = items.map(tourEsc);
  return xs.length < 2 ? xs.join("") : xs.slice(0, -1).join(", ") + " and " + xs[xs.length - 1];
}
const tourDims = app => app.set().dimensions.filter(d => d.key !== app.variantKey);
// Labels mid-sentence lose their leading capital (dimPrefix keeps an acronym's).

const TOUR_STEPS = [
  {
    title: "Welcome to the Steel-IQ scenario viewer",
    body: "<p>Every run this viewer serves is here: its interactive plots, one measure compared "
      + "across scenarios, the annual cost of a tonne of iron or steel, and the input data each scenario "
      + "was run with.</p><p>The tour takes about two minutes. Use <b>→</b> and <b>←</b> to move and "
      + "<b>Esc</b> to leave it.</p>",
  },
  {
    title: "Five views",
    target: ".modetabs", at: "right",
    before: (h, app) => { h.sidebar(); },
    body: "<p><b>Explore</b> shows one scenario's plots, or two side by side. <b>Compare</b> charts one measure across several "
      + "scenarios. <b>Annual costs</b> stacks the cost of a tonne of iron or crude steel. "
      + "<b>View data</b> lists the inputs the scenarios were run with. <b>Download results</b> hands "
      + "out one scenario's result tables.</p>",
  },
  {
    title: "Filters",
    target: ".filters-card", at: "right",
    before: (h, app) => { app.setMode("explore"); h.sidebar(); h.filters(); },
    body: app => {
      const base = tourDims(app).map(d => dimPrefix(d.label));
      if (!base.length)
        return "<p>No filters yet: they appear once the runs differ in a setting or in their input data.</p>";
      return "<p>A scenario is one combination of the filters: " + tourList(base) + ". They are the settings "
        + "that differ between the runs, with the run name added when two runs would otherwise look the same.</p>"
        + "<p>Click a pill to narrow the list. A greyed pill has no scenario left under the other filters. "
      + "<i>Clear</i> resets them, the chevron folds the card into a row of chips, and the divider "
      + "below it drags.</p>";
    },
  },
  {
    title: "The run table",
    target: "#explore-pane .rt-table", at: "bottom",
    before: (h, app) => { app.setMode("explore"); h.sidebar(); h.overview(); },
    body: "<p>With nothing picked, every run sits in one table, one row per run: its scenario's <b>#n</b>, "
      + "its name, when it started and the input data it used. A faded row is left out by the filters.</p>"
      + "<p>Click a row to explore that run, or switch the toggle to pick two for the split view. "
      + "<i>details</i> opens the run's settings, shown next.</p>"
      + "<p>The same table picks in Compare, Annual costs, View data and Download results, and "
      + "<i>all runs</i> beside the scenario list brings it back.</p>",
  },
  {
    title: "Run details",
    target: "#explore-pane .rd-sec", at: "bottom",
    before: (h, app) => { app.setMode("explore"); h.sidebar(); h.overview(); h.details(); },
    body: "<p>The top answers what sets the run apart: its period, seed and scenarios, then every setting "
      + "<b>changed from the default</b> and every one that <b>varies across the listed runs</b>. Click one to "
      + "jump to it.</p>"
      + "<p>The contents beside it jump to each group of settings. A blue dot marks a change, a purple dot a "
      + "difference between the runs, and a greyed group is switched off.</p>",
  },
  {
    title: "Scenarios",
    target: "#picker .runs", at: "right",
    before: (h, app) => { app.setMode("explore"); h.sidebar(); h.pickOne(); },
    body: "<p>Every scenario matching the filters, each with a fixed <b>#n</b> tag. The charts repeat "
      + "the tag, and it never changes with the filters, so #7 on a chart is always #7 here.</p>"
      + "<p>Click one to explore it. In Compare and Annual costs you tick as many as you like, and "
      + "<i>select all</i> picks whatever the filters currently show.</p>",
  },
  {
    title: "Explore: the plot pages",
    target: "#explore-pane header", at: "bottom",
    before: (h, app) => { app.setMode("explore"); h.sidebar(); h.pickOne(); h.bar("#explore-pane"); },
    body: app => {
      const plots = app.set().plots.map(p => p.label);
      const variant = app.set().dimensions.find(d => d.key === app.variantKey);
      return "<p>Each run comes with " + plots.length + " plot pages written by the model: "
        + tourList(plots) + ".</p>"
        + (variant ? "<p>A <i>" + tourEsc(variant.label) + "</i> row appears above the tabs when a scenario "
                     + "was run several times, differing only in that.</p>" : "");
    },
  },
  {
    title: "Explore: the plot",
    target: "#explore-pane iframe",
    before: (h, app) => { app.setMode("explore"); h.sidebar(); h.pickOne(); },
    body: "<p>The plots are the model's own interactive pages: hover for values, click legend entries to "
      + "hide series, and use the controls at the top of each page.</p>"
      + "<p>The address bar always names the run and plot, so copy it to share exactly this view. "
      + "The control bar above drags to resize and folds away with its chevron.</p>",
  },
  {
    title: "Explore: split view",
    target: "#explore-split-pane .split-sides",
    before: (h, app) => { app.setMode("explore"); h.sidebar(); h.split(); },
    body: "<p>Tick <b>Split view</b> above the scenario list to see two scenarios side by side. Clicks in "
      + "the list then fill <b>A</b> and <b>B</b> in turn; the A / B toggle chooses which side the next "
      + "click sets, <i>⇄ Swap</i> trades the two sides, and <i>clear A</i> or <i>clear B</i> empties a side. "
      + "An empty side shows the run table to pick from, and <i>change</i> beside a side's name empties it.</p>"
      + "<p>The plot tabs drive both sides at once, and the address bar names both runs, so the link "
      + "shares the comparison.</p>",
  },
  {
    title: "Compare",
    target: "#compare-pane header", at: "bottom",
    before: (h, app) => { app.setMode("compare"); h.sidebar(); h.pickTwo(); h.bar("#compare-pane"); },
    body: () => "<p>One measure across the ticked scenarios, in categories mirroring the plot pages: "
      + tourList(COMPARE_CATEGORIES.map(c => dimPrefix(c.label))) + ".</p>"
      + "<p>Each category brings its own controls (boundary and scope for emissions, product and flow "
      + "direction for trade), plus a technology multi-select and a geography filter of countries, "
      + "sub-national units, trade blocs and regions. The numbers sit in the <i>Data table</i> under "
      + "the chart.</p>"
      + "<p>With nothing ticked, here and in Annual costs, the run table appears instead: tick up "
      + "to 20 and press <i>Show</i>.</p>",
  },
  {
    title: "Annual costs",
    target: "#annual-costs-pane header", at: "bottom",
    before: (h, app) => { app.setMode("annual-costs"); h.sidebar(); h.pickTwo(); h.bar("#annual-costs-pane"); },
    body: () => "<p>The cost of a tonne of iron or crude steel, stacked by component: "
      + tourList(ANNUAL_COST_GROUPS.map(g => dimPrefix(g.label))) + ". It is the model's own "
      + "unit production cost, production-weighted over your selection.</p>"
      + "<p>Show it as a breakdown over time, one stack per scenario, or as bars for a single year. Split "
      + "a group into its members, and pick technologies and reductants separately.</p>",
  },
  {
    title: "View data",
    target: "#topics", at: "right",
    before: (h, app) => { app.setMode("data"); h.sidebar(); h.topic(); },
    body: app => {
      const topics = app.inputs().getTopics().map(dimPrefix);
      return "<p>The inputs the runs were made with, by topic" + (topics.length ? ": " + tourList(topics) : "")
        + ", read from the runs' master workbooks.</p>"
        + "<p>A row that is the same in every run appears once. A row that differs is followed by a "
      + "pale-orange row per variant, tagged with the filter setting that activates it. The filters above "
      + "keep only the variants in use, so filtering down to one scenario shows exactly its inputs.</p>"
      + "<p>With no sheet picked, the run table shows which runs the filters cover; click one "
      + "to filter to exactly its scenario. Clicking <i>View data</i> again brings the table back.</p>";
    },
  },
  {
    title: "Working with a table",
    target: "#data-pane header", at: "bottom",
    before: (h, app) => { app.setMode("data"); h.topic(); h.bar("#data-pane"); },
    body: "<p>Narrow to the differing rows, search, filter any key column, sort by a column header and "
      + "pull the year slider in. Year topics can also be charted, one line per row, and every view "
      + "exports as CSV.</p>",
  },
  {
    title: "Download results",
    target: "#results-pane",
    before: (h, app) => { h.sidebar(); app.setMode("results"); h.pickOne(); },
    body: "<p>One scenario's result tables, in the folders its run has among run, data, LCOE, LCOH, trade "
      + "and policy. Download a table as a gzipped CSV, or all of them as one zip, or view it here page by page. "
      + "With nothing picked, click a run in the table.</p>",
  },
  {
    title: "That's the tour",
    target: () => { const l = document.querySelector("#tour-link");
                    return l.getClientRects().length ? l : document.querySelector("#menu-toggle"); },
    at: "bottom",
    // Framed by the app, the banner and both targets are hidden: the card sits mid-frame and names the app's Tour
    body: () => "<p>Restart it any time from <b>Tour</b> "
      + (document.documentElement.classList.contains("framed") ? "above the viewer. "
        : "in the top bar (under the ☰ on a narrow screen). ")
      + "The sidebar width, the folded bars and "
      + "the filters card's height are remembered in this browser.</p>",
  },
];

function createTour(app) {
  const q = sel => document.querySelector(sel);
  const visible = el => !!el && el.getClientRects().length > 0;
  const PAD = 6, GAP = 12, EDGE = 12;
  let ui = null, step = -1, target = null, undo = [], saved = null;
  // A target's rows can fill in after it is measured, so the spotlight follows its size.
  const sizer = new ResizeObserver(() => place());

  // Folded parts open through their own buttons, so the remembered preference follows, and fold back.
  function unfold(folded, button) {
    if (!folded || !button) return;
    button.click();
    undo.push(() => button.click());
  }
  const helpers = {
    sidebar: () => unfold(document.body.classList.contains("sidebar-collapsed"), q("#collapse")),
    filters: () => unfold(q("#picker").classList.contains("filters-collapsed"), q("#picker .filters-toggle")),
    bar: pane => unfold(q(pane).classList.contains("bar-collapsed"), q(pane + " .bar-collapse .chevron")),
    // Single mode: the first listed scenario when none is open.
    pickOne: () => {
      if (app.viewer().getScenario()) return;
      const b = q("#picker .runs button");
      if (b) b.click();
    },
    // Explore with nothing picked, which shows the run table.
    overview: () => {
      if (!app.viewer().getScenario()) return;
      app.sidebar().setScenario(null);
      app.viewer().show(null);
    },
    // The first run's details in the run table; closed again at the end.
    details: () => {
      if (q("#explore-pane .rt-details")) return;
      const more = q("#explore-pane .rt-more");
      if (!more) return;
      more.click();
      undo.push(() => { if (more.isConnected && more.textContent === "hide") more.click(); });
    },
    // Multi mode: tick listed scenarios until two are picked.
    pickTwo: () => {
      for (const b of document.querySelectorAll("#picker .runs button")) {
        if (app.sidebar().getScenarios().length >= 2) break;
        if (!b.classList.contains("checked")) b.click();
      }
    },
    // Split view with both sides filled; turned off again at the end.
    split: () => {
      if (!app.splitOn()) {
        app.setSplit(true);
        undo.push(() => { app.setSplit(false); app.sidebar().setSplit(null, null); });
      }
      for (const b of document.querySelectorAll("#picker .runs button")) {
        if (app.sidebar().getScenarioA() && app.sidebar().getScenarioB()) break;
        if (!b.classList.contains("picked-a") && !b.classList.contains("picked-b")) b.click();
      }
    },
    // The first topic once the list has arrived with inputs.json.
    topic: (tries = 15) => {
      if (app.getMode() !== "data" || q("#topics .runs button.active")) return;
      const b = q("#topics .runs button");
      if (b) b.click();
      else if (tries) setTimeout(() => helpers.topic(tries - 1), 200);
    },
  };

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    e.className = cls;
    if (text) e.textContent = text;
    return e;
  }
  function build() {
    const overlay = el("div", "tour-overlay");
    const spot = el("div", "tour-spot");
    const card = el("div", "tour-card");
    card.setAttribute("role", "dialog");
    card.setAttribute("aria-label", "Tour");
    const title = el("div", "tour-title");
    const body = el("div", "tour-body");
    const foot = el("div", "tour-foot");
    const count = el("span", "tour-count");
    const skip = el("button", "tour-skip", "Skip tour");
    skip.onclick = end;
    const back = el("button", "tour-back", "Back");
    back.onclick = () => go(step - 1);
    const next = el("button", "tour-next", "Next");
    next.onclick = () => go(step + 1);
    foot.append(count, skip, back, next);
    card.append(title, body, foot);
    document.body.append(overlay, spot, card);
    ui = { overlay, spot, card, title, body, count, back, next };
  }

  function resolve(t) {
    const e = typeof t === "function" ? t() : t ? q(t) : null;
    return visible(e) ? e : null;
  }

  function go(n) {
    if (n < 0) return;
    if (n >= TOUR_STEPS.length) { end(); return; }
    step = n;
    const s = TOUR_STEPS[n];
    try { if (s.before) s.before(helpers, app); } catch (e) { console.warn("tour step " + n, e); }
    // Two frames, so a view switched on just now is laid out before it is measured.
    requestAnimationFrame(() => requestAnimationFrame(() => {
      if (!ui || step !== n) return;
      target = resolve(s.target);
      sizer.disconnect();
      if (target) sizer.observe(target);
      ui.title.textContent = s.title;
      ui.body.innerHTML = typeof s.body === "function" ? s.body(app) : s.body;
      ui.count.textContent = (n + 1) + " / " + TOUR_STEPS.length;
      ui.back.disabled = n === 0;
      ui.next.textContent = n === TOUR_STEPS.length - 1 ? "Finish" : "Next";
      if (target) target.scrollIntoView({ block: "nearest", inline: "nearest" });
      place();
      ui.next.focus();
    }));
  }

  // The card on whichever side of the target has room, else over the middle.
  function place() {
    if (!ui) return;
    const { card, spot, overlay } = ui;
    const vw = window.innerWidth, vh = window.innerHeight;
    const cw = card.offsetWidth, ch = card.offsetHeight;
    const clamp = (v, max) => Math.min(Math.max(EDGE, v), Math.max(EDGE, max));
    if (!target || !visible(target)) {
      spot.hidden = true;
      overlay.classList.add("dim");
      card.style.left = clamp((vw - cw) / 2, vw - cw - EDGE) + "px";
      card.style.top = clamp((vh - ch) / 2, vh - ch - EDGE) + "px";
      return;
    }
    overlay.classList.remove("dim");
    const r = target.getBoundingClientRect();
    const box = { left: r.left - PAD, top: r.top - PAD, width: r.width + 2 * PAD, height: r.height + 2 * PAD };
    spot.hidden = false;
    Object.assign(spot.style, { left: box.left + "px", top: box.top + "px",
                                width: box.width + "px", height: box.height + "px" });
    const fits = {
      right: box.left + box.width + GAP + cw <= vw - EDGE,
      left: box.left - GAP - cw >= EDGE,
      bottom: box.top + box.height + GAP + ch <= vh - EDGE,
      top: box.top - GAP - ch >= EDGE,
    };
    const at = [TOUR_STEPS[step].at || "right", "right", "left", "bottom", "top"].find(k => fits[k]);
    let x, y;
    if (at === "right") { x = box.left + box.width + GAP; y = box.top; }
    else if (at === "left") { x = box.left - GAP - cw; y = box.top; }
    else if (at === "bottom") { x = box.left; y = box.top + box.height + GAP; }
    else if (at === "top") { x = box.left; y = box.top - GAP - ch; }
    else { x = box.left + (box.width - cw) / 2; y = box.top + (box.height - ch) / 2; }
    card.style.left = clamp(x, vw - cw - EDGE) + "px";
    card.style.top = clamp(y, vh - ch - EDGE) + "px";
  }

  function onKey(e) {
    if (e.key === "Escape") end();
    else if (e.key === "ArrowRight") go(step + 1);
    else if (e.key === "ArrowLeft") go(step - 1);
    else return;
    e.preventDefault();
  }

  function start() {
    if (ui) return;
    saved = { hash: location.hash, mode: app.getMode() };
    try { localStorage.setItem("tourSeen", "1"); } catch (e) {}
    build();
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    go(0);
  }

  function end() {
    if (!ui) return;
    for (const u of undo.reverse()) { try { u(); } catch (e) {} }
    undo = [];
    for (const e of [ui.overlay, ui.spot, ui.card]) e.remove();
    sizer.disconnect();
    ui = null;
    target = null;
    step = -1;
    document.removeEventListener("keydown", onKey);
    window.removeEventListener("resize", place);
    window.removeEventListener("scroll", place, true);
    // Back where the tour started, through the page's own hashchange handling.
    if (saved.hash) { if (location.hash !== saved.hash) location.hash = saved.hash; }
    else {
      app.setMode("explore");
      app.sidebar().setScenario(null);
      app.viewer().show(null);
    }
  }

  return { start, end };
}
