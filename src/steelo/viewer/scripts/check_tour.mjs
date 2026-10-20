// Walks the guided tour against a running viewer; fails on a missing target, a card off screen, a page error or a
// view not restored. Usage: node check_tour.mjs URL [--print], URL from `steelo-viewer --no-browser --port 8080`.

let chromium;
try { ({ chromium } = await import("playwright")); }
catch (e) {
  console.error("Playwright is not installed here: npm install --no-save playwright && npx playwright install chromium");
  process.exit(2);
}

const url = process.argv.find(a => a.startsWith("http"));
if (!url) {
  console.error("usage: node check_tour.mjs URL [--print]   (the URL steelo-viewer prints)");
  process.exit(2);
}
const print = process.argv.includes("--print");
const failures = [];
const fail = (where, what) => { failures.push(where + ": " + what); console.log("  FAIL " + where + ": " + what); };

const browser = await chromium.launch({ executablePath: process.env.TOUR_BROWSER || undefined });

async function newPage(storage) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  if (storage) await ctx.addInitScript(s => { for (const [k, v] of Object.entries(s)) localStorage.setItem(k, v); }, storage);
  const page = await ctx.newPage();
  page.on("pageerror", e => fail("page", "error: " + e.message));
  page.on("console", m => { if (m.type() === "error") fail("page", "console error: " + m.text()); });
  return page;
}

const stepInfo = page => page.evaluate(() => {
  const card = document.querySelector(".tour-card"), spot = document.querySelector(".tour-spot");
  if (!card) return null;
  const r = card.getBoundingClientRect();
  return {
    n: +card.querySelector(".tour-count").textContent.split("/")[0],
    title: card.querySelector(".tour-title").textContent,
    text: card.querySelector(".tour-body").innerText,
    spot: !spot.hidden,
    onScreen: r.left >= 0 && r.top >= 0 && r.right <= innerWidth && r.bottom <= innerHeight,
    // The active tab's own label, without its (?) help icon.
    mode: document.querySelector(".modetabs .active")?.firstChild.textContent,
    hash: location.hash,
  };
});

// Next, then wait for the card to show another step (an optional step may be skipped).
async function next(page, from) {
  await page.click(".tour-next");
  await page.waitForFunction(n => {
    const c = document.querySelector(".tour-card");
    return !c || +c.querySelector(".tour-count").textContent.split("/")[0] !== n;
  }, from);
  await page.waitForTimeout(400);   // data-driven rows and the spotlight's transition settle
}

const pageState = page => page.evaluate(() => ({
  hash: location.hash, mode: document.querySelector(".modetabs .active")?.firstChild.textContent,
  sidebar: document.body.classList.contains("sidebar-collapsed"),
  filters: document.querySelector("#picker").classList.contains("filters-collapsed"),
  plotbar: document.querySelector("#explore-pane").classList.contains("bar-collapsed"),
  tour: !!document.querySelector(".tour-card"),
}));

// Every step from the open one to the end renders, with its target when it has one.
async function walk(page) {
  const steps = await page.evaluate(() => TOUR_STEPS.map(s => ({ title: s.title, target: !!s.target, optional: !!s.optional })));
  const seen = new Set();
  for (let guard = 0; guard < steps.length + 1 && await page.$(".tour-card"); guard++) {
    const info = await stepInfo(page);
    const s = steps[info.n - 1];
    seen.add(info.n);
    const where = "step " + info.n + " (" + info.title + ")";
    if (s.target && !s.optional && !info.spot) fail(where, "its target element was not found or is hidden");
    if (!info.onScreen) fail(where, "the card is off screen");
    if (!info.text.trim()) fail(where, "empty text");
    console.log("  " + info.n + ". " + info.title + " · " + (info.mode || "page") + (info.spot ? "" : " · no spotlight"));
    if (print) console.log("     " + info.text.replace(/\n+/g, "\n     "));
    await next(page, info.n);
  }
  for (let i = 1; i <= steps.length; i++)
    if (!seen.has(i) && !steps[i - 1].optional) fail("step " + i + " (" + steps[i - 1].title + ")", "never shown");
}

// 1. A first visit: the tour starts by itself and every step renders.
console.log("Walk-through on a first visit");
{
  const page = await newPage();
  await page.goto(url, { waitUntil: "networkidle" });
  if (!await page.$(".tour-card")) fail("start", "the tour did not start on a first visit");
  await walk(page);
  const after = await pageState(page);
  if (after.tour) fail("finish", "the tour is still open");
  if (after.mode !== "Explore" || after.hash !== "") fail("finish", "the view was not restored: " + after.mode + " " + after.hash);
  if (await page.evaluate(() => localStorage.getItem("tourSeen")) !== "1") fail("finish", "the first visit was not remembered");
  await page.reload({ waitUntil: "networkidle" });
  if (await page.$(".tour-card")) fail("reload", "the tour started again on a second visit");
  await page.context().close();
}

// 2. Everything folded and a run open: the tour unfolds what it needs, and Skip folds it back and returns to the run.
console.log("Folded panels and an open run");
{
  const page = await newPage({ tourSeen: "1", sidebarCollapsed: "1", filtersCollapsed: "1", plotbarCollapsed: "1" });
  await page.goto(url, { waitUntil: "networkidle" });
  // Opened by URL, since the folded sidebar cannot be clicked.
  await page.evaluate(() => { const s = manifest.sets[0], r = s.runs[0]; location.hash = s.id + "/" + r.dir + "/" + r.plots[0]; });
  await page.waitForTimeout(300);
  const before = await pageState(page);
  if (!before.hash) fail("setup", "no run opened");
  await page.click("#tour-link");
  await page.waitForTimeout(300);
  for (let n = 1; n < 20; n++) {
    if (await page.evaluate(() => document.querySelector(".tour-title").textContent) === "Explore: the plot pages") break;
    await next(page, n);
  }
  const mid = await pageState(page);
  if (mid.sidebar || mid.filters || mid.plotbar) fail("unfold", "still folded at the Explore step: " + JSON.stringify(mid));
  await page.click(".tour-skip");
  await page.waitForTimeout(500);
  const after = await pageState(page);
  if (after.tour) fail("skip", "the tour is still open");
  if (!after.sidebar || !after.filters || !after.plotbar) fail("skip", "not folded back: " + JSON.stringify(after));
  if (after.hash !== before.hash || after.mode !== before.mode) fail("skip", "the run was not restored: " + after.hash);
  await page.context().close();
}

await browser.close();
if (failures.length) { console.log("\n" + failures.length + " problem(s):\n  " + failures.join("\n  ")); process.exit(1); }
console.log("\nTour OK");
