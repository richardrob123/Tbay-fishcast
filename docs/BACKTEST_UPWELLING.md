# Backtest — upwelling response (Aug 2026 event)

**Question.** Does the modelled 12 °C laker line move the way upwelling physics
demands — cold water shoaling shoreward under sustained west wind — and does an
independent instrument agree?

**Natural experiment.** Early August 2026 at buoy 45027 (western Superior, 6 m
thermistor):

| date | buoy 6 m °C | W-wind hrs | regime |
|------|------------:|-----------:|--------|
| Aug 3 | 16.5 | 0 | warm, dead calm (stratified) |
| Aug 4 | 8.0 | 16 | west wind → upwelling |
| Aug 5 | 7.7 | 18 | upwelling continues |

An 8.5 °C subsurface drop in 48 h, driven by west wind — textbook upwelling.

## Result — confirmed, with honest limits

**1. Model tracks the event (truth-anchored).** The 12 °C isotherm crossed the
buoy's 6 m sensor on **Aug 4 in both the buoy and LSOFS+correction** (buoy
16.5→8.0; corrected LSOFS 13.1→9.6, i.e. from above to below the sensor). The
model has real upwelling-*timing* skill at the point where we can check it.

**2. Shore line marches shoreward.** At Silver Harbour the modelled 12 °C isotherm
(raw LSOFS) shoaled **10 → 7 → 2 m across Aug 3→5** as west-wind hours went
0 → 16 → 18, widening the reachable cold-water zone. Direction and timing match the
wind forcing.

**3. Limits — stated, not hidden.**
- **Day-to-day misses.** Aug 2 LSOFS ran warm at 6 m (16.6 °C) while the buoy was
  already cold (9.8 °C) — a genuine miss. The model captures the big transition, not
  every wiggle.
- **State-dependent correction.** The +3.3 °C subsurface warm-bias is a pooled
  average. It is smallest on calm days (Aug 3: raw LSOFS 6 m = 16.4 vs buoy 16.5 —
  essentially no bias, so the correction *over-cools*) and largest mid-event. This is
  exactly why the map draws the isotherm as a **band**, not a line.
- **Spatial transfer.** The truth buoy is ~270 km away in the western basin
  (AUDIT_ROUND3: the "~90 km" previously stated here understated it 3×). Thunder
  Bay's shore feels the same synoptic wind but responds on its own local timing.
- **No binary flip at Silver.** Silver's outer-rock shoals hold cold water within
  cast range on both calm and event days, so the reachable *flag* stays true; what
  changes is how far offshore the main line sits and how much water is in range. A
  flatter shore would flip.

## Verdict

The product's central mechanism — cold water shoaling into cast range under west
wind — **reproduces a documented event and agrees with in-situ truth on timing.**
The residual uncertainty is honest and quantified (the band), and it is largest
during the very events the tool is for, which a single Thunder Bay subsurface logger
would collapse.

Reproduce: `python scripts/backtest_upwelling.py` → `viz/backtest_upwelling.html`.

Sources: LSOFS (NOAA), NDBC 45027 (subsurface truth), GLSEA (surface SST), ERA5 /
Open-Meteo (wind), CHS NONNA-10 (bathymetry), Esri World Imagery (basemap).
