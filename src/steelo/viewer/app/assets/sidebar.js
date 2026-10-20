"use strict";
/* Scenario sidebar: the filter pills and the matching-scenario list, in single, multi, split or filters mode. */

function groupScenarios(set, variantKey) {
  const others = set.dimensions.filter(d => d.key !== variantKey);
  const map = new Map();
  for (const r of set.runs) {
    const key = others.map(d => r.dims[d.key]).join("|");
    if (!map.has(key)) {
      const dims = {};
      for (const d of others) dims[d.key] = r.dims[d.key];
      map.set(key, { key, n: map.size + 1, dims, runs: [] });
    }
    map.get(key).runs.push(r);
  }
  return [...map.values()];
}

// Lowercase only the first letter; a label opening with an acronym ("BF allowed") is left alone.
function dimPrefix(label) {
  const second = label.charAt(1);
  if (second && second !== second.toLowerCase()) return label;
  return label.charAt(0).toLowerCase() + label.slice(1);
}

// "label value" for chips and subtitles.
function dimPair(d, v) {
  return dimPrefix(d.label) + " " + v;
}

// A scenario name: the first dimension's value bare, later ones after their label.
function dimText(d, v, i) {
  return i === 0 ? v : dimPrefix(d.label) + " " + v;
}

function scenarioLabel(set, scenario) {
  return set.dimensions.filter(d => d.key in scenario.dims)
    .map((d, i) => dimText(d, scenario.dims[d.key], i)).join(" · ");
}

// Dims that vary across the picks name each series; the shared rest goes into a subtitle once.
function splitDims(set, variantKey, scenarios) {
  const dims = set.dimensions.filter(d => d.key !== variantKey);
  const differing = dims.filter(d => new Set(scenarios.map(sc => sc.dims[d.key])).size > 1);
  return { differing, common: dims.filter(d => !differing.includes(d)) };
}

// Only the set's first dimension is bare: its values name themselves.
function seriesLabel(set, scenario, differing) {
  if (!differing.length) return scenarioLabel(set, scenario);
  return differing.map(d => {
    const v = scenario.dims[d.key];
    return d === set.dimensions[0] ? v : dimPrefix(d.label) + " " + v;
  }).join(" · ");
}

// A scenario's "#n" tag, fixed whatever the filters; the HTML form greys it for Plotly text.
function scenarioTag(sc) { return "#" + sc.n; }
function scenarioTagHtml(sc) { return '<span style="color:#66707d">' + scenarioTag(sc) + "</span>"; }

function createScenarioSidebar(container, set, opts) {
  const scenarios = groupScenarios(set, opts.variantKey);
  const dimensions = set.dimensions.filter(d => d.key !== opts.variantKey);
  const state = {
    mode: "single",
    filters: Object.fromEntries(dimensions.map(d => [d.key, null])),
    scenario: null,
    picked: new Set(),
    scenarioA: null,
    scenarioB: null,
    armed: "A",
  };

  container.replaceChildren();
  const headBtn = document.createElement("button");
  headBtn.className = "filters-toggle";
  headBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none"'
    + ' stroke="currentColor" stroke-width="2.4"><path d="M6 15l6-6 6 6"/></svg>';
  const headLab = document.createElement("div");
  headLab.className = "section-title";
  headLab.textContent = "Filters";
  const clearFilters = document.createElement("a");
  clearFilters.className = "clear-filters";
  clearFilters.textContent = "clear";
  clearFilters.title = "Clear all filters";
  clearFilters.onclick = () => {
    for (const d of dimensions) state.filters[d.key] = null;
    announceFilters();
    filtersChanged();
  };
  const filtersHead = document.createElement("div");
  filtersHead.className = "filters-head";
  filtersHead.append(headBtn, headLab, clearFilters);
  const filtersWrap = document.createElement("div");
  filtersWrap.className = "filters-wrap";
  const filtersBox = document.createElement("div");
  filtersWrap.appendChild(filtersBox);
  const summaryBox = document.createElement("div");
  summaryBox.className = "filters-summary pills";
  const filtersCard = document.createElement("div");
  filtersCard.className = "filters-card";
  filtersCard.append(filtersHead, filtersWrap, summaryBox);
  const vresizer = document.createElement("div");
  vresizer.className = "vresizer";
  vresizer.title = "drag to resize";
  const listLab = document.createElement("div");
  listLab.className = "section-title";
  listLab.textContent = "Scenarios";
  const countLine = document.createElement("div");
  countLine.className = "runcount";
  const listHead = document.createElement("div");
  listHead.className = "list-head";
  const overviewLink = document.createElement("a");
  overviewLink.className = "overview-link";
  overviewLink.textContent = "overview";
  overviewLink.title = "Show all of this set's scenarios in one table";
  overviewLink.onclick = () => opts.onOverview();
  listHead.append(listLab, overviewLink, countLine);
  const listBox = document.createElement("div");
  listBox.className = "runs";
  const armLab = document.createElement("span");
  armLab.className = "rowlabel";
  armLab.textContent = "Next click sets";
  const armA = document.createElement("button");
  armA.className = "arm-a";
  armA.textContent = "A";
  armA.onclick = () => { state.armed = "A"; render(); };
  const armB = document.createElement("button");
  armB.className = "arm-b";
  armB.textContent = "B";
  armB.onclick = () => { state.armed = "B"; render(); };
  const swapAB = document.createElement("button");
  swapAB.className = "swap-ab";
  swapAB.textContent = "\u21c4 Swap";
  swapAB.title = "Swap scenarios A and B";
  swapAB.onclick = () => {
    [state.scenarioA, state.scenarioB] = [state.scenarioB, state.scenarioA];
    if (!state.scenarioA || !state.scenarioB) state.armed = state.scenarioA ? "B" : "A";
    render();
    emit();
  };
  const armRow = document.createElement("div");
  armRow.className = "arm-row";
  armRow.append(armLab, armA, armB, swapAB);
  const splitCheck = document.createElement("input");
  splitCheck.type = "checkbox";
  const splitToggle = document.createElement("label");
  splitToggle.className = "split-toggle";
  splitToggle.append(splitCheck, document.createTextNode(" Split view: two scenarios side by side"));
  splitCheck.onchange = () => opts.onSplitToggle(splitCheck.checked);
  const splitControls = document.createElement("div");
  splitControls.className = "split-controls";
  splitControls.append(splitToggle, armRow);
  container.append(filtersCard, vresizer, splitControls, listHead, listBox);

  function applyFiltersCollapsed(collapsed) {
    container.classList.toggle("filters-collapsed", collapsed);
    headBtn.setAttribute("aria-expanded", String(!collapsed));
    headBtn.title = collapsed ? "Show filter options" : "Collapse filters";
    headBtn.setAttribute("aria-label", headBtn.title);
  }
  try { applyFiltersCollapsed(localStorage.getItem("filtersCollapsed") === "1"); } catch (e) {}
  headBtn.onclick = () => {
    const collapsed = !container.classList.contains("filters-collapsed");
    applyFiltersCollapsed(collapsed);
    try { localStorage.setItem("filtersCollapsed", collapsed ? "1" : "0"); } catch (e) {}
  };

  try {
    const h = localStorage.getItem("filtersHeight");
    if (h) { filtersCard.style.height = h; filtersCard.style.maxHeight = "none"; }
  } catch (e) {}
  vresizer.addEventListener("mousedown", e => {
    e.preventDefault();
    document.body.classList.add("resizing");
    const top = filtersCard.getBoundingClientRect().top;
    // Keep room below the card for the scenario list, or for the topic list in filters mode.
    const host = state.mode === "filters" ? container.parentElement : container;
    const max = host.getBoundingClientRect().bottom - top - 160;
    const move = ev => {
      filtersCard.style.height = Math.min(Math.max(100, ev.clientY - top), max) + "px";
      filtersCard.style.maxHeight = "none";
    };
    const up = () => {
      document.body.classList.remove("resizing");
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
      try { localStorage.setItem("filtersHeight", filtersCard.style.height); } catch (e) {}
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  });

  const matching = (filters = state.filters) => scenarios.filter(sc =>
    dimensions.every(d => !filters[d.key] || sc.dims[d.key] === filters[d.key]));
  const pickedScenarios = () => scenarios.filter(sc => state.picked.has(sc.key));

  function emit() {
    opts.onChange({ mode: state.mode, scenario: state.scenario, scenarios: pickedScenarios(),
                    scenarioA: state.scenarioA, scenarioB: state.scenarioB,
                    filters: { ...state.filters }, matching: matching() });
  }

  function announceFilters() {
    if (opts.onFilterChange) opts.onFilterChange({ ...state.filters });
  }

  // In filters mode the filters are the selection, so a change is announced.
  function filtersChanged() {
    render();
    if (state.mode === "filters") emit();
  }

  function onFilter(key, value) {
    state.filters[key] = value;
    announceFilters();
    if (state.mode === "single" && state.scenario) {
      const matches = matching();
      if (!matches.includes(state.scenario)) {
        state.scenario = matches[0] || null;
        render();
        emit();
        return;
      }
    }
    filtersChanged();
  }

  function onScenarioClick(sc) {
    if (state.mode === "single") state.scenario = sc;
    else if (state.mode === "split") {
      state["scenario" + state.armed] = sc;
      // Auto-advance to the still-empty slot, so two plain clicks fill A then B.
      const other = state.armed === "A" ? "B" : "A";
      if (!state["scenario" + other]) state.armed = other;
    } else if (state.picked.has(sc.key)) state.picked.delete(sc.key);
    else state.picked.add(sc.key);
    render();
    emit();
  }

  function renderFilters() {
    filtersBox.replaceChildren();
    for (const d of dimensions) {
      const row = document.createElement("div");
      row.className = "dim";
      const lab = document.createElement("div");
      lab.className = "dimlabel";
      lab.textContent = d.label;
      if (d.help) lab.appendChild(helpIcon(d.help));
      row.appendChild(lab);
      const pills = document.createElement("div");
      pills.className = "pills";
      const mk = (label, value) => {
        const b = document.createElement("button");
        b.textContent = label;
        b.classList.toggle("active", state.filters[d.key] === value);
        if (value !== null) {
          // Grey out values with no scenarios under the other dimensions' selections.
          const trial = { ...state.filters, [d.key]: value };
          b.disabled = matching(trial).length === 0;
        }
        b.onclick = () => onFilter(d.key, value);
        pills.appendChild(b);
      };
      mk("All", null);
      for (const v of d.values) mk(v, v);
      row.appendChild(pills);
      filtersBox.appendChild(row);
    }
  }

  function renderCount(matches) {
    countLine.replaceChildren(matches.length + " of " + scenarios.length);
    const link = (text, title, onclick) => {
      const a = document.createElement("a");
      a.textContent = text;
      a.title = title;
      a.onclick = onclick;
      countLine.append(" · ", a);
    };
    if (state.mode === "multi") {
      countLine.append(" · " + state.picked.size + " picked");
      const unpicked = matches.filter(sc => !state.picked.has(sc.key));
      if (unpicked.length)
        link("select all", "Pick every scenario matching the current filters",
          () => { unpicked.forEach(sc => state.picked.add(sc.key)); render(); emit(); });
      if (state.picked.size) link("clear", "Unpick every scenario", () => { state.picked.clear(); render(); emit(); });
    } else if (state.mode === "split") {
      if (state.scenarioA) link("clear A", "Unpick scenario A", () => { state.scenarioA = null; state.armed = "A"; render(); emit(); });
      if (state.scenarioB) link("clear B", "Unpick scenario B", () => { state.scenarioB = null; state.armed = "B"; render(); emit(); });
    } else if (state.scenario) {
      link("clear", "Unpick the scenario", () => { state.scenario = null; render(); emit(); });
    }
  }

  function renderList(matches) {
    listBox.replaceChildren();
    if (!matches.length) {
      const d = document.createElement("div");
      d.className = "empty";
      d.textContent = "No scenarios match the current filters.";
      listBox.appendChild(d);
      return;
    }
    for (const sc of matches) {
      const b = document.createElement("button");
      const tag = document.createElement("span");
      tag.className = "scn";
      tag.textContent = scenarioTag(sc);
      const name = document.createElement("span");
      name.className = "scn-name";
      name.textContent = scenarioLabel(set, sc);
      b.append(tag, name);
      b.title = sc.runs.map(r => r.dir).join("\n");
      if (state.mode === "single") b.classList.toggle("active", sc === state.scenario);
      else if (state.mode === "split") {
        const badge = document.createElement("span");
        badge.className = "ab-badge";
        badge.textContent = (sc === state.scenarioA ? "A" : "") + (sc === state.scenarioB ? "B" : "");
        b.appendChild(badge);
        b.classList.toggle("picked-a", sc === state.scenarioA);
        b.classList.toggle("picked-b", sc === state.scenarioB);
      } else b.classList.toggle("checked", state.picked.has(sc.key));
      b.onclick = () => onScenarioClick(sc);
      listBox.appendChild(b);
    }
  }

  function renderSummary() {
    summaryBox.replaceChildren();
    for (const d of dimensions) {
      const v = state.filters[d.key];
      if (v === null) continue;
      const b = document.createElement("button");
      b.className = "active";
      b.textContent = dimPair(d, v);
      b.title = "Clear filter";
      b.onclick = () => onFilter(d.key, null);
      summaryBox.appendChild(b);
    }
  }

  function render() {
    const matches = matching();
    container.classList.toggle("filters-only", state.mode === "filters");
    container.classList.toggle("split-mode", state.mode === "split");
    clearFilters.hidden = dimensions.every(d => state.filters[d.key] === null);
    // The split-view toggle belongs to Explore's single and split modes only.
    splitToggle.hidden = state.mode !== "single" && state.mode !== "split";
    splitCheck.checked = state.mode === "split";
    overviewLink.hidden = !opts.onOverview || state.mode === "filters"
      || (state.mode === "single" && !state.scenario) || (state.mode === "multi" && !state.picked.size);
    armA.classList.toggle("active", state.armed === "A");
    armB.classList.toggle("active", state.armed === "B");
    swapAB.disabled = !state.scenarioA && !state.scenarioB;
    renderFilters();
    renderSummary();
    renderCount(matches);
    renderList(matches);
  }

  render();
  return {
    scenarios,
    setMode(mode) {
      // Entering split view carries the single pick over as A.
      if (mode === "split" && state.mode !== "split") {
        if (state.scenario) state.scenarioA = state.scenario;
        state.armed = state.scenarioA && !state.scenarioB ? "B" : "A";
      }
      state.mode = mode;
      render();
    },
    setSplit(a, b) {
      state.scenarioA = a;
      state.scenarioB = b;
      state.armed = a && !b ? "B" : "A";
      render();
    },
    setScenario(sc) { state.scenario = sc; render(); },
    setPicked(scs) { state.picked = new Set(scs.map(sc => sc.key)); render(); },
    setFilters(filters) {
      for (const d of dimensions) state.filters[d.key] = d.values.includes(filters[d.key]) ? filters[d.key] : null;
      render();
    },
    getScenario: () => state.scenario,
    getScenarios: pickedScenarios,
    getScenarioA: () => state.scenarioA,
    getScenarioB: () => state.scenarioB,
    getFilters: () => ({ ...state.filters }),
    getMatching: () => matching(),
  };
}
