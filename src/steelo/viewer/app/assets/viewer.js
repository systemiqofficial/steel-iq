"use strict";
/* Plot pane for one scenario (variant row, plot tabs, iframe), and the Explore split view. */

// Drag divider and collapse chevron for a pane's control header, remembered per pane under storageKey.
function attachBarControls(container, header, storageKey) {
  const MIN_BAR = 40;
  const hresizer = document.createElement("div");
  hresizer.className = "hresizer";
  hresizer.title = "drag to resize · double-click to reset";
  const collapseRow = document.createElement("div");
  collapseRow.className = "bar-collapse";
  const collapseBtn = document.createElement("button");
  collapseBtn.className = "chevron";
  collapseBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none"'
    + ' stroke="currentColor" stroke-width="2.4"><path d="M6 15l6-6 6 6"/></svg>';
  const summary = document.createElement("span");
  summary.className = "bar-summary";
  summary.title = "Show controls";
  collapseRow.append(collapseBtn, summary);
  const reset = () => { header.style.height = ""; header.style.overflowY = ""; };
  hresizer.addEventListener("dblclick", reset);
  hresizer.addEventListener("mousedown", e => {
    e.preventDefault();
    document.body.classList.add("resizing");
    const top = header.getBoundingClientRect().top;
    const move = ev => {
      header.style.height = Math.min(Math.max(MIN_BAR, ev.clientY - top), window.innerHeight / 2) + "px";
      header.style.overflowY = "auto";   // rows beyond the chosen height scroll
    };
    const up = () => {
      document.body.classList.remove("resizing");
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  });
  function apply(collapsed) {
    container.classList.toggle("bar-collapsed", collapsed);
    collapseBtn.setAttribute("aria-expanded", String(!collapsed));
    collapseBtn.title = collapsed ? "Show controls" : "Hide controls";
    collapseBtn.setAttribute("aria-label", collapseBtn.title);
  }
  try { apply(localStorage.getItem(storageKey) === "1"); } catch (e) {}
  collapseBtn.onclick = () => {
    const collapsed = !container.classList.contains("bar-collapsed");
    apply(collapsed);
    try { localStorage.setItem(storageKey, collapsed ? "1" : "0"); } catch (e) {}
  };
  summary.onclick = collapseBtn.onclick;
  return { hresizer, collapseRow, reset, setSummary(text) { summary.textContent = text || ""; } };
}

// Shared with createSplitViewer: a run's first available plot, and a plot file's URL.
function firstPlot(set, r) { return r ? (set.plots.find(p => r.plots.includes(p.file)) || {}).file : undefined; }
function runUrl(set, r, p) { return set.base.replace(/\/$/, "") + "/" + r.dir + "/plots/interactive/" + p; }

function createPlotViewer(container, set, opts) {
  const variantKey = opts.variantKey;
  const variantDim = set.dimensions.find(d => d.key === variantKey);
  let scenario = null;
  let run = null;
  let plot = null;

  container.replaceChildren();
  const header = document.createElement("header");
  const variantBar = document.createElement("div");
  variantBar.className = "menurow";
  const bar = document.createElement("div");
  bar.className = "menurow";
  header.append(variantBar, bar);
  const message = document.createElement("div");
  message.className = "compare-message";
  message.textContent = "Pick a scenario in the sidebar to explore its plots.";
  if (opts.renderEmpty) {
    message.className = "scenario-overview";
    opts.renderEmpty(message);
  }
  const frame = document.createElement("iframe");
  frame.title = "interactive plot";
  const controls = attachBarControls(container, header, "plotbarCollapsed");
  container.append(header, controls.hresizer, controls.collapseRow, message, frame);

  const buttons = new Map();
  for (const p of set.plots) {
    const b = document.createElement("button");
    b.textContent = p.label;
    b.onclick = () => show(scenario, run, p.file);
    buttons.set(p.file, b);
    bar.appendChild(b);
  }

  function renderVariants() {
    variantBar.replaceChildren();
    variantBar.hidden = !scenario || scenario.runs.length < 2;
    if (variantBar.hidden) return;
    const lab = document.createElement("span");
    lab.className = "rowlabel";
    lab.textContent = variantDim ? variantDim.label : variantKey;
    variantBar.appendChild(lab);
    for (const r of scenario.runs) {
      const b = document.createElement("button");
      b.textContent = r.dims[variantKey];
      b.title = r.dir;
      b.classList.toggle("active", r === run);
      b.onclick = () => show(scenario, r, plot);
      variantBar.appendChild(b);
    }
  }

  function show(sc, r, p) {
    // A manual bar resize is per scenario: a new scenario shows the whole bar again.
    if (sc !== scenario) controls.reset();
    scenario = sc;
    // Stay on the same variant across scenarios when the new one has it too.
    const prevVariant = run && run.dims[variantKey];
    run = !sc ? null
      : r && sc.runs.includes(r) ? r
      : sc.runs.find(x => x.dims[variantKey] === prevVariant) || sc.runs[0];
    const want = p || plot;
    plot = run ? (want && run.plots.includes(want) ? want : firstPlot(set, run)) : null;
    renderVariants();
    for (const [file, b] of buttons) {
      b.disabled = !run || !run.plots.includes(file);
      b.classList.toggle("active", !!run && file === plot);
    }
    container.classList.toggle("no-scenario", !scenario);
    controls.setSummary(plot ? (set.plots.find(p => p.file === plot) || {}).label : "");
    message.hidden = !!scenario;
    const src = run && plot ? runUrl(set, run, plot) : "about:blank";
    if (frame.getAttribute("src") !== src) frame.setAttribute("src", src);
    if (opts.onChange) opts.onChange(run, plot);
  }

  return { show, getScenario: () => scenario, getRun: () => run, getPlot: () => plot };
}

// Explore split view: one shared plot-tab row over sides A and B, each falling back to its own first plot.
function createSplitViewer(container, set, opts) {
  const variantKey = opts.variantKey;
  const variantDim = set.dimensions.find(d => d.key === variantKey);
  let scenarioA = null, runA = null, scenarioB = null, runB = null, wantPlot = null;

  container.replaceChildren();
  const header = document.createElement("header");
  const bar = document.createElement("div");
  bar.className = "menurow";
  header.append(bar);
  const controls = attachBarControls(container, header, "splitBarCollapsed");
  const sides = document.createElement("div");
  sides.className = "split-sides";
  container.append(header, controls.hresizer, controls.collapseRow, sides);

  function makeSide(letter) {
    const label = document.createElement("div");
    label.className = "split-label";
    const chip = document.createElement("span");
    chip.className = "ab-chip";
    chip.textContent = letter;
    const name = document.createElement("span");
    label.append(chip, name);
    if (opts.onUnpick) {
      const change = document.createElement("a");
      change.className = "split-change";
      change.textContent = "change";
      change.title = "Pick another scenario " + letter;
      change.onclick = () => opts.onUnpick(letter);
      label.appendChild(change);
    }
    const variantBar = document.createElement("div");
    variantBar.className = "split-variant";
    const message = document.createElement("div");
    message.className = "compare-message";
    message.textContent = "Pick scenario " + letter + " in the sidebar.";
    if (opts.renderEmpty) message.className = "scenario-overview";
    const frame = document.createElement("iframe");
    frame.title = "interactive plot (" + letter + ")";
    const side = document.createElement("div");
    side.className = "split-side side-" + letter.toLowerCase();
    side.append(label, variantBar, message, frame);
    sides.appendChild(side);
    return { letter, side, label, name, variantBar, message, frame, drawn: null };
  }
  const A = makeSide("A"), B = makeSide("B");

  function drawEmpty(side) {
    const sc = side === A ? scenarioA : scenarioB, other = side === A ? scenarioB : scenarioA;
    const width = side.message.clientWidth;   // 0 while hidden
    if (sc || !opts.renderEmpty || !width) return;
    if (side.drawn && side.drawn.other === other && side.drawn.width === width) return;
    side.drawn = { other, width };
    opts.renderEmpty(side.message, side.letter, other);
  }
  if (opts.renderEmpty)
    for (const side of [A, B]) new ResizeObserver(() => drawEmpty(side)).observe(side.message);

  const buttons = new Map();
  for (const p of set.plots) {
    const b = document.createElement("button");
    b.textContent = p.label;
    b.onclick = () => { wantPlot = p.file; refresh(); };
    buttons.set(p.file, b);
    bar.appendChild(b);
  }

  function renderVariant(side, sc, run, onPick) {
    side.variantBar.replaceChildren();
    side.variantBar.hidden = !sc || sc.runs.length < 2;
    if (side.variantBar.hidden) return;
    const lab = document.createElement("span");
    lab.className = "rowlabel";
    lab.textContent = variantDim ? variantDim.label : variantKey;
    side.variantBar.appendChild(lab);
    for (const r of sc.runs) {
      const b = document.createElement("button");
      b.textContent = r.dims[variantKey];
      b.title = r.dir;
      b.classList.toggle("active", r === run);
      b.onclick = () => onPick(r);
      side.variantBar.appendChild(b);
    }
  }

  function renderSide(side, sc, run, plot) {
    side.side.classList.toggle("no-scenario", !sc);
    side.label.hidden = !sc;
    side.name.textContent = sc ? scenarioLabel(set, sc) : "";
    side.message.hidden = !!sc;
    drawEmpty(side);
    const src = run && plot ? runUrl(set, run, plot) : "about:blank";
    if (side.frame.getAttribute("src") !== src) side.frame.setAttribute("src", src);
  }

  function render() {
    const plotA = runA && (wantPlot && runA.plots.includes(wantPlot) ? wantPlot : firstPlot(set, runA));
    const plotB = runB && (wantPlot && runB.plots.includes(wantPlot) ? wantPlot : firstPlot(set, runB));
    renderVariant(A, scenarioA, runA, r => { runA = r; refresh(); });
    renderVariant(B, scenarioB, runB, r => { runB = r; refresh(); });
    renderSide(A, scenarioA, runA, plotA);
    renderSide(B, scenarioB, runB, plotB);
    for (const [file, b] of buttons) {
      const inA = !!(runA && runA.plots.includes(file)), inB = !!(runB && runB.plots.includes(file));
      b.disabled = !inA && !inB;
      b.classList.toggle("active", !!wantPlot && file === wantPlot);
    }
    controls.setSummary((set.plots.find(p => p.file === wantPlot) || {}).label || "");
  }
  function refresh() {
    render();
    if (opts.onChange) opts.onChange({ runA, runB, plot: wantPlot });
  }

  function show(scA, rA, scB, rB, p) {
    if (scA !== scenarioA || scB !== scenarioB) controls.reset();
    scenarioA = scA;
    scenarioB = scB;
    runA = !scA ? null : rA && scA.runs.includes(rA) ? rA : scA.runs[0];
    runB = !scB ? null : rB && scB.runs.includes(rB) ? rB : scB.runs[0];
    if (p) wantPlot = p;
    if (!wantPlot) wantPlot = firstPlot(set, runA) || firstPlot(set, runB);
    refresh();
  }

  render();
  return { show, getScenarioA: () => scenarioA, getRunA: () => runA,
           getScenarioB: () => scenarioB, getRunB: () => runB, getPlot: () => wantPlot };
}
