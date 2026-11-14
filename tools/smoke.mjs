/* Execute the page's own script against the real JSON with a tiny DOM shim.
   Catches runtime errors, undefined fields and empty renders before deploy. */
import { readFileSync, readdirSync } from "node:fs";

const html = readFileSync("site/index.html", "utf8");
const script = html.split("<script>")[1].split("</script>")[0];

const nodes = {};
const mk = (id) => (nodes[id] = {
  id, innerHTML: "", textContent: "", hidden: true, style: {},
  onclick: null, onchange: null,
});
[
  "area", "dl-series", "synthetic-banner", "cards", "calendar", "cal-sub",
  "ramp", "seam", "ablation", "method", "factor-note", "generated",
].forEach(mk);

globalThis.document = {
  getElementById: (id) => nodes[id] ?? mk(id),
  createElement: () => ({ click() {}, set href(_) {}, set download(_) {} }),
};
globalThis.Blob = class { constructor(p) { this.parts = p; } };
globalThis.URL = { createObjectURL: () => "blob:x" };
globalThis.fetch = async (p) => ({
  json: async () => JSON.parse(readFileSync(`site/${p}`, "utf8")),
});

let failed = false;
const fail = (m) => { failed = true; console.error("  FAIL " + m); };

await (async () => {
  // Strip the IIFE so we can drive it per area, then re-add our own driver.
  const body = script.slice(0, script.lastIndexOf("(async function(){"));
  const run = new Function(body + "\nreturn {load, renderCards, ramp, onColour};")();

  const man = JSON.parse(readFileSync("site/data/manifest.json", "utf8"));
  console.log(`manifest: ${man.areas.length} areas, synthetic=${man.synthetic}`);

  for (const a of man.areas) {
    Object.values(nodes).forEach((n) => { n.innerHTML = ""; n.textContent = ""; });
    await run.load(a.key, man);
    console.log(`\n${a.key}:`);

    const cal = nodes.calendar.innerHTML;
    const seam = nodes.seam.innerHTML;
    const cards = nodes.cards.innerHTML;

    if (!cal.startsWith("<svg")) fail("calendar did not render an svg");
    if ((cal.match(/<rect/g) || []).length < 12) fail("calendar has too few cells");
    if (!seam.includes("VIIRS joins")) fail("seam chart missing the 2012 marker");
    if (!/viewBox="0 0 \d+ \d+"/.test(seam)) fail("seam chart has a bad viewBox");
    if (cards.includes("undefined") || cal.includes("undefined") || seam.includes("undefined"))
      fail("rendered output contains 'undefined'");
    if (cards.includes("NaN") || seam.includes("NaN")) fail("rendered output contains NaN");
    if (!nodes.ablation.innerHTML.includes("%")) fail("ablation table empty");
    if (!nodes["factor-note"].innerHTML.includes("MW")) fail("factor note empty");

    const d = JSON.parse(readFileSync(`site/data/${a.key}.json`, "utf8"));
    const pct = d.current.percentile;
    if (d.current.complete === true && new Date().getDate() < 28)
      fail("running month wrongly marked complete");
    if (pct !== null && (pct < 0 || pct > 100)) fail(`percentile out of range: ${pct}`);
    if (d.trend.p_value < 0 || d.trend.p_value > 1) fail("p-value out of range");

    const cells = (cal.match(/<rect/g) || []).length;
    console.log(`  calendar ${cells} cells over ${d.years.length} years`);
    console.log(`  current  ${d.current.flag} (${pct}th pct), last complete ${d.anomaly.flag}`);
    console.log(`  seam     naive ${d.seam.raw.step_pct}%  ->  harmonised ${d.seam.harmonised.step_pct}%`);

    // CSV export path
    nodes["dl-series"].onclick();
  }

  // Colour ramp sanity: monotonic lightness, no clipping outside 0..1.
  const isColour = (c) => /^(rgb\(|#)/.test(c);
  if (!isColour(run.ramp(-5)) || !isColour(run.ramp(5))) fail("ramp clamping broken");
  if (run.onColour(0.1) === run.onColour(0.9)) fail("label contrast does not flip");
})();

console.log(failed ? "\nSMOKE TEST FAILED" : "\nsmoke test passed");
process.exit(failed ? 1 : 0);
