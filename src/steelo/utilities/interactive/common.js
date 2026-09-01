/* Shared shell for the interactive viewers. Expects the globals CONFIG (chartTitle,
   runs, defaultRun, techColours, regionColours, tradeBlocs, geoInfo, geoUnitNames) and DATA
   (per-run payloads) to be defined first. A chart template calls

     Interactive.init({geoKeys, techs, onChange: render});

   which builds the run selector, the geography filter (countries, sub-national
   geo units, trade blocs, regions, selected entries as removable chips) and, when
   `techs` is given, a technology filter inside #shared-filters. render() then reads
   Interactive.selectedGeos() / selectedTechs() and the helpers below. */
const Interactive = (() => {
  const GEO_SEP = ":";
  const theme = {surface: "#fcfcfb", ink: "#0b0b0b", inkSecondary: "#52514e", grid: "#e3e1db"};
  const FALLBACK_COLOURS = ["#a52a2a", "#6b6b6b", "#b8860b", "#556b2f", "#708090", "#8b008b"];

  let allGeoKeys = [], onChange = () => {};
  const countryCbs = new Map(), unitCbs = new Map(), countryUnits = new Map();
  const techCbs = new Map();
  const groupCbs = new Map();  // "bloc:EU" / "region:Europe" -> {cb, members}

  /* ---- geography helpers ---- */
  function isoOf(geo) { return geo.split(GEO_SEP)[0]; }
  function infoOf(geo) { return CONFIG.geoInfo[isoOf(geo)] || {}; }
  function regionOf(geo) { return infoOf(geo).region || "Unknown region"; }
  function countryName(iso3) { return infoOf(iso3).country || ""; }
  function unitName(geo) { return CONFIG.geoUnitNames[geo] || ""; }
  function geoKeys() { return allGeoKeys.slice(); }

  function run() { return document.getElementById("run-select").value; }

  function selectedGeos() {
    const sel = new Set();
    allGeoKeys.forEach(geo => {
      const cb = geo.includes(GEO_SEP) ? unitCbs.get(geo) : countryCbs.get(geo);
      if (cb.checked) sel.add(geo);
    });
    return sel;
  }

  function selectedTechs() {
    return new Set([...techCbs].filter(([, cb]) => cb.checked).map(([tech]) => tech));
  }

  /* "all countries", "20 of 84 countries" or, for a single-country run with
     units, "3 of 7 geo units" — given the geo keys present in the current run. */
  function geoNote(runGeos) {
    const selected = selectedGeos();
    const selRun = [...runGeos].filter(g => selected.has(g));
    if (selRun.length === runGeos.size) return "all countries";
    const runCountries = new Set([...runGeos].map(isoOf));
    if (runCountries.size > 1) return `${new Set(selRun.map(isoOf)).size} of ${runCountries.size} countries`;
    return `${selRun.length} of ${runGeos.size} geo units`;
  }

  /* ---- colour helpers ---- */
  function hexToRgba(hex, alpha) {
    const r = parseInt(hex.slice(1, 3), 16), g = parseInt(hex.slice(3, 5), 16), b = parseInt(hex.slice(5, 7), 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }

  /* Blend towards white, as the static charts do for their lighter bands. */
  function lighten(hex, factor) {
    const ch = i => Math.round(parseInt(hex.slice(i, i + 2), 16) * (1 - factor) + 255 * factor)
      .toString(16).padStart(2, "0");
    return "#" + ch(1) + ch(3) + ch(5);
  }

  /* Fixed colour per series key: house colours, then a stable fallback for keys
     without one so a filter never repaints the survivors. */
  function colourTable(house, keys) {
    const unknown = keys.filter(k => !house[k]).sort();
    const table = {};
    keys.forEach(k => {
      table[k] = house[k] || FALLBACK_COLOURS[unknown.indexOf(k) % FALLBACK_COLOURS.length];
    });
    return table;
  }

  /* ---- chart chrome ---- */
  function summary(text) { document.getElementById("summary").textContent = text; }

  function showEmpty(gd, text, width, height) {
    Plotly.react(gd, [], {width, height, paper_bgcolor: theme.surface, plot_bgcolor: theme.surface,
      annotations: [{text, showarrow: false, font: {size: 16, color: theme.inkSecondary}}]});
  }

  /* ---- DOM building ---- */
  function el(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    Object.entries(attrs).forEach(([k, v]) => {
      if (k === "text") node.textContent = v;
      else if (k === "class") node.className = v;
      else node.setAttribute(k, v);
    });
    children.forEach(c => node.appendChild(c));
    return node;
  }

  function dropdown(id) {
    const toggle = el("button", {id: id + "-toggle"});
    const panel = el("div", {id: id + "-boxes", class: "box-panel"});
    return el("div", {id: id + "-dropdown", class: "box-dropdown"}, [toggle, panel]);
  }

  function buildShell(container, withTechs) {
    container.appendChild(el("div", {id: "run-panel"}, [
      el("label", {for: "run-select", text: "Run"}), el("select", {id: "run-select"})]));
    container.appendChild(el("div", {id: "geo-panel"}, [
      el("label", {}, [document.createTextNode("Geography"),
        el("button", {id: "geo-all", class: "small-btn", text: "All"}),
        el("button", {id: "geo-none", class: "small-btn", text: "None"})]),
      dropdown("country"), dropdown("unit"), dropdown("bloc"), dropdown("region"),
      el("div", {id: "geo-chips"})]));
    if (withTechs) {
      container.appendChild(el("div", {id: "tech-panel"}, [
        el("label", {}, [document.createTextNode("Technologies"),
          el("button", {id: "tech-all", class: "small-btn", text: "All"}),
          el("button", {id: "tech-none", class: "small-btn", text: "None"})]),
        dropdown("tech")]));
    }
  }

  function addBox(parent, value, text, cbs, onToggle, note) {
    const cb = el("input", {type: "checkbox", value});
    cb.checked = true;
    cb.addEventListener("change", () => { onToggle(cb); updateUi(); onChange(); });
    const label = el("label", {}, [cb, document.createTextNode(text)]);
    if (note) label.appendChild(el("span", {class: "muted", text: note}));
    parent.appendChild(label);
    cbs.set(value, cb);
    return cb;
  }

  /* ---- filter state ---- */
  function setCountry(iso3, checked) {
    countryCbs.get(iso3).checked = checked;
    if (countryUnits.has(iso3)) countryUnits.get(iso3).forEach(u => unitCbs.get(u).checked = checked);
  }

  function countryState(iso3) {
    const cb = countryCbs.get(iso3);
    return cb.checked ? 1 : (cb.indeterminate ? 0.5 : 0);
  }

  function updateToggle(id, n, total, noun) {
    const node = document.getElementById(id);
    if (node) node.textContent = `${n} of ${total} ${noun} ▾`;
  }

  /* Refresh derived states (countries from their units, groups from their
     countries), the dropdown-toggle summaries and the chips row of selected geos
     (a fully selected multi-unit country collapses to one country chip). */
  function updateUi() {
    for (const [iso3, units] of countryUnits) {
      const cb = countryCbs.get(iso3);
      const n = units.filter(u => unitCbs.get(u).checked).length;
      cb.checked = n === units.length;
      cb.indeterminate = n > 0 && n < units.length;
    }
    updateToggle("country-toggle", [...countryCbs.keys()].filter(iso3 => countryState(iso3) > 0).length,
      countryCbs.size, "countries");
    updateToggle("unit-toggle", [...unitCbs.values()].filter(cb => cb.checked).length, unitCbs.size, "geo units");
    const counts = {bloc: [0, 0], region: [0, 0]};
    for (const [id, {cb, members}] of groupCbs) {
      const states = members.map(countryState);
      cb.checked = states.every(s => s === 1);
      cb.indeterminate = !cb.checked && states.some(s => s > 0);
      const kind = id.split(":")[0];
      counts[kind][1] += 1;
      if (cb.checked) counts[kind][0] += 1;
    }
    updateToggle("bloc-toggle", ...counts.bloc, "trade blocs");
    updateToggle("region-toggle", ...counts.region, "regions");
    updateToggle("tech-toggle", [...techCbs.values()].filter(cb => cb.checked).length, techCbs.size, "technologies");

    const chips = document.getElementById("geo-chips");
    chips.innerHTML = "";
    const sel = selectedGeos();
    if (sel.size === allGeoKeys.length || !sel.size) {
      chips.appendChild(el("span", {class: "geo-chip-note",
        text: sel.size ? "all countries selected" : "no countries selected"}));
      return;
    }
    const addChip = (text, uncheck) => {
      const x = el("button", {text: "×", title: "Remove"});
      x.addEventListener("click", () => { uncheck(); updateUi(); onChange(); });
      chips.appendChild(el("span", {class: "geo-chip"}, [document.createTextNode(text), x]));
    };
    for (const [iso3, cb] of countryCbs) {
      if (countryUnits.has(iso3)) {
        const units = countryUnits.get(iso3);
        const selUnits = units.filter(u => unitCbs.get(u).checked);
        if (selUnits.length === units.length) addChip(iso3, () => setCountry(iso3, false));
        else selUnits.forEach(u => addChip(u, () => { unitCbs.get(u).checked = false; }));
      } else if (cb.checked) {
        addChip(iso3, () => { cb.checked = false; });
      }
    }
  }

  /* Build the shell. geoKeys: every geo key in DATA (ISO3 or ISO3:unit);
     techs: technologies for the optional technology filter; onChange: the
     chart's render(). */
  function init({geoKeys: keys, techs, onChange: callback, container = "shared-filters"}) {
    onChange = callback;
    document.title = CONFIG.chartTitle;
    buildShell(document.getElementById(container), Boolean(techs));

    const runSelect = document.getElementById("run-select");
    CONFIG.runs.forEach(key => runSelect.appendChild(el("option", {value: key, text: DATA[key].title})));
    runSelect.value = CONFIG.defaultRun || CONFIG.runs[0];
    runSelect.addEventListener("change", onChange);
    if (CONFIG.runs.length === 1) document.getElementById("run-panel").style.display = "none";

    allGeoKeys = [...new Set(keys)].sort();
    const units = allGeoKeys.filter(g => g.includes(GEO_SEP));
    units.forEach(geo => {
      const iso3 = isoOf(geo);
      if (!countryUnits.has(iso3)) countryUnits.set(iso3, []);
      countryUnits.get(iso3).push(geo);
    });
    const countries = [...new Set(allGeoKeys.map(isoOf))].sort();

    const countryBoxes = document.getElementById("country-boxes");
    countries.forEach(iso3 => addBox(countryBoxes, iso3, iso3, countryCbs,
      cb => setCountry(iso3, cb.checked), countryName(iso3) ? " " + countryName(iso3) : ""));
    const unitBoxes = document.getElementById("unit-boxes");
    units.forEach(geo => addBox(unitBoxes, geo, geo, unitCbs, () => {}, unitName(geo) ? " " + unitName(geo) : ""));
    if (!units.length) document.getElementById("unit-dropdown").style.display = "none";
    if (countries.length === 1 && units.length) document.getElementById("country-dropdown").style.display = "none";

    /* Trade blocs and regions are bulk toggles over the country checkboxes; only
       groups with a member present in the data get a checkbox. */
    const present = new Set(countries);
    const addGroup = (kind, parent, name, members) => {
      const inData = members.filter(iso3 => present.has(iso3)).sort();
      if (!inData.length) return;
      const cb = addBox(parent, `${kind}:${name}`, name, new Map(),
        box => inData.forEach(iso3 => setCountry(iso3, box.checked)), ` ${inData.length}`);
      groupCbs.set(`${kind}:${name}`, {cb, members: inData});
    };
    const blocBoxes = document.getElementById("bloc-boxes");
    Object.entries(CONFIG.tradeBlocs).forEach(([bloc, members]) => addGroup("bloc", blocBoxes, bloc, members));
    if (!blocBoxes.childElementCount) document.getElementById("bloc-dropdown").style.display = "none";
    const regionMembers = new Map();
    countries.forEach(iso3 => {
      const region = regionOf(iso3);
      if (!regionMembers.has(region)) regionMembers.set(region, []);
      regionMembers.get(region).push(iso3);
    });
    const regionBoxes = document.getElementById("region-boxes");
    [...regionMembers.keys()].sort().forEach(region => addGroup("region", regionBoxes, region, regionMembers.get(region)));

    if (techs) {
      const techBoxes = document.getElementById("tech-boxes");
      [...new Set(techs)].sort().forEach(tech => addBox(techBoxes, tech, tech, techCbs, () => {}));
    }

    const dropdowns = [...document.querySelectorAll(".box-dropdown")];
    dropdowns.forEach(dd => {
      const panel = dd.querySelector(".box-panel");
      dd.querySelector("button").addEventListener("click", () => {
        dropdowns.forEach(other => {
          const p = other.querySelector(".box-panel");
          if (p !== panel) p.classList.remove("open");
        });
        panel.classList.toggle("open");
      });
    });
    document.addEventListener("click", e => {
      if (!dropdowns.some(dd => dd.contains(e.target)))
        dropdowns.forEach(dd => dd.querySelector(".box-panel").classList.remove("open"));
    });
    const setAllGeo = checked => { countries.forEach(iso3 => setCountry(iso3, checked)); updateUi(); onChange(); };
    document.getElementById("geo-all").addEventListener("click", () => setAllGeo(true));
    document.getElementById("geo-none").addEventListener("click", () => setAllGeo(false));
    if (techs) {
      const setAllTech = checked => { techCbs.forEach(cb => cb.checked = checked); updateUi(); onChange(); };
      document.getElementById("tech-all").addEventListener("click", () => setAllTech(true));
      document.getElementById("tech-none").addEventListener("click", () => setAllTech(false));
    }
    updateUi();
  }

  return {init, run, geoKeys, selectedGeos, selectedTechs, geoNote, isoOf, regionOf, countryName, unitName,
          summary, showEmpty, hexToRgba, lighten, colourTable, theme, GEO_SEP};
})();
