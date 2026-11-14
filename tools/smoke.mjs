/* Run the page in a real DOM (jsdom) against the generated JSON.
   Fails on runtime errors, empty renders, NaN/undefined leaks, and checks
   that a custom coordinate box recomputes everything. */
import { readFileSync } from "node:fs";
import { JSDOM } from "jsdom";

const html = readFileSync("site/index.html", "utf8");
let failed = false;
const fail = (m) => { failed = true; console.error("  FAIL " + m); };
const errors = [];

const dom = new JSDOM(html, {
  runScripts: "dangerously",
  url: "https://example.org/",
  beforeParse(window) {
    window.fetch = async (p) => ({
      json: async () => JSON.parse(readFileSync("site/" + p, "utf8")),
    });
    window.addEventListener("error", (e) => errors.push(e.message));
    // jsdom lacks these; the page only touches them on user gestures.
    window.SVGElement.prototype.setPointerCapture ??= () => {};
  },
});
const { document } = dom.window;
await new Promise((r) => setTimeout(r, 400));

const $ = (id) => document.getElementById(id);
const text = (id) => ($(id)?.textContent ?? "").trim();
const htmlOf = (id) => ($(id)?.innerHTML ?? "");

if (errors.length) fail("page errors: " + errors.join(" | "));

function checkRender(label) {
  console.log(`\n[${label}]`);
  const strip = htmlOf("strip"), cal = htmlOf("calendar"), seam = htmlOf("seam");
  if (!strip.includes("cell-stat")) fail("readout strip empty");
  const cells = (cal.match(/<rect/g) || []).length;
  if (cells < 24) fail(`calendar has only ${cells} cells`);
  if (!seam.includes("VIIRS joins")) fail("seam chart missing 2012 marker");
  for (const [id, s] of [["strip", strip], ["calendar", cal], ["seam", seam]]) {
    if (s.includes("undefined")) fail(`${id} contains 'undefined'`);
    if (s.includes("NaN")) fail(`${id} contains NaN`);
  }
  console.log("  strip   :", text("strip").replace(/\s+/g, " ").slice(0, 150));
  console.log("  calendar:", cells, "cells");
  console.log("  seam    :", (seam.match(/step [+-]?\d+%/g) || []).join("  "));
}

// 1. default area loaded at boot
if (!htmlOf("presets").includes("chip")) fail("presets never rendered");
if (!text("dl-built")) fail("dateline build date empty");
if (!htmlOf("map").includes("<path")) fail("map has no land paths");
if (!htmlOf("map").includes("data-i")) fail("map has no activity cells");
if (!htmlOf("ablation").includes("%")) fail("ablation table empty");
checkRender("default preset");

// 2. custom coordinates through the accessible path
$("in-w").value = "-9.0"; $("in-s").value = "39.0";
$("in-e").value = "-6.8"; $("in-n").value = "41.0";
$("apply").click();
await new Promise((r) => setTimeout(r, 50));
checkRender("custom box (Portugal coords)");
if (!text("selinfo").includes("grid cells")) fail("selection info not updated");

// 3. an empty ocean box renders the no-record state, not a crash
$("in-w").value = "-40"; $("in-s").value = "-30";
$("in-e").value = "-35"; $("in-n").value = "-25";
$("apply").click();
await new Promise((r) => setTimeout(r, 50));
if (!htmlOf("strip").toLowerCase().includes("no satellite-detected burning"))
  fail("empty-area state missing");
else console.log("\n[ocean box] empty state renders correctly");

// 4. percentile sanity straight from the data
const g = JSON.parse(readFileSync("site/data/grid.json", "utf8"));
if (!g.cells.length) fail("grid export has no cells");
if (g.n_months < 300) fail("grid export month axis looks short");

if (errors.length) fail("late page errors: " + errors.join(" | "));
console.log(failed ? "\nSMOKE TEST FAILED" : "\nsmoke test passed");
process.exit(failed ? 1 : 0);
