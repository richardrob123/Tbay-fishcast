"""Build the 2D isotherm 'threshold-line' map (plan view) — the operator's ask.

Not a 1D ray. For each NONNA-covered shore spot this renders, looking DOWN from
above:
  * the real CHS NONNA-10 bathymetry (depth raster + shoreline),
  * the LAKER-CEILING LINE — where the bottom crosses the 12 °C isotherm depth,
    drawn as a BAND (data-driven uncertainty), not a false-precise single contour,
  * the cast-range collar (<=75 m from shore), and
  * the REACHABLE cold water = bottom-is-cold AND within-cast-range.

Temperature is corrected with the data-driven depth model (features/thermocline):
surface anchored to GLSEA satellite SST, subsurface bias measured LIVE against NDBC
buoy thermistors (fresh, like-for-like depth). The isotherm band carries that
measured spread through to the map.

    python scripts/build_isotherm_maps.py [YYYY-MM-DD]

Writes viz/isotherm_map.html (self-contained, PNGs embedded). CHS attribution in
the footer (NONNA licence).
"""
from __future__ import annotations

import base64
import io
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from scipy.ndimage import distance_transform_edt

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

from tbay_fishcast.config import load_config  # noqa: E402
from tbay_fishcast.features import thermocline  # noqa: E402
from tbay_fishcast.features.wind import in_sector, FAVORABLE_SECTOR  # noqa: E402
from tbay_fishcast.ingest import basemap, era5_wind, glsea, ndbc, nonna  # noqa: E402
from tbay_fishcast.ingest.backfill import _open_first, station_node_map  # noqa: E402
from tbay_fishcast.ingest.lsofs_extract import extract_nodes, valid_time_from_dataset  # noqa: E402
from tbay_fishcast.ingest.lsofs_paths import LsofsFile, candidate_urls  # noqa: E402
from tbay_fishcast.ingest import lsofs_grid  # noqa: E402

TARGET_C = 12.0
CAST_M = 75.0
PROFILE_DEPTHS = [1, 2, 4, 6, 8, 10, 15]
BIAS_DAYS = 4                     # pool the subsurface bias over this many recent days
MATCH_H = 1.5                     # buoy within +/- this of model 12Z (tight, no smearing)
BIAS_BAND_M = (2.5, 6.5)         # subsurface sensor depths used for the bias measurement
OUT_HTML = Path(__file__).resolve().parents[1] / "viz" / "isotherm_map.html"
BATHY_CMAP = LinearSegmentedColormap.from_list(
    "bathy", ["#eaf4f4", "#9ad1e0", "#3d8fb5", "#1f5f86", "#0d2f4f"])


def pooled_subsurface_bias(cfg, day):
    """LSOFS subsurface warm bias pooled over BIAS_DAYS x 3 buoys, tight-time-matched.

    Returns (central, lo, hi, per_buoy_rows, n). Central = pooled median (robust);
    band = pooled mean ± 1σ — the day-to-day + spatial spread that becomes the
    isotherm band. Stale-sensor buoys (e.g. 45028, dead since June) drop out.
    """
    buoyz = {"45027": 6.0, "45023": 5.0, "45216": 3.0}
    series = {}
    for sid in buoyz:
        try:
            series[sid] = ndbc.fetch_ocean_realtime(int(sid))
        except Exception:  # noqa: BLE001
            series[sid] = []

    def buoy_at(sid, z, t):
        pool = [r for r in series[sid]
                if abs(r.depth_m - z) < 0.6 and abs((r.time - t).total_seconds()) <= MATCH_H * 3600]
        return float(np.mean([r.temp_c for r in pool])) if pool else None

    per = {sid: [] for sid in buoyz}
    days = [date.fromordinal(day.toordinal() - k) for k in range(BIAS_DAYS)]
    for d in days:
        f = LsofsFile(d, "t12z", "n", 6)
        try:
            ds = _open_first(candidate_urls(f, cfg.lsofs.recent_bucket,
                                            cfg.lsofs.archive_bucket, byterange=False))
        except Exception:  # noqa: BLE001
            continue
        grid = lsofs_grid.read_grid(ds)
        vt = valid_time_from_dataset(ds)
        for sid, z in buoyz.items():
            bt = buoy_at(sid, z, vt)
            if bt is None:
                continue
            nm = lsofs_grid.nearest_node(grid, ndbc.BUOYS[sid].lat, ndbc.BUOYS[sid].lon, min_depth_m=3.0)
            lz = extract_nodes(ds, {f"b{sid}": nm.node}, [z])[0].temp_c
            per[sid].append(lz - bt)
        ds.close()

    allb = [v for vals in per.values() for v in vals]
    rows = []
    for sid, z in buoyz.items():
        v = per[sid]
        rows.append((sid, z, (float(np.mean(v)) if v else None),
                     (float(np.std(v)) if v else None), len(v)))
    if not allb:
        return 0.0, 0.0, 0.0, rows, 0
    central = float(np.median(allb))
    mu, sd = float(np.mean(allb)), float(np.std(allb))
    return central, mu - sd, mu + sd, rows, len(allb)


def upwelling_context(day):
    """Recent W-quadrant wind at Thunder Bay: is upwelling being driven? (dict or None)."""
    try:
        w = era5_wind.fetch_wind((date.fromordinal(day.toordinal() - 4)).isoformat(), day.isoformat())
    except Exception:  # noqa: BLE001
        return None
    sp = np.asarray(w.get("wind_speed_10m") or [], dtype=float)
    dr = np.asarray(w.get("wind_direction_10m") or [], dtype=float)
    if not len(sp):
        return None
    fav = in_sector(dr)
    last48 = slice(-48, None)
    fav_h = int(fav[last48].sum())
    insec = sp[last48][fav[last48]]
    return {"fav_hours_48": fav_h, "mean_insector_kn": float(insec.mean()) if insec.size else 0.0,
            "max_kn_24": float(sp[-24:].max()) if len(sp) >= 24 else float(sp.max())}


def render_spot(station, patch, band, surf_c, bias, day):
    d = patch.depth
    res = nonna.ground_res_m(patch.res_mercator_m, station.lat)
    water = np.isfinite(d)
    dist = distance_transform_edt(water) * res
    iso_c = band["central"]
    lo = band["shallow"] if band["shallow"] is not None else iso_c
    hi = band["deep"] if band["deep"] is not None else iso_c
    ny, nx = d.shape
    ext = [-nx / 2 * res, nx / 2 * res, -ny / 2 * res, ny / 2 * res]
    dd = np.where(water, d, np.nan)

    # reachable cold water: bottom at/below isotherm AND within cast range
    reachable = water & (dd >= iso_c) & (dist <= CAST_M)
    reach_area = int(reachable.sum()) * res * res
    reach_dist = float(dist[reachable].min()) if reachable.any() else None

    # satellite basemap for georeference validation (aligned to the patch's 3857 bounds)
    sat = None
    try:
        sat = basemap.fetch_imagery_3857(patch.bounds_3857, size_px=700)
    except Exception as e:  # noqa: BLE001
        print(f"    (satellite basemap unavailable: {str(e)[:50]})")

    from matplotlib.patheffects import withStroke
    halo = [withStroke(linewidth=2.2, foreground="black")]

    fig, ax = plt.subplots(figsize=(6.4, 6.4))
    ax.set_facecolor("#0a0d10")
    if sat is not None:
        ax.imshow(sat, origin="upper", extent=ext, interpolation="bilinear")
        depth_alpha, shore_col, sl_w = 0.42, "#8ff7ff", 1.6
    else:
        ax.set_facecolor("#efe9df")
        depth_alpha, shore_col, sl_w = 1.0, "#333333", 1.1
    im = ax.imshow(np.ma.masked_invalid(d), origin="upper", extent=ext, cmap=BATHY_CMAP,
                   vmin=0, vmax=max(12, float(np.nanpercentile(dd, 98))), alpha=depth_alpha)
    cb = plt.colorbar(im, shrink=0.82, pad=0.02); cb.set_label("depth (m)")
    # shoreline = NONNA water/land edge; the alignment check against the imagery
    ax.contour(np.where(water, d, -1), levels=[0.02], extent=ext, origin="upper",
               colors=shore_col, linewidths=sl_w)
    ax.contour(dist, levels=[CAST_M], extent=ext, origin="upper",
               colors="#ffb84d", linewidths=1.3, linestyles="dashed")
    if lo != hi:
        ax.contourf(dd, levels=sorted([lo, hi]), extent=ext, origin="upper",
                    colors=["#ff2d55"], alpha=0.20)
    if iso_c is not None:
        ax.contour(dd, levels=[iso_c], extent=ext, origin="upper",
                   colors="#ff173a", linewidths=2.6)
    if reachable.any():
        ax.contourf(reachable.astype(float), levels=[0.5, 1.5], extent=ext,
                    origin="upper", colors=["#39d353"], alpha=0.5, hatches=["///"])
    ax.plot(0, 0, marker="*", ms=15, color="w", mec="k")
    ax.plot([ext[0] + 60, ext[0] + 260], [ext[2] + 55, ext[2] + 55], "w", lw=3,
            path_effects=halo)
    ax.text(ext[0] + 60, ext[2] + 78, "200 m", fontsize=8, color="w", path_effects=halo)
    ax.annotate("N", xy=(ext[1] - 45, ext[3] - 42), fontsize=11, fontweight="bold",
                ha="center", color="w", path_effects=halo)
    ax.annotate("", xy=(ext[1] - 45, ext[3] - 48), xytext=(ext[1] - 45, ext[3] - 120),
                arrowprops=dict(arrowstyle="->", lw=1.6, color="w"))
    band_txt = f"{lo:.1f}–{hi:.1f} m" if lo != hi else f"{iso_c:.1f} m"
    ax.set_title(f"{station.name}\n{day} · surface {surf_c:.1f}°C · "
                 f"{TARGET_C:.0f}°C isotherm {band_txt}", fontsize=9.5)
    ax.set_xticks([]); ax.set_yticks([])
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=115, bbox_inches="tight")
    plt.close(fig)
    return {
        "png_b64": base64.b64encode(buf.getvalue()).decode(),
        "reach_area_m2": reach_area, "reach_dist_m": reach_dist,
        "iso_central": iso_c, "iso_lo": lo, "iso_hi": hi,
        "reachable": bool(reachable.any()), "has_sat": sat is not None,
        "coverage": float(water.mean()), "res_m": res,
    }


def build_page(day, cards, bias, detail, wind):
    def esc(x):
        return (x or "").replace("&", "&amp;").replace("<", "&lt;")
    rows = "".join(
        f"<tr><td>{sid}</td><td>{f'{z:.1f} m' if z is not None else '—'}</td>"
        f"<td>{f'{mu:+.2f}' if mu is not None else '— (stale)'}</td>"
        f"<td>{f'±{sd:.2f}' if sd is not None else '—'}</td><td>{n}</td></tr>"
        for sid, z, mu, sd, n in detail)
    if wind:
        wind_line = (f"Recent wind: {wind['fav_hours_48']}/48 h from the west quadrant at "
                     f"{wind['mean_insector_kn']:.0f} kn (gusts to {wind['max_kn_24']:.0f}). "
                     + ("Below the ~12–17 kn upwelling threshold — today's shallow cold water is "
                        "seasonal thermocline, not a strong upwelling event."
                        if wind['mean_insector_kn'] < 12 else
                        "At/above the upwelling threshold — cold water is being driven shoreward."))
    else:
        wind_line = ""
    panels = ""
    for c in cards:
        verdict = ("REACHABLE" if c["reachable"] else "NOT reachable")
        vclass = "go" if c["reachable"] else "out"
        detail_line = (f"closest reachable cold water ~{c['reach_dist_m']:.0f} m from shore · "
                       f"{c['reach_area_m2']/1e4:.2f} ha in range"
                       if c["reachable"] else
                       "12 °C water only meets bottom beyond cast range")
        panels += f"""
      <figure class="panel">
        <div class="ptop"><h2>{esc(c['name'])}</h2><span class="chip {vclass}">{verdict}</span></div>
        <img alt="isotherm plan view for {esc(c['name'])}"
             src="data:image/png;base64,{c['png_b64']}"/>
        <figcaption>{detail_line}. Isotherm band {c['iso_lo']:.1f}–{c['iso_hi']:.1f} m ·
          NONNA-10 ~{c['res_m']:.0f} m · water coverage {c['coverage']*100:.0f}%.</figcaption>
      </figure>"""
    excl = "".join(f"<li>{esc(n)}</li>" for n in cards.__class__ == list and [] or [])
    return f"""<title>Laker Threshold Lines — Thunder Bay</title>
<style>
:root{{--bg:#fbfaf7;--ink:#1a1f24;--muted:#5c6670;--line:#e4e0d8;--panel:#fff;
  --go:#2e8b57;--out:#b03a2e;--accent:#c1121f}}
@media (prefers-color-scheme:dark){{:root{{--bg:#12151a;--ink:#eef1f4;--muted:#9aa4ae;
  --line:#2a2f37;--panel:#181c22}}}}
:root[data-theme="dark"]{{--bg:#12151a;--ink:#eef1f4;--muted:#9aa4ae;--line:#2a2f37;--panel:#181c22}}
:root[data-theme="light"]{{--bg:#fbfaf7;--ink:#1a1f24;--muted:#5c6670;--line:#e4e0d8;--panel:#fff}}
*{{box-sizing:border-box}} body{{margin:0}}
.wrap{{max-width:1080px;margin:0 auto;padding:34px 22px 60px;color:var(--ink);
  background:var(--bg);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  line-height:1.5}}
h1{{font-size:26px;margin:0 0 4px;letter-spacing:-.01em}}
.sub{{color:var(--muted);font-size:14px;margin-bottom:22px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:20px}}
.panel{{margin:0;background:var(--panel);border:1px solid var(--line);border-radius:12px;
  padding:14px;overflow:hidden}}
.panel img{{width:100%;height:auto;border-radius:6px;display:block}}
.ptop{{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;gap:8px}}
.ptop h2{{font-size:15px;margin:0}}
.chip{{font-size:11px;font-weight:700;padding:3px 9px;border-radius:999px;white-space:nowrap}}
.chip.go{{background:var(--go);color:#fff}} .chip.out{{background:transparent;color:var(--out);
  border:1px solid var(--line)}}
figcaption{{font-size:11.5px;color:var(--muted);margin-top:9px;line-height:1.4}}
.legend{{display:flex;flex-wrap:wrap;gap:14px;font-size:12px;color:var(--muted);margin:16px 0 24px}}
.legend b{{color:var(--ink);font-weight:600}}
.sw{{display:inline-block;width:22px;height:0;vertical-align:middle;margin-right:5px}}
.method{{margin-top:30px;border-top:1px solid var(--line);padding-top:18px;font-size:13px;
  color:var(--muted)}}
.method h3{{color:var(--ink);font-size:14px;margin:16px 0 6px}}
table{{border-collapse:collapse;width:100%;font-size:12px;margin:8px 0;
  font-variant-numeric:tabular-nums}}
th,td{{text-align:left;padding:5px 9px;border-bottom:1px solid var(--line)}}
th{{color:var(--ink)}}
.caveat{{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--accent);
  border-radius:8px;padding:12px 14px;margin:16px 0;font-size:13px;color:var(--ink)}}
.attrib{{margin-top:18px;font-size:11px;color:var(--muted);font-style:italic}}
.wind{{font-size:12.5px;color:var(--ink);background:var(--panel);border:1px solid var(--line);
  border-radius:8px;padding:9px 12px;margin-bottom:18px}}
</style>
<div class="wrap">
  <h1>Laker threshold lines — Thunder Bay</h1>
  <div class="sub">{day} · analysis overlaid on satellite imagery, looking straight down.
    The red line is where the lake bottom crosses the {TARGET_C:.0f} °C laker ceiling —
    the closest cold water sits to shore. Green hatch = cold water you can actually
    reach ( ≤ {CAST_M:.0f} m ). The cyan line is the depth-model shoreline — it should
    hug the real land/water edge in the imagery; that it does is the georeference check.</div>
  {f'<div class="wind">{wind_line}</div>' if wind_line else ''}

  <div class="legend">
    <span><span class="sw" style="border-top:2px solid #29c5d6"></span><b>shoreline</b> depth-model vs imagery (alignment check)</span>
    <span><span class="sw" style="border-top:2.4px solid var(--accent)"></span><b>12 °C line</b> bottom meets the laker ceiling</span>
    <span><span class="sw" style="background:#d21f3c;opacity:.25;height:12px"></span><b>uncertainty band</b> (measured subsurface-bias spread)</span>
    <span><span class="sw" style="border-top:2px dashed #e8791a"></span><b>cast range</b> {CAST_M:.0f} m from shore</span>
    <span><span class="sw" style="background:#2e8b57;opacity:.5;height:12px"></span><b>reachable cold water</b></span>
  </div>

  <div class="grid">{panels}</div>

  <div class="caveat"><b>What's solid and what isn't.</b> The bathymetry is real CHS
    hydrographic survey (NONNA-10, ~10 m). The <em>surface</em> temperature is anchored
    to same-day satellite SST. The soft spot is the <em>subsurface</em> correction:
    Thunder Bay has no in-water thermometer, so the warm-bias is transferred from
    basin buoys — that is why the 12 °C line is drawn as a band, not a hairline.
    A single subsurface logger at one of these spots would collapse the band.
    One more limit: the temperature profile is the open-lake column, so it is most
    reliable on <em>open shore</em> (Silver Harbour). Inside a breakwall-enclosed basin
    (Marina) the water stratifies warmer than the open lake, so treat in-basin
    reachability cautiously — trust the open-lake side of the wall.</div>

  <div class="method">
    <h3>How the 12 °C depth was set (data-driven, {day})</h3>
    <p>Correction tapers from a satellite-anchored surface to a subsurface warm bias
      measured live as LSOFS minus in-situ NDBC thermistors — pooled over the last
      {BIAS_DAYS} days at each buoy's own sensor depth, matched tightly to model time
      (no smearing). Stale sensors are dropped automatically.</p>
    <table><thead><tr><th>buoy</th><th>sensor depth</th><th>mean bias °C</th><th>day-to-day σ</th><th>n days</th></tr></thead>
      <tbody>{rows}</tbody></table>
    <p>Pooled subsurface warm bias: central <b>{bias.subsurface_bias_c:+.1f} °C</b>,
      band <b>{bias.subsurface_bias_lo_c:+.1f} … {bias.subsurface_bias_hi_c:+.1f} °C</b>
      (mean ± 1σ over n={bias.n_buoys} buoy·days). The band is widest near the thermocline,
      where internal waves move faster than the model can phase-match — which is exactly
      the depth the isotherm sits at, so the map shows a band, not a line.</p>
    <div class="attrib">Bathymetry contains intellectual property of the Canadian
      Hydrographic Service (CHS), Fisheries and Oceans Canada — not for navigation.
      Surface SST: NOAA GLSEA. Subsurface truth: NDBC. Wind: ERA5 via Open-Meteo.
      Satellite basemap: Imagery © Esri, Maxar, Earthstar Geographics.</div>
  </div>
</div>"""


def main(argv) -> int:
    day = date.fromisoformat(argv[1]) if len(argv) > 1 else date(2026, 8, 4)
    cfg = load_config()
    nodes = station_node_map(cfg)
    f = LsofsFile(day, "t12z", "n", 6)
    ds = _open_first(candidate_urls(f, cfg.lsofs.recent_bucket, cfg.lsofs.archive_bucket,
                                    byterange=False))
    rows = extract_nodes(ds, nodes, PROFILE_DEPTHS)
    ds.close()

    central, lo, hi, detail, n = pooled_subsurface_bias(cfg, day)
    print(f"pooled subsurface warm bias: central {central:+.2f} band [{lo:+.2f},{hi:+.2f}] n={n}")
    wind = upwelling_context(day)
    if wind:
        print(f"wind: {wind['fav_hours_48']}/48h W-quadrant, {wind['mean_insector_kn']:.1f}kn in-sector")

    cards = []
    for s in cfg.shore_stations:
        pr = sorted([r for r in rows if r.station_id == s.id], key=lambda r: r.depth_m)
        depths = [r.depth_m for r in pr]
        raw = [r.temp_c for r in pr]
        try:
            g = glsea.fetch_sst(s.lat, s.lon, day).sst_c
        except Exception as e:  # noqa: BLE001
            print(f"  {s.id}: GLSEA unavailable ({str(e)[:40]}) — skipping"); continue
        surf_bias = raw[0] - g
        bias = thermocline.BiasModel(surf_bias, central, lo, hi, n_buoys=n)
        band = thermocline.isotherm_band(depths, raw, bias, TARGET_C)
        try:
            patch = nonna.fetch_patch(s.lat, s.lon, half_m=1000, scale_px=280)
        except Exception as e:  # noqa: BLE001
            print(f"  {s.id}: NONNA fetch failed ({str(e)[:40]}) — skipping"); continue
        if patch.coverage_frac < 0.05 or band["central"] is None:
            print(f"  {s.id}: no NONNA nearshore coverage or no isotherm — skipping plan view")
            continue
        card = render_spot(s, patch, band, g, bias, day)
        card["name"] = s.name
        cards.append(card)
        print(f"  {s.id}: iso {band['central']:.1f} m (band {card['iso_lo']:.1f}-{card['iso_hi']:.1f}) "
              f"reachable={card['reachable']}")

    if not cards:
        print("no plan views produced")
        return 1
    biasmodel = thermocline.BiasModel(0.0, central, lo, hi, n_buoys=n)
    OUT_HTML.write_text(build_page(day, cards, biasmodel, detail, wind))
    print(f"wrote {OUT_HTML}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
