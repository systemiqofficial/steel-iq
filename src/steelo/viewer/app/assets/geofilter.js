"use strict";
/* Geography filter of the Compare and Annual costs panes: countries, geo units, trade blocs and regions. */

// Country order at the head of the dropdown, when present in the data.
const GEO_LEAD_COUNTRIES = ["CHN", "IND", "USA", "JPN", "KOR", "SAU", "ARE"];

function createGeoFilter(container, opts) {
  const geo = opts.geo || {};
  const blocs = geo.tradeBlocs || {};
  const info = geo.geoInfo || {};
  const unitNames = geo.geoUnitNames || {};
  const onChange = opts.onChange || (() => {});
  const noun = opts.noun || "countries";

  const isoOf = key => key.split(":")[0];
  const nameOf = iso3 => (info[iso3] || {}).country || "";
  const regionOf = iso3 => (info[iso3] || {}).region || "Unknown region";

  const checked = new Map();          // geo key -> bool, survives setGeoKeys
  let allKeys = [];
  let countries = [];
  let countryMembers = new Map();     // iso3 -> its geo keys (bare and/or units)
  const countryCbs = new Map(), unitCbs = new Map(), groupCbs = new Map();
  let regionCountries = new Map();    // region -> its countries in the data
  const expandedRegions = new Set();  // survives rebuilds, like the selection

  container.classList.add("geofilter");
  const lab = document.createElement("span");
  lab.className = "rowlabel";
  lab.textContent = opts.label || "Geography";
  if (!opts.label) lab.appendChild(helpIcon("Which countries the chart adds up.\n"
    + "For one country only: click None, then tick it in the countries or regions dropdown."));
  const allBtn = document.createElement("button");
  allBtn.className = "small";
  allBtn.textContent = "All";
  const noneBtn = document.createElement("button");
  noneBtn.className = "small";
  noneBtn.textContent = "None";
  const chips = document.createElement("span");
  chips.className = "geo-chips";

  function mkDropdown(noun) {
    const root = document.createElement("span");
    root.className = "dd";
    const toggle = document.createElement("button");
    toggle.className = "dd-toggle";
    const panel = document.createElement("div");
    panel.className = "dd-panel";
    panel.hidden = true;
    toggle.onclick = () => {
      const open = panel.hidden;
      for (const d of Object.values(dds)) d.panel.hidden = true;
      panel.hidden = !open;
      // Viewport coordinates, so a header shrunk to a scrolling strip cannot clip the panel.
      if (open) {
        const r = toggle.getBoundingClientRect();
        panel.style.position = "fixed";
        panel.style.left = r.left + "px";
        panel.style.top = (r.bottom + 4) + "px";
      }
    };
    root.append(toggle, panel);
    return { root, toggle, panel, noun };
  }
  const dds = {
    country: mkDropdown(noun),
    unit: mkDropdown("geo units"),
    bloc: mkDropdown("trade blocs"),
    region: mkDropdown("regions"),
  };
  container.append(lab, allBtn, noneBtn,
    ...Object.values(dds).map(d => d.root), chips);
  document.addEventListener("click", e => {
    if (!container.contains(e.target))
      for (const d of Object.values(dds)) d.panel.hidden = true;
  });

  const setCountry = (iso3, on) => countryMembers.get(iso3).forEach(k => checked.set(k, on));
  function countryState(iso3) {
    const members = countryMembers.get(iso3);
    const n = members.filter(k => checked.get(k)).length;
    return n === members.length ? 1 : n ? 0.5 : 0;
  }

  function addRow(panel, text, note, onToggle) {
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.onchange = () => { onToggle(cb.checked); update(); onChange(); };
    const label = document.createElement("label");
    label.append(cb, text);
    if (note) {
      const m = document.createElement("span");
      m.className = "muted";
      m.textContent = " " + note;
      label.appendChild(m);
    }
    panel.appendChild(label);
    return cb;
  }

  function rebuild() {
    for (const d of Object.values(dds)) d.panel.replaceChildren();
    countryCbs.clear();
    unitCbs.clear();
    groupCbs.clear();
    regionCountries = new Map();

    const addCountry = iso3 => countryCbs.set(iso3, addRow(dds.country.panel, iso3, nameOf(iso3),
      on => setCountry(iso3, on)));
    const lead = GEO_LEAD_COUNTRIES.filter(iso3 => countryMembers.has(iso3));
    const rest = countries.filter(iso3 => !lead.includes(iso3));
    lead.forEach(addCountry);
    if (lead.length && rest.length) {
      const div = document.createElement("div");
      div.className = "dd-sep";
      dds.country.panel.appendChild(div);
    }
    rest.forEach(addCountry);
    const units = allKeys.filter(k => k.includes(":"));
    for (const key of units)
      unitCbs.set(key, addRow(dds.unit.panel, key, unitNames[key] || "",
        on => checked.set(key, on)));
    dds.unit.root.hidden = !units.length;

    // Blocs and regions bulk-toggle the countries; only groups with a member in the data get a row.
    const addGroup = (kind, panel, name, members) => {
      const inData = members.filter(iso3 => countryMembers.has(iso3)).sort();
      if (!inData.length) return;
      groupCbs.set(kind + ":" + name, {
        members: inData,
        cb: addRow(panel, name, String(inData.length),
          on => inData.forEach(iso3 => setCountry(iso3, on))),
      });
    };
    for (const [name, members] of Object.entries(blocs))
      addGroup("bloc", dds.bloc.panel, name, members);
    dds.bloc.root.hidden = !dds.bloc.panel.childElementCount;
    const regions = regionCountries;
    for (const iso3 of countries) {
      const region = regionOf(iso3);
      if (!regions.has(region)) regions.set(region, []);
      regions.get(region).push(iso3);
    }
    for (const region of [...regions.keys()].sort())
      addGroup("region", dds.region.panel, region, regions.get(region));
    // A lone "Unknown region" group carries no information, so the dropdown goes.
    dds.region.root.hidden = regions.size === 1 && regions.has("Unknown region");
  }

  function addChip(text, uncheck) {
    const x = document.createElement("button");
    x.textContent = "×";
    x.title = "Remove";
    x.onclick = () => { uncheck(); update(); onChange(); };
    const chip = document.createElement("span");
    chip.className = "geo-chip";
    chip.append(text, x);
    chips.appendChild(chip);
    return chip;
  }

  function addRegionChip(region, members) {
    const expanded = expandedRegions.has(region);
    const flip = document.createElement("button");
    flip.textContent = expanded ? "▾" : "▸";
    flip.title = expanded ? "Collapse to the region" : "Expand into countries";
    flip.onclick = () => {
      if (expanded) expandedRegions.delete(region);
      else expandedRegions.add(region);
      update();
    };
    const count = document.createElement("span");
    count.className = "muted";
    count.textContent = String(members.length);
    const chip = addChip(region, () => members.forEach(iso3 => setCountry(iso3, false)));
    chip.classList.add("region");
    chip.prepend(flip);
    chip.insertBefore(count, chip.lastChild);
  }

  // Regions of two or more countries with every country fully selected.
  const fullRegions = () => [...regionCountries].sort()
    .filter(([, members]) => members.length > 1 && members.every(iso3 => countryState(iso3) === 1));

  function update() {
    for (const [iso3, cb] of countryCbs) {
      const s = countryState(iso3);
      cb.checked = s === 1;
      cb.indeterminate = s === 0.5;
    }
    for (const [key, cb] of unitCbs) cb.checked = !!checked.get(key);
    const counts = { bloc: [0, 0], region: [0, 0] };
    for (const [id, { cb, members }] of groupCbs) {
      const states = members.map(countryState);
      cb.checked = states.every(s => s === 1);
      cb.indeterminate = !cb.checked && states.some(s => s > 0);
      const kind = id.split(":")[0];
      counts[kind][1] += 1;
      if (cb.checked) counts[kind][0] += 1;
    }
    dds.country.toggle.textContent = countries.filter(c => countryState(c) > 0).length
      + " of " + countries.length + " " + noun + " ▾";
    dds.unit.toggle.textContent = [...unitCbs.values()].filter(cb => cb.checked).length
      + " of " + unitCbs.size + " geo units ▾";
    dds.bloc.toggle.textContent = counts.bloc[0] + " of " + counts.bloc[1] + " trade blocs ▾";
    dds.region.toggle.textContent = counts.region[0] + " of " + counts.region[1] + " regions ▾";

    chips.replaceChildren();
    const sel = selectedGeos();
    if (!sel.size || sel.size === allKeys.length) {
      const m = document.createElement("span");
      m.className = "muted";
      m.textContent = sel.size ? "all " + noun + " selected" : "no " + noun + " selected";
      chips.appendChild(m);
      return;
    }
    // A fully selected region collapses to one chip; expanded, its country chips follow it.
    const inRegionChip = new Set();
    for (const [region, members] of fullRegions()) {
      addRegionChip(region, members);
      if (expandedRegions.has(region)) members.forEach(iso3 => addChip(iso3, () => setCountry(iso3, false)));
      members.forEach(iso3 => inRegionChip.add(iso3));
    }
    // A fully selected multi-unit country collapses to one country chip.
    for (const iso3 of countries) {
      if (inRegionChip.has(iso3)) continue;
      const members = countryMembers.get(iso3);
      const selMembers = members.filter(k => checked.get(k));
      if (!selMembers.length) continue;
      if (selMembers.length === members.length) addChip(iso3, () => setCountry(iso3, false));
      else selMembers.forEach(k => addChip(k, () => checked.set(k, false)));
    }
  }

  allBtn.onclick = () => { allKeys.forEach(k => checked.set(k, true)); update(); onChange(); };
  noneBtn.onclick = () => { allKeys.forEach(k => checked.set(k, false)); update(); onChange(); };

  const selectedGeos = () => new Set(allKeys.filter(k => checked.get(k)));

  function note() {
    const sel = selectedGeos();
    if (sel.size === allKeys.length) return "all " + noun;
    const full = fullRegions();
    const inFull = new Set(full.flatMap(([, members]) => members));
    const rest = countries.filter(iso3 => !inFull.has(iso3) && countryState(iso3) > 0);
    if (!full.length) return rest.length + " of " + countries.length + " " + noun;
    const names = full.map(([region]) => region).join(" + ");
    if (!rest.length) return names;
    return names + " + " + (rest.length <= 3 ? rest.join(", ") : rest.length + " other " + noun);
  }

  return {
    setGeoKeys(keys) {
      allKeys = [...new Set(keys)].sort();
      allKeys.forEach(k => { if (!checked.has(k)) checked.set(k, true); });
      countryMembers = new Map();
      for (const k of allKeys) {
        const iso3 = isoOf(k);
        if (!countryMembers.has(iso3)) countryMembers.set(iso3, []);
        countryMembers.get(iso3).push(k);
      }
      countries = [...countryMembers.keys()].sort();
      rebuild();
      update();
    },
    selectedGeos,
    setSelected(keys) {
      const on = keys instanceof Set ? keys : new Set(keys);
      allKeys.forEach(k => checked.set(k, on.has(k)));
      update();
    },
    note,
  };
}
