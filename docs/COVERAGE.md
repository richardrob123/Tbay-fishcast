# Coverage — what the map maps, and where it stops

The forecast covers the NW Lake Superior shore as a set of overlapping ~8.4 km boxes
(`STRETCHES` in `scripts/build_coast_site.py`). A box is included only if a live NONNA
bathymetry probe shows enough surveyed water (<3 % is skipped) and LSOFS has nodes in it.
Coverage is **bathymetry-limited**: where the Canadian Hydrographic Service never surveyed,
there is no depth, and a depth-based cold-water map cannot be built.

## Three groups

1. **The continuous city arc** — Kam mouth → MacKenzie Point / Silver Harbour. Seven
   overlapping boxes, gapless (~26 km). The best-surveyed, best-calibrated stretch.
2. **SW detached cluster** — Chippewa (near the city, continuous-ish with the Kam mouth) and,
   ~28 km further SW, **Little Trout Bay / Cloud Bay**. The shore *between* (~48.15–48.25 N)
   is unsurveyed in NONNA, so the SW-far box stands alone.
3. **NE detached cluster** — **Silver Islet / Sleeping Giant tip**, ~40 km E across Black Bay
   (which is unsurveyed), so it is not continuous with the arc. Sleeping Giant PP access/regs
   are verified off-system by the operator (ADR-007 / ADR-022).

## Known gaps (disclosed, not hidden)

- **Little Trout Bay is only ~14 % surveyed in NONNA.** It is buildable but sparse — the cold
  band is interpolated across mostly-unsurveyed water from ~21 LSOFS nodes, so its reachable
  area is large but poorly constrained (it reported ~138 ha vs ~7–80 ha for well-surveyed
  stretches — an over-claim, not confidence). The map handles this honestly: every stretch
  carries `survey_cov` in the manifest, any box under 25 % is marked `low_confidence`, and the
  headline "ha reachable" **excludes** low-confidence boxes (shown separately as "+N
  sparse-survey areas, indicative"). The overlay still draws so the angler can see it, flagged.
  (An earlier note that LTB had *zero* bathymetry was a coordinate error — the real bay at
  48.07 N, 89.45 W does have partial coverage.)
- **The shore between Chippewa and Little Trout Bay (~48.15–48.25 N) is unsurveyed** — no box.
- **Black Bay and the inner Sibley/Sturgeon Bay embayments are unsurveyed or warmwater** — no
  cold-water box; the deliberately-excluded warmwater launches (Mountdale/Kam, Sturgeon Bay)
  are boat launches, not cold shore, and are not stations.

## Filling the gaps (future)

The only realistic path to depth where CHS never surveyed (Little Trout Bay's deeper water, the
Chippewa–LTB gap) is **satellite-derived bathymetry** from Sentinel-2 — feasible given Lake
Superior's clarity (~10 m retrieval), but a calibrated modelling pipeline, not a drop-in
ingest. Documented as an option, not built. Local subsurface *temperature* truth (Bare Point
intake; the UMN-Duluth mooring archive) is tracked separately as the accuracy lever.
