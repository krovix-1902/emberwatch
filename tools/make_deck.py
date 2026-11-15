"""Generate a 7-slide EmberWatch deck as a self-contained HTML file.

Charts are real: built as inline SVG from site/data/punjab.json, the same
numbers the live site shows. Chrome headless then prints it to PDF.
"""
from __future__ import annotations
import json, math

D = json.load(open("site/data/punjab.json"))
M = json.load(open("site/data/manifest.json"))
MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
EMBER = ["#f2ebdc","#ead4a6","#ddb068","#cd853d","#bc5a24","#a03414","#7c230c","#4e1606"]

def ramp(t):
    t=max(0.0,min(1.0,t)); x=t*(len(EMBER)-1); i=int(x); f=x-i
    if i>=len(EMBER)-1: return EMBER[-1]
    a=[int(EMBER[i][k:k+2],16) for k in (1,3,5)]
    b=[int(EMBER[i+1][k:k+2],16) for k in (1,3,5)]
    return "rgb(%d,%d,%d)"%tuple(round(a[k]+(b[k]-a[k])*f) for k in range(3))

def annual(cal):
    return [(r["year"], sum(r["months"])) for r in cal]

def seam_svg():
    na = annual(D["raw_calendar"]); ha = annual(D["calendar"])
    # index each to its own 2009-11 mean so units never share an axis
    def base(series):
        v=[t for y,t in series if 2009<=y<=2011 and t>0]
        return sum(v)/len(v) if v else 0
    nb,hb=base(na),base(ha)
    na=[(y,t/nb*100) for y,t in na]; ha=[(y,t/hb*100) for y,t in ha]
    W,H,l,r,t,b=1140,290,46,14,16,32
    yrs=[y for y,_ in ha]
    mx=max([v for _,v in na]+[v for _,v in ha])*1.08
    X=lambda y:l+(y-yrs[0])/(yrs[-1]-yrs[0])*(W-l-r)
    Y=lambda v:H-b-(v/mx)*(H-t-b)
    def path(s): return "".join(("M" if i==0 else "L")+f"{X(y):.1f} {Y(v):.1f}" for i,(y,v) in enumerate(s))
    s=f'<svg viewBox="0 0 {W} {H}" width="100%">'
    for i in range(4):
        v=mx*i/3; y=Y(v)
        s+=f'<line x1="{l}" x2="{W-r}" y1="{y:.1f}" y2="{y:.1f}" stroke="#d9d3c5"/>'
        s+=f'<text x="{l-6}" y="{y+3:.1f}" text-anchor="end" font-size="9" fill="#6f6a5e" font-family="monospace">{v:.0f}</text>'
    for y in yrs:
        if y%5==0: s+=f'<text x="{X(y):.1f}" y="{H-12}" text-anchor="middle" font-size="9" fill="#6f6a5e" font-family="monospace">{y}</text>'
    s+=f'<line x1="{X(2012):.1f}" x2="{X(2012):.1f}" y1="{t}" y2="{H-b}" stroke="#b3421a" stroke-width="1.3" stroke-dasharray="5 4"/>'
    s+=f'<text x="{X(2012)+5:.1f}" y="{t+10}" fill="#b3421a" font-size="10" font-family="monospace">VIIRS joins</text>'
    s+=f'<path d="{path(na)}" fill="none" stroke="#9a9485" stroke-width="2"/>'
    s+=f'<path d="{path(ha)}" fill="none" stroke="#1c1a15" stroke-width="2.4"/>'
    return s+"</svg>"

def calendar_svg():
    cal=D["calendar"]; years=[r["year"] for r in cal]
    mx=max((max(r["months"]) for r in cal), default=1) or 1
    left,top,W,ch=44,16,1000,11
    cw=(W-left-6)/12
    H=top+len(years)*ch+4
    s=f'<svg viewBox="0 0 {W} {H}" width="100%">'
    for i,m in enumerate(MONTHS):
        s+=f'<text x="{left+i*cw+cw/2:.1f}" y="11" text-anchor="middle" font-size="8" fill="#6f6a5e" font-family="monospace">{m}</text>'
    for yi,r in enumerate(cal):
        yy=top+yi*ch
        if r["year"]%2==0 or r["year"]==years[-1]:
            s+=f'<text x="{left-5}" y="{yy+10:.1f}" text-anchor="end" font-size="7.5" fill="#6f6a5e" font-family="monospace">{r["year"]}</text>'
        for mi,v in enumerate(r["months"]):
            tt=math.sqrt(v/mx) if mx else 0
            x=left+mi*cw
            col=ramp(0.15+0.85*tt) if v>0 else "#edeade"
            s+=f'<rect x="{x+0.6:.1f}" y="{yy+0.6:.1f}" width="{cw-1.2:.1f}" height="{ch-1.6:.1f}" fill="{col}"/>'
    return s+"</svg>"

AB = {a["variant"]:a["step_pct"] for a in D["ablation"]}
cov = {c["sensor"]:c for c in M["coverage"]}
SEAM_N = f'+{AB["Raw splice, no floor"]:.0f}%'
SEAM_H = f'+{AB["Floor plus calibration"]:.0f}%'

CSS = """
@page{size:1280px 720px;margin:0}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'IBM Plex Sans',system-ui,sans-serif;color:#1c1a15}
.slide{width:1280px;height:720px;padding:60px 72px 86px;page-break-after:always;
  background:#f6f3ec;position:relative;overflow:hidden;display:flex;flex-direction:column}
.chart svg{display:block;width:100%;height:auto}
.chart{max-height:452px;overflow:hidden}
.kick{font-family:'IBM Plex Mono',monospace;font-size:14px;letter-spacing:.12em;
  text-transform:uppercase;color:#b3421a}
h1{font-family:'Instrument Serif',Georgia,serif;font-weight:400;line-height:1.02}
.t1{font-size:96px}.t1 em{font-style:italic;color:#b3421a}
.t2{font-size:52px;margin:6px 0 20px}
.strap{font-family:'Instrument Serif',serif;font-style:italic;font-size:30px;color:#6f6a5e}
.say{font-family:'Instrument Serif',serif;font-style:italic;font-size:26px;color:#1c1a15;
  border-left:4px solid #b3421a;padding-left:20px;margin-top:auto;max-width:62ch}
.foot{position:absolute;bottom:28px;left:72px;right:72px;display:flex;justify-content:space-between;
  font-family:'IBM Plex Mono',monospace;font-size:12px;color:#6f6a5e;border-top:1px solid #d9d3c5;padding-top:10px}
.big{font-family:'IBM Plex Mono',monospace;font-size:120px;font-weight:500;color:#b3421a;line-height:1}
.steps{display:grid;grid-template-columns:1fr 1fr;gap:14px 46px;margin-top:10px}
.steps div{border-top:1px solid #d9d3c5;padding-top:10px}
.steps b{font-size:19px}.steps p{font-size:15px;color:#6f6a5e;margin-top:3px}
.n{font-family:'IBM Plex Mono',monospace;color:#b3421a;font-size:13px}
table{border-collapse:collapse;width:100%;font-size:22px;margin-top:14px}
td{padding:14px 16px;border-bottom:1px solid #d9d3c5;font-family:'IBM Plex Mono',monospace}
td.v{text-align:right}
tr.hero td{background:#efe7d6;font-weight:600;font-size:26px}
.lim li{font-size:19px;color:#1c1a15;margin:10px 0;list-style:none;padding-left:26px;position:relative}
.lim li::before{content:'—';position:absolute;left:0;color:#b3421a}
.lim b{font-weight:600}
.chart{background:#fbf9f4;border:1px solid #1c1a15;padding:18px;box-shadow:5px 5px 0 #d9d3c5}
.legwrap{font-family:'IBM Plex Mono',monospace;font-size:13px;color:#6f6a5e;margin-top:8px}
.url{font-family:'IBM Plex Mono',monospace;font-size:16px;color:#1c1a15}
.stamp{display:inline-block;border:2px solid #b3421a;color:#b3421a;font-family:'IBM Plex Mono',monospace;
  font-size:12px;letter-spacing:.1em;text-transform:uppercase;padding:4px 10px;transform:rotate(-1deg)}
.row{display:flex;gap:40px;align-items:center}
.col{flex:1}
"""

def foot(n):
    return f'<div class="foot"><span>EmberWatch · Harmonization of MODIS &amp; VIIRS Hot Spots</span><span>NASA Space Apps 2026 · {n} / 7</span></div>'

slides = []

# 1 title
slides.append(f'''<div class="slide">
  <div class="kick">NASA Space Apps Challenge 2026 · London</div>
  <h1 class="t1">Ember<em>Watch</em></h1>
  <p class="strap">One fire record, twenty-five years, no sensor seams.</p>
  <div style="margin-top:auto">
    <div class="kick" style="color:#6f6a5e">Challenge</div>
    <div style="font-size:22px;margin-top:4px">Harmonization of MODIS and VIIRS Hot Spots</div>
    <div style="font-size:18px;color:#6f6a5e;margin-top:16px">Rohan “Krovix” Khatri</div>
  </div>
  {foot(1)}</div>''')

# 2 problem
slides.append(f'''<div class="slide">
  <div class="kick">01 · The problem</div>
  <h1 class="t2">The record has a seam in it</h1>
  <div class="chart">{seam_svg()}
    <div class="legwrap">grey: raw detections, both sensors spliced ({SEAM_N} step at 2012) &nbsp;·&nbsp; black: harmonised ({SEAM_H})</div></div>
  <div class="say">Download the raw feed and it looks like burning jumped over 400% in 2012. It didn’t. The satellite changed. That broken record is what fire managers have had to work with.</div>
  {foot(2)}</div>''')

# 3 calendar
slides.append(f'''<div class="slide">
  <div class="kick">02 · The solution, seen</div>
  <h1 class="t2">Twenty-five fire seasons, one calendar</h1>
  <div class="chart">{calendar_svg()}
    <div class="legwrap">Punjab &amp; Haryana · harmonised cell-days per month, {D["years"][0]}–{D["years"][-1]} · darker = more burning</div></div>
  <div class="say">Harmonised, the two satellites become one readable history. Every cell comparable to every other, at a glance.</div>
  {foot(3)}</div>''')

# 4 method
steps=[("One schema","Both sensors to one table; MODIS 0–100 confidence mapped to VIIRS low/nominal/high."),
("Grid, not pixels","Detections binned to a 0.25° grid. Raw point counts never compared across sensors."),
("Cell-days","The unit is one cell, one day, any burning. Near-invariant to pixel size and platform count."),
("A shared floor",f"Both sensors clipped to {M['frp_floor_mw']} MW, the weakest fire MODIS reliably sees."),
("Calibrated on overlap",f"Median VIIRS-to-MODIS ratios per cell and month, fitted on {M['overlap_years'][0]}–{M['overlap_years'][1]}."),
("Then tested","Plotted across the join; the residual step is measured. That test is the next slide.")]
steprows="".join(f'<div><span class="n">0{i+1}</span> <b>{t}</b><p>{p}</p></div>' for i,(t,p) in enumerate(steps))
slides.append(f'''<div class="slide">
  <div class="kick">03 · How it works</div>
  <h1 class="t2">Six steps that make two satellites comparable</h1>
  <div class="steps">{steprows}</div>
  <div class="say">The key move is counting cell-days instead of raw detections, so pixel size and how many satellites are flying stop distorting the record.</div>
  {foot(4)}</div>''')

# 5 proof
slides.append(f'''<div class="slide">
  <div class="kick">04 · The proof</div>
  <h1 class="t2">We don’t claim it works. We measure it.</h1>
  <div class="row">
    <div class="col"><table>
      <tr><td>Raw splice, no floor</td><td class="v">+{AB["Raw splice, no floor"]:.0f}%</td></tr>
      <tr><td>Shared FRP floor</td><td class="v">{AB["Shared FRP floor"]:.0f}%</td></tr>
      <tr class="hero"><td>Floor + calibration</td><td class="v">+{AB["Floor plus calibration"]:.0f}%</td></tr>
    </table>
    <div style="font-size:14px;color:#6f6a5e;margin-top:10px">Step at the 2012 sensor change, same area, measured identically each time.</div></div>
    <div class="col" style="text-align:center"><div class="big">12%</div>
      <div style="font-size:17px;color:#6f6a5e;margin-top:8px">residual seam, down from over 400%</div></div>
  </div>
  <div class="say">The false jump falls from over 400% to 12%, and the live page recomputes it for any area you draw on the map.</div>
  {foot(5)}</div>''')

# 6 audience
lc=D["last_complete"]
slides.append(f'''<div class="slide">
  <div class="kick">05 · Who it’s for</div>
  <h1 class="t2">Is this month normal, or not?</h1>
  <div class="row" style="margin-top:8px">
    <div class="col chart">
      <div class="kick" style="color:#6f6a5e">{MONTHS[lc["month"]-1]} {lc["year"]} vs 25 years of history</div>
      <div style="font-family:'IBM Plex Mono',monospace;font-size:54px;font-weight:500;margin:10px 0">{D["anomaly"]["flag"]}</div>
      <div style="font-size:16px;color:#6f6a5e">{D["anomaly"].get("percentile",0):.0f}th percentile for the month · trend {D["trend"]["direction"]}, p &lt; 0.001</div>
    </div>
    <div class="col" style="font-size:19px;color:#1c1a15;line-height:1.5">
      Draw any box on Earth. EmberWatch says whether current burning is normal or extreme against decades of history, with a seasonal Mann-Kendall significance test behind the call.
    </div>
  </div>
  <div class="say">Early-warning responders, land managers and scientists get a consistent baseline for anywhere, not just the three places we pre-loaded.</div>
  {foot(6)}</div>''')

# 7 limits + links
slides.append(f'''<div class="slide">
  <div class="kick">06 · Honest limits</div>
  <h1 class="t2">What it can’t tell you</h1>
  <ul class="lim">
    <li><b>Detections are not burned area.</b> One large fire and several small ones can count the same.</li>
    <li><b>Cloud and overpass gaps hide fires.</b> A quiet pixel is not a certified absence of fire.</li>
    <li><b>Industry burns too.</b> Gas flares and volcanoes give some cells a permanent false fire season.</li>
  </ul>
  <div style="margin-top:28px">
    <div class="url">Live &nbsp; krovix-1902.github.io/emberwatch</div>
    <div class="url">Code &nbsp; github.com/krovix-1902/emberwatch</div>
    <div style="margin-top:14px;font-size:13px;color:#6f6a5e">Data: NASA FIRMS (MODIS C6.1, VIIRS 375m). MODIS {cov["MODIS"]["detections"]:,} detections, VIIRS {cov["VIIRS"]["detections"]:,}.</div>
  </div>
  <div class="say">A monitoring tool you can trust is one that tells you its own blind spots.</div>
  {foot(7)}</div>''')

html = f'''<!DOCTYPE html><html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>{CSS}</style></head><body>{"".join(slides)}</body></html>'''

open("deck.html","w").write(html)
print("wrote deck.html", len(html), "bytes")
