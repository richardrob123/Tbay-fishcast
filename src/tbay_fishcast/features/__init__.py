"""Feature layer — pure, deterministic transforms over ingested data (ADR-001).

Phase 0 load-bearing feature: sigma-layer -> fixed-depth temperature interpolation
(features/sigma.py), property-tested. Wedderburn / upwelling-event detectors are
skeletons here until reference wind ingest (ERA5) is reachable.
"""
