// Loads every pane and every plot page of the first run with all requests outside the viewer's origin blocked;
// fails on a blocked request, a page error or a console error. Usage: node check_viewer_offline.mjs URL

let chromium;
try { ({ chromium } = await import("playwright")); }
catch (e) {
  console.error("Playwright is not installed here: npm install --no-save playwright && npx playwright install chromium");
  process.exit(2);
}

const url = process.argv.find(a => a.startsWith("http"));
if (!url) {
  console.error("usage: node check_viewer_offline.mjs URL   (the URL steelo-viewer prints)");
  process.exit(2);
}
const origin = new URL(url).origin;
const problems = [];

const browser = await chromium.launch({ executablePath: process.env.TOUR_BROWSER || undefined });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
await ctx.addInitScript(() => localStorage.setItem("tourSeen", "1"));
// data: and blob: URLs never reach the network.
await ctx.route("**/*", route => {
  const u = route.request().url();
  if (u.startsWith(origin + "/") || u.startsWith("data:") || u.startsWith("blob:")) return route.continue();
  problems.push("blocked: " + u);
  return route.abort("blockedbyclient");
});

async function open(hash) {
  const page = await ctx.newPage();
  page.on("pageerror", e => problems.push((hash || "#explore") + " page error: " + e.message));
  page.on("console", m => { if (m.type() === "error") problems.push((hash || "#explore") + " console error: " + m.text()); });
  await page.goto(url + hash, { waitUntil: "networkidle" });
  return page;
}

// Resolves on the Explore iframe's next load event, after a click on the plot tab `label` (none: the first run's row).
const frameLoad = (page, label) => page.evaluate(label => new Promise((resolve, reject) => {
  document.querySelector("#explore-pane iframe").addEventListener("load", resolve, { once: true });
  setTimeout(() => reject(new Error("no iframe load for " + (label || "the first run") + " within 120 s")), 120000);
  (label ? [...document.querySelectorAll("#explore-pane header button")].find(b => b.textContent === label)
    : document.querySelector("#explore-pane .rt-row")).click();
}), label);

// Explore: the first run from the run table, then every one of its plot pages.
{
  const page = await open("");
  const { run, plots } = await page.evaluate(() => ({ run: manifest.sets[0].runs[0], plots: manifest.sets[0].plots }));
  await frameLoad(page, null);
  const shown = await page.evaluate(() => viewer.getPlot());
  for (const file of run.plots.filter(f => f !== shown)) await frameLoad(page, plots.find(p => p.file === file).label);
  console.log("Explore: " + run.dir + ", " + run.plots.length + " plot pages loaded");
  await page.close();
}

// Compare and Annual costs: two runs ticked in the run table, then Show.
for (const hash of ["#compare", "#annual-costs"]) {
  const page = await open(hash);
  const pane = hash.slice(1) + "-pane";
  const rows = page.locator("#" + pane + " .rt-row");
  for (let i = 0; i < Math.min(2, await rows.count()); i++) await rows.nth(i).click();
  await page.click("#" + pane + " .rt-show");
  await page.waitForSelector("#" + pane + " .compare-chart .main-svg", { timeout: 60000 });
  await page.waitForLoadState("networkidle");
  console.log(hash + ": " + await page.$eval("#" + pane + " .compare-title", e => e.textContent));
  await page.close();
}

// View data: the first run's settings as the filters, then the first topic.
{
  const page = await open("#data");
  await page.locator("#data-pane .rt-row").first().click();
  await page.locator("#topics .runs button").first().click();
  await page.waitForLoadState("networkidle");
  console.log("#data: " + await page.$eval("#data-pane .compare-title", e => e.textContent));
  await page.close();
}

// Download results: the first run's folders, then the first table's first page.
{
  const page = await open("#results");
  await page.locator("#results-pane .rt-row").first().click();
  await page.locator("#results-pane .results-button:text-is('View')").first().click();
  await page.waitForSelector("#results-pane .results-table tbody tr", { timeout: 60000 });
  console.log("#results: " + await page.$eval("#results-pane .results-view-title", e => e.textContent));
  await page.close();
}

await browser.close();
if (problems.length) { console.log("\n" + problems.length + " problem(s):\n  " + problems.join("\n  ")); process.exit(1); }
console.log("\nOffline OK");
