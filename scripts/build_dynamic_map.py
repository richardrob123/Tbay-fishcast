"""Tier-1 dynamic cold-water map — zoomable + 5-day forecast, self-contained.

The insight (docs): the map = a FIXED high-res bathymetry × a TINY time-varying
isotherm-depth field. So we precompute, over a shoreline stretch:
  * one satellite basemap + one NONNA bathymetry patch (time-invariant),
  * a shore-distance field (time-invariant),
  * and for the nowcast + each forecast day, an isotherm-depth field interpolated
    from the LSOFS nodes in the box -> a reachable-cold-water overlay (green) with
    the 12 C line (red).
The self-contained page stacks the satellite base + the selected day's overlay and
lets you pan (drag) / zoom (wheel) and scrub the forecast (slider). No external
requests (fits the Artifact CSP); no LLM.

    python scripts/build_dynamic_map.py [issue YYYY-MM-DD]

Writes viz/dynamic_map.html. Bounded to the embedded stretch; the whole-coast slippy
version is Tier-2 (GitHub Pages + WebGL), see docs/DATA_AUDIT / the plan.
"""
from __future__ import annotations

import base64
import io
import math
import sys
from datetime import date
from pathlib import Path

import numpy as np
from scipy.interpolate import griddata

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.image as mpimg  # noqa: E402

from tbay_fishcast.config import load_config  # noqa: E402
from tbay_fishcast.features import bias_live, thermocline  # noqa: E402
from tbay_fishcast.features.overlay import (  # noqa: E402
    cold_reachable, fill_unsurveyed_water, imagery_unsurveyed_water, isobath_line_rgba,
    land_shore_distance, merc)
from tbay_fishcast.ingest import basemap, glsea, lsofs_grid, nonna  # noqa: E402
from tbay_fishcast.ingest.backfill import _open_first  # noqa: E402
from tbay_fishcast.ingest.lsofs_extract import extract_native_columns, valid_time_from_dataset  # noqa: E402
from tbay_fishcast.ingest.lsofs_paths import LsofsFile, candidate_urls  # noqa: E402

CENTER = (48.455, -89.175)     # harbour stretch incl. Marina Park + north shore
HALF_M = 4500.0
PX = 680
TARGET_C = 12.0
CAST_M = 75.0
LEADS = [("n", 6, 0)] + [("f", h, h) for h in (24, 48, 72, 96, 120)]
_R = 6378137.0


def _png_b64(rgb_or_rgba) -> str:
    buf = io.BytesIO()
    mpimg.imsave(buf, rgb_or_rgba, format="png")
    return base64.b64encode(buf.getvalue()).decode()


def main(argv) -> int:
    issue = date.fromisoformat(argv[1]) if len(argv) > 1 else date(2026, 8, 4)
    cfg = load_config()
    clat, clon = CENTER

    # --- fixed layers: bathymetry, shore-distance, satellite base ---
    patch = nonna.fetch_patch(clat, clon, half_m=HALF_M, scale_px=PX)
    res = nonna.ground_res_m(patch.res_mercator_m, clat)
    depth = fill_unsurveyed_water(patch.depth, res)   # drop mid-water NONNA survey gaps
    water = np.isfinite(depth)
    ny, nx = depth.shape
    x0, y0, x1, y1 = patch.bounds_3857
    # pixel-centre 3857 coords (row 0 = top = y1)
    xs = np.linspace(x0, x1, nx)
    ys = np.linspace(y1, y0, ny)
    gx, gy = np.meshgrid(xs, ys)
    sat = basemap.fetch_imagery_3857(patch.bounds_3857, size_px=PX)
    # the basemap is ground truth for land vs water: drop the unsurveyed no-data apron
    # so the shore-cast is measured from the true coast, not the offshore no-data edge.
    not_land = imagery_unsurveyed_water(depth, sat.mean(axis=2))
    dist, _land = land_shore_distance(depth, res, not_land=not_land)
    print(f"patch {depth.shape} res {res:.0f} m, water {patch.coverage_frac:.0%}")

    # --- LSOFS nodes in the box ---
    f0 = LsofsFile(issue, "t12z", "n", 6)
    ds0 = _open_first(candidate_urls(f0, cfg.lsofs.recent_bucket, cfg.lsofs.archive_bucket, byterange=False))
    grid = lsofs_grid.read_grid(ds0)
    ds0.close()
    lon = np.where(grid.lon > 180, grid.lon - 360, grid.lon)
    dlat = HALF_M / 111000.0
    dlon = HALF_M / (111000.0 * math.cos(math.radians(clat)))
    inbox = np.where((lon >= clon - dlon) & (lon <= clon + dlon)
                     & (grid.lat >= clat - dlat) & (grid.lat <= clat + dlat) & (grid.h >= 3.0))[0]
    node_xy = np.array([merc(grid.lat[n], lon[n]) for n in inbox])
    print(f"{len(inbox)} LSOFS nodes in box")

    central, lo, hi, _, n = bias_live.pooled_subsurface_bias(cfg, issue)
    # surface anchor with GLSEA-lag fallback (without it the isotherm collapses to
    # the surface — the whole shelf reads "cold" and the 12 C line disappears)
    _px = glsea.fetch_recent_sst(clat, clon, issue)
    g_sst = _px.sst_c if _px else None

    frames = []
    for kind, fh, lead in LEADS:
        f = LsofsFile(issue, "t12z", kind, fh)
        try:
            ds = _open_first(candidate_urls(f, cfg.lsofs.recent_bucket, cfg.lsofs.archive_bucket, byterange=False))
        except Exception:  # noqa: BLE001
            continue
        vt = valid_time_from_dataset(ds)
        node_map = {str(int(nd)): int(nd) for nd in inbox}
        # native sigma-layer profiles (no depth-binning loss for the isotherm)
        cols = extract_native_columns(ds, node_map)
        ds.close()
        iso_pts, iso_val = [], []
        for i, nd in enumerate(inbox):
            col = cols.get(str(int(nd)))
            if col is None or len(col.depths_m) < 4:
                continue
            depths = col.depths_m
            raw = col.temps_c
            surf_bias = (raw[0] - g_sst) if g_sst else 0.0
            bm = thermocline.BiasModel(surf_bias, central, lo, hi, n_buoys=n)
            zc = thermocline.isotherm_band(depths, raw, bm, TARGET_C)["central"]
            if zc is not None:
                iso_pts.append(node_xy[i]); iso_val.append(zc)
        if len(iso_pts) < 3:
            continue
        iso_pts = np.array(iso_pts)
        iso_field = griddata(iso_pts, iso_val, (gx, gy), method="linear")
        nanmask = ~np.isfinite(iso_field)
        if nanmask.any():
            iso_field[nanmask] = griddata(iso_pts, iso_val, (gx[nanmask], gy[nanmask]), method="nearest")
        _cold, reachable = cold_reachable(depth, iso_field, dist, cast_m=CAST_M)
        rgba = np.zeros((ny, nx, 4), dtype=np.uint8)
        rgba[reachable] = (57, 211, 83, 120)     # reachable cold water — green
        line = isobath_line_rgba(depth, iso_field, dist)   # 12 C line as a true contour
        lm = line[:, :, 3] > 40
        rgba[lm] = line[lm]                       # red isobath over the green band
        frames.append({"label": f"{vt:%a %b %-d}", "lead": lead,
                       "reach_ha": round(float(reachable.sum()) * res * res / 1e4, 1),
                       "png": _png_b64(rgba)})
        print(f"  +{lead:>3}h {vt:%b %-d}: {frames[-1]['reach_ha']} ha reachable")

    if not frames:
        print("no frames produced"); return 1
    html = _page(issue, _png_b64(sat), frames, res, g_sst)
    out = Path(__file__).resolve().parents[1] / "viz" / "dynamic_map.html"
    out.write_text(html)
    print(f"wrote {out} ({len(html)/1e6:.1f} MB)")
    return 0


def _page(issue, sat_b64, frames, res, g_sst):
    import json
    frames_js = json.dumps([{"label": f["label"], "ha": f["reach_ha"], "png": f["png"]} for f in frames])
    return f"""<title>Thunder Bay Cold-Water Map — dynamic</title>
<style>
  :root{{--bg:#0d1117;--ink:#e6edf3;--muted:#9aa4ae;--line:#2a2f37;--accent:#ff1e3c;--go:#39d353}}
  *{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}}
  .wrap{{max-width:1000px;margin:0 auto;padding:18px 16px 40px}}
  h1{{font-size:20px;margin:0 0 2px}} .sub{{color:var(--muted);font-size:13px;margin-bottom:12px}}
  .stage{{position:relative;width:100%;aspect-ratio:1/1;overflow:hidden;border-radius:12px;
    border:1px solid var(--line);background:#000;touch-action:none;cursor:grab}}
  .stage.drag{{cursor:grabbing}}
  .layer{{position:absolute;top:0;left:0;width:100%;height:100%;transform-origin:0 0;
    image-rendering:auto}}
  #ov{{image-rendering:pixelated}}
  .bar{{display:flex;align-items:center;gap:12px;margin:12px 0 6px}}
  input[type=range]{{flex:1;accent-color:var(--accent)}}
  .chip{{font-variant-numeric:tabular-nums;font-size:13px;min-width:150px}}
  .legend{{display:flex;flex-wrap:wrap;gap:16px;font-size:12px;color:var(--muted);margin-top:10px}}
  .sw{{display:inline-block;width:16px;height:10px;vertical-align:middle;margin-right:5px;border-radius:2px}}
  button{{background:#21262d;color:var(--ink);border:1px solid var(--line);border-radius:7px;
    padding:5px 10px;font-size:13px;cursor:pointer}}
  .note{{font-size:11.5px;color:var(--muted);margin-top:14px;line-height:1.5}}
</style>
<div class="wrap">
  <h1>Thunder Bay cold-water map — where the lakers are reachable</h1>
  <div class="sub">Issue {issue} · drag to pan, scroll/pinch to zoom, slide to scrub the 5-day
    forecast. Red = the 12&nbsp;°C line (cold water meets bottom); green = cold water within a
    {CAST_M:.0f}&nbsp;m cast. Bathymetry ~{res:.0f} m (CHS NONNA-10).</div>
  <div class="stage" id="stage">
    <img class="layer" id="base" alt="satellite"/>
    <img class="layer" id="ov" alt="reachable cold water"/>
  </div>
  <div class="bar">
    <button id="reset">Reset view</button>
    <input type="range" id="t" min="0" max="{len(frames)-1}" value="0" step="1"/>
    <span class="chip" id="lbl"></span>
  </div>
  <div class="legend">
    <span><span class="sw" style="background:#ff1e3c"></span>12&nbsp;°C line</span>
    <span><span class="sw" style="background:#39d353;opacity:.6"></span>reachable cold water</span>
    <span id="hint">surface {('%.1f°C' % g_sst) if g_sst else 'n/a'} · pooled correction applied</span>
  </div>
  <div class="note">Tier-1 view: a self-contained, embedded stretch of the harbour shore
    (Marina Park + north shore). Absolute reachability carries the same ±band as the
    per-spot maps; the forecast surface anchor is held from the issue day. The full-coast,
    stream-as-you-pan version is Tier-2 (a static MapLibre/WebGL site).</div>
</div>
<script>
const FR = {frames_js};
const base = document.getElementById('base'), ov = document.getElementById('ov');
const SAT = "data:image/png;base64,{sat_b64}";
base.src = SAT;
let i = 0;
function setFrame(k){{ i=k; ov.src="data:image/png;base64,"+FR[k].png;
  document.getElementById('lbl').textContent = FR[k].label + " · " + FR[k].ha + " ha in range"; }}
setFrame(0);
document.getElementById('t').addEventListener('input', e=>setFrame(+e.target.value));

// pan/zoom via shared transform on both layers
let scale=1, tx=0, ty=0;
const stage=document.getElementById('stage');
function apply(){{ const t=`translate(${{tx}}px,${{ty}}px) scale(${{scale}})`;
  base.style.transform=t; ov.style.transform=t; }}
function clamp(){{ const w=stage.clientWidth; const min=w*(1-scale);
  tx=Math.min(0,Math.max(min,tx)); ty=Math.min(0,Math.max(min,ty)); }}
stage.addEventListener('wheel', e=>{{ e.preventDefault();
  const r=stage.getBoundingClientRect(), px=e.clientX-r.left, py=e.clientY-r.top;
  const f=Math.exp(-e.deltaY*0.0015), ns=Math.min(8,Math.max(1,scale*f));
  const k=ns/scale; tx=px-(px-tx)*k; ty=py-(py-ty)*k; scale=ns; clamp(); apply(); }},{{passive:false}});
let drag=false, lx=0, ly=0;
stage.addEventListener('pointerdown', e=>{{ drag=true; lx=e.clientX; ly=e.clientY;
  stage.classList.add('drag'); stage.setPointerCapture(e.pointerId); }});
stage.addEventListener('pointermove', e=>{{ if(!drag)return; tx+=e.clientX-lx; ty+=e.clientY-ly;
  lx=e.clientX; ly=e.clientY; clamp(); apply(); }});
stage.addEventListener('pointerup', ()=>{{ drag=false; stage.classList.remove('drag'); }});
document.getElementById('reset').onclick=()=>{{ scale=1; tx=0; ty=0; apply(); }};
</script>"""


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
