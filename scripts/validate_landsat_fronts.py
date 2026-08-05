"""Landsat 30 m thermal — spatial check of the model's surface-temperature pattern.

The map draws a modelled cold-water edge. The only way to check its SHAPE against
observation is high-res thermal imagery. Landsat 8/9 Collection-2 Level-2 Surface
Temperature is 30 m and covers Thunder Bay, but its ABSOLUTE values are unreliable
over water (a land algorithm — runs several °C warm). So we validate PATTERN, not
level: subtract each field's own mean and ask whether LSOFS and Landsat agree on WHERE
it is relatively warm vs cool across the bay (anomaly correlation). That tests the
model's horizontal structure — the thing the isotherm-line geometry rests on.

Honest limits, stated up front: Landsat is a mid-morning snapshot (~11:00 local) vs the
model's 12Z; LSOFS horizontal resolution is 200 m–2.5 km so it cannot resolve the
sharpest 30 m fronts; and clear scenes are ~monthly. This is a spatial sanity check,
not a precision validation.

    python scripts/validate_landsat_fronts.py [YYYY-MM-DD]   # a date with a clear scene

Writes viz/landsat_fronts.html. Imagery/ST © USGS/NASA Landsat; anchor SST: NOAA GLSEA.
"""
from __future__ import annotations

import base64
import io
import sys
from datetime import date
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from tbay_fishcast.config import load_config  # noqa: E402
from tbay_fishcast.ingest import lsofs_grid  # noqa: E402
from tbay_fishcast.ingest.backfill import _open_first  # noqa: E402
from tbay_fishcast.ingest.lsofs_extract import extract_surface, valid_time_from_dataset  # noqa: E402
from tbay_fishcast.ingest.lsofs_paths import LsofsFile, candidate_urls  # noqa: E402

BBOX = (-89.35, 48.36, -88.90, 48.56)  # Thunder Bay + north shore (lon0,lat0,lon1,lat1)


def _landsat_scene(day: date):
    import planetary_computer as pc
    import pystac_client
    cat = pystac_client.Client.open("https://planetarycomputer.microsoft.com/api/stac/v1")
    lo, la, hi, ha = BBOX
    win = f"{day.isoformat()}/{day.isoformat()}"
    items = list(cat.search(collections=["landsat-c2-l2"], bbox=[lo, la, hi, ha],
                            datetime=win, query={"eo:cloud_cover": {"lt": 40}}).items())
    if not items:
        # widen to the whole month, pick the clearest
        win = f"{day.replace(day=1).isoformat()}/{day.isoformat()}"
        items = list(cat.search(collections=["landsat-c2-l2"], bbox=[lo, la, hi, ha],
                                datetime=win, query={"eo:cloud_cover": {"lt": 25}}).items())
    if not items:
        return None, None
    it = min(items, key=lambda i: i.properties["eo:cloud_cover"])
    return it, pc.sign(it.assets["lwir11"].href)


def main(argv) -> int:
    import rasterio
    from rasterio.warp import transform, transform_bounds
    from rasterio.windows import from_bounds

    day = date.fromisoformat(argv[1]) if len(argv) > 1 else date(2026, 7, 11)
    it, href = _landsat_scene(day)
    if it is None:
        print(f"no clear Landsat scene near {day}"); return 1
    sday = it.properties["datetime"][:10]
    cloud = it.properties["eo:cloud_cover"]
    print(f"Landsat scene {it.id} ({sday}, cloud {cloud:.0f}%)")

    import planetary_computer as pc
    lo, la, hi, ha = BBOX
    with rasterio.open(href) as ds:
        b = transform_bounds("EPSG:4326", ds.crs, lo, la, hi, ha)
        win = from_bounds(*b, transform=ds.transform)
        arr = ds.read(1, window=win).astype(float)
        wt = ds.window_transform(win)
        crs = ds.crs
    st = np.where(arr > 0, arr * 0.00341802 + 149.0 - 273.15, np.nan)  # C2 L2 ST -> °C
    st = np.where((st > 0) & (st < 32), st, np.nan)                    # keep plausible water temps
    # mask clouds via QA_PIXEL (bit1 dilated, bit2 cirrus, bit3 cloud, bit4 shadow)
    try:
        qhref = pc.sign(it.assets["qa_pixel"].href)
        with rasterio.open(qhref) as qds:
            qb = transform_bounds("EPSG:4326", qds.crs, lo, la, hi, ha)
            qa = qds.read(1, window=from_bounds(*qb, transform=qds.transform))
        if qa.shape == st.shape:
            cloudy = (qa & ((1 << 1) | (1 << 2) | (1 << 3) | (1 << 4))) > 0
            st = np.where(cloudy, np.nan, st)
            print(f"cloud-masked {100*cloudy.mean():.0f}% of the window via QA_PIXEL")
    except Exception as e:  # noqa: BLE001
        print(f"(QA cloud mask unavailable: {str(e)[:40]})")
    return _run(day, sday, cloud, st, wt, crs, date.fromisoformat(sday))


def cfg():  # tiny cache
    if not hasattr(cfg, "_c"):
        cfg._c = load_config()
    return cfg._c


def _run(day, sday, cloud, st, wt, crs, lday):
    import rasterio  # noqa: F401
    from rasterio.warp import transform

    f = LsofsFile(lday, "t12z", "n", 6)
    lds = _open_first(candidate_urls(f, cfg().lsofs.recent_bucket, cfg().lsofs.archive_bucket,
                                     byterange=False))
    grid = lsofs_grid.read_grid(lds)
    vt = valid_time_from_dataset(lds)
    lon = np.where(grid.lon > 180, grid.lon - 360, grid.lon)
    lo, la, hi, ha = BBOX
    inbox = np.where((lon >= lo) & (lon <= hi) & (grid.lat >= la) & (grid.lat <= ha)
                     & (grid.h >= 3.0))[0]
    surf = extract_surface(lds, {str(int(n)): int(n) for n in inbox})
    lds.close()
    stat = {int(r.station_id): r.temp_c for r in surf}

    # sample Landsat at each node location
    ny, nx = st.shape
    node_ll = [(int(n), float(lon[n]), float(grid.lat[n])) for n in inbox]
    xs, ys = transform("EPSG:4326", crs, [p[1] for p in node_ll], [p[2] for p in node_ll])
    inv = ~wt
    lsat_at, lsofs_at, pts = [], [], []
    for (n, lo_, la_), x, y in zip(node_ll, xs, ys):
        c, rr = inv * (x, y)
        ci, ri = int(round(c)), int(round(rr))
        if 0 <= ri < ny and 0 <= ci < nx and np.isfinite(st[ri, ci]) and n in stat:
            lsat_at.append(st[ri, ci]); lsofs_at.append(stat[n]); pts.append((lo_, la_))
    lsat_at = np.array(lsat_at); lsofs_at = np.array(lsofs_at)
    n = len(lsat_at)
    if n < 5:
        print(f"only {n} co-located water nodes — scene too cloudy/edge; try another date"); return 1

    # PATTERN agreement: correlate anomalies from each field's own mean
    la_an = lsat_at - lsat_at.mean()
    lo_an = lsofs_at - lsofs_at.mean()
    r = float(np.corrcoef(la_an, lo_an)[0, 1])
    print(f"co-located water nodes: {n}")
    print(f"Landsat water temp (biased, abs): mean {lsat_at.mean():.1f} C  range {lsat_at.min():.1f}-{lsat_at.max():.1f}")
    print(f"LSOFS surface:                    mean {lsofs_at.mean():.1f} C  range {lsofs_at.min():.1f}-{lsofs_at.max():.1f}")
    print(f"SPATIAL PATTERN correlation (anomaly-from-mean): r = {r:+.2f}  "
          f"({'agree' if r>0.3 else 'weak/no' } spatial agreement)")

    _figure(day, sday, cloud, st, wt, crs, pts, la_an, lo_an, r, n, vt_note=str(lday))
    return 0


def _figure(day, sday, cloud, st, wt, crs, pts, la_an, lo_an, r, n, vt_note):
    from rasterio.warp import transform
    ny, nx = st.shape
    ext = [wt.c, wt.c + wt.a * nx, wt.f + wt.e * ny, wt.f]  # (xmin,xmax,ymin,ymax) in scene CRS
    anom = st - np.nanmean(st)
    fig, ax = plt.subplots(figsize=(8, 7))
    im = ax.imshow(anom, extent=ext, origin="upper", cmap="RdBu_r", vmin=-3, vmax=3)
    plt.colorbar(im, ax=ax, shrink=.8, label="Landsat surface temp anomaly (°C, relative)")
    # node points colored by LSOFS anomaly (same colormap) — do the colors match the background?
    xs, ys = transform("EPSG:4326", crs, [p[0] for p in pts], [p[1] for p in pts])
    ax.scatter(xs, ys, c=lo_an, cmap="RdBu_r", vmin=-3, vmax=3, edgecolors="k",
               linewidths=1.2, s=90, label="LSOFS surface anomaly (nodes)")
    ax.set_title(f"Landsat 30 m thermal pattern vs LSOFS surface — {sday}\n"
                 f"anomaly-from-mean · spatial r = {r:+.2f} (n={n} nodes) · cloud {cloud:.0f}%",
                 fontsize=10)
    ax.set_xticks([]); ax.set_yticks([]); ax.legend(loc="lower right", fontsize=8)
    buf = io.BytesIO(); fig.savefig(buf, format="png", dpi=115, bbox_inches="tight"); plt.close(fig)
    b64 = base64.b64encode(buf.getvalue()).decode()
    out = Path(__file__).resolve().parents[1] / "viz" / "landsat_fronts.html"
    verdict = ("The model's surface pattern matches the satellite's where they can be compared."
               if r > 0.3 else
               "Spatial agreement is weak here — expected: LSOFS is too coarse to resolve the 30 m "
               "structure, and Landsat's over-water values are uncertain. Use Landsat for front "
               "geometry qualitatively, not as a quantitative gate.")
    out.write_text(f"""<title>Landsat Front Check — Thunder Bay</title>
<div style="max-width:900px;margin:0 auto;padding:30px;font-family:system-ui;line-height:1.5">
  <h1 style="font-size:22px">Landsat 30 m thermal vs model surface pattern</h1>
  <p style="color:#666">{sday} · If a node dot's colour matches the background it sits on, the model
    and satellite agree on relatively warm/cool there. Absolute temperature is removed (Landsat
    reads warm over water); this compares PATTERN only.</p>
  <img style="width:100%;border-radius:8px" src="data:image/png;base64,{b64}"/>
  <p style="margin-top:16px"><b>Spatial pattern correlation r = {r:+.2f}</b> (n={n} co-located water
    nodes). {verdict}</p>
  <p style="font-size:12px;color:#888;margin-top:18px;font-style:italic">Landsat C2 L2 Surface
    Temperature © USGS/NASA (30 m, unreliable absolute over water). LSOFS surface: NOAA. A
    mid-morning snapshot vs 12Z model; clear scenes ~monthly. Spatial sanity check, not a gate.</p>
</div>""")
    print(f"wrote {out}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
