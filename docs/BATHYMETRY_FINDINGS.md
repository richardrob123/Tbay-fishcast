# Bathymetry — sourcing findings (2026-08-05)

The cross-shore product needs depth-vs-distance-from-shore per spot. Looked hard for
high-resolution near-shore bathymetry for Thunder Bay; here is the honest state.

| source | resolution | Thunder Bay | verdict |
|---|---|---|---|
| **NCEI Great Lakes (Superior grid)** | ~92 m | full lake, real | **Works, any-location** — but too coarse/smoothed for the 0–75 m cast zone (reads ~4–5 m at Silver Harbour where the operator's soundings say 5–7 m; it flattens the drop-off). Good for offshore context + first-cut anywhere. |
| **CHS NONNA-10** | 10 m | Canadian waters | Exists, but its public GeoServer (`nonna-geoserver.data.chs-shc.ca`) returned **empty for every request** — WCS GetCoverage, WMS GetMap/GetFeatureInfo, both resolutions, all zoom scales — **even at known-surveyed ocean ports (Halifax, Vancouver)**. Access is broken in a way not crackable without an unbounded rabbit hole (likely CRS/axis handling, auth, or the interactive CHS download portal). **Dead end via API.** |
| ETOPO / GEBCO (ERDDAP) | ~1.8 km | global | Far too coarse for shore work. |
| **Operator countdown-sonar soundings** | metre | the actual spots | **The real cast-zone source.** The operator already calibrates per-lure vertical drop at known depth (counts→metres) — those soundings beat every public dataset in the cast zone. |

## Decision — hybrid bathymetry

- **Primary (cast zone): operator soundings** where they exist (seed already has Silver
  Harbour 5–7 m at cast range). Wire the countdown-sonar soundings in as they accumulate.
- **Fallback / any-location: the NCEI 92 m grid**, sampled with water-snapping + auto
  offshore-direction detection (works for an arbitrary dropped pin, coarse near shore).
- NONNA-10 remains a possible upgrade *if* the user pulls tiles via the CHS download portal
  manually; not machine-accessible here.

**Takeaway for the product:** public data can't resolve 75 m from shore at Thunder Bay.
Cast-zone precision comes from the operator's own soundings — which is why the sonar
calibration is in the kit. The NCEI grid gives real any-location coverage as the base layer.
