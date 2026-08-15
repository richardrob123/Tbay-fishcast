"""Out-of-range requests must clamp, never fail the whole window (ADR-054).

This module pins one failure MODE, not one bug, because the same mode has now bitten three
separate upstreams and each time it disabled a validation check while the run reported success:

  * GLSEA/ERDDAP answers an end_date past the dataset's coverage with a 404 for the ENTIRE
    range. The subsurface guards ran for a week against an empty satellite series and quietly
    judged nothing.
  * Open-Meteo's archive answers an end_date past today with a hard error for the ENTIRE
    request. The surface gate would have failed on its first scheduled run.

Every one of these callers legitimately asks past the end of coverage — a hindcast needs data
out to its longest lead's valid time, which is in the future by construction. So "don't ask for
the future" is not the fix; clamping at the boundary that knows the API's contract is.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from tbay_fishcast.features import site_validity as sv
from tbay_fishcast.ingest import wind_archive

ROOT = Path(__file__).resolve().parents[1]


def test_wind_archive_clamps_a_future_end_to_today(monkeypatch):
    """The exact request the surface gate makes: end = yesterday + 7 days."""
    seen = {}

    def fake_run(cmd, **kw):
        seen["url"] = cmd[-1]
        raise AssertionError("stop before the network")

    monkeypatch.setattr(wind_archive.subprocess, "run", fake_run)
    today = datetime.now(timezone.utc).date()
    with pytest.raises(AssertionError):
        wind_archive.fetch_hourly_wind(today - timedelta(days=14), today + timedelta(days=7))
    assert f"end_date={today.isoformat()}" in seen["url"]
    assert "start_date=" in seen["url"]


def test_wind_archive_returns_empty_rather_than_asking_backwards(monkeypatch):
    """A window entirely in the future clamps to end < start. Returning empty is the honest
    answer; sending a reversed range would be a 400 the caller reads as an outage."""
    def boom(*_a, **_k):
        raise AssertionError("must not reach the network")

    monkeypatch.setattr(wind_archive.subprocess, "run", boom)
    today = datetime.now(timezone.utc).date()
    t, d, k = wind_archive.fetch_hourly_wind(today + timedelta(days=2), today + timedelta(days=5))
    assert (t, d, k) == ([], [], [])


def test_wind_archive_cache_key_uses_the_clamped_end(monkeypatch):
    """A clamped fetch must not be cached under the REQUESTED end. If it were, a later call
    whose window is genuinely available would hit a truncated series and never notice."""
    monkeypatch.setattr(wind_archive, "_CACHE", Path("/nonexistent-cache-for-test"))
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=3)
    clamped = wind_archive._cache_path(
        json.dumps(["om-archive", wind_archive.TBAY_LAT, wind_archive.TBAY_LON,
                    start.isoformat(), today.isoformat()]))
    requested = wind_archive._cache_path(
        json.dumps(["om-archive", wind_archive.TBAY_LAT, wind_archive.TBAY_LON,
                    start.isoformat(), (today + timedelta(days=7)).isoformat()]))
    assert clamped != requested

    seen = {}

    def fake_run(cmd, **kw):
        seen["url"] = cmd[-1]
        raise AssertionError("stop before the network")

    monkeypatch.setattr(wind_archive.subprocess, "run", fake_run)
    with pytest.raises(AssertionError):
        wind_archive.fetch_hourly_wind(start, today + timedelta(days=7))
    assert f"end_date={today.isoformat()}" in seen["url"]


def test_a_model_only_site_check_says_so():
    """Passing with no observation means 'the model is not grossly wrong here', NOT 'the
    observation checks out' — there was no observation. The two must not read the same."""
    v = sv.check([(18.2, None, 18.1)] * 20)
    assert v.usable
    assert "model arm only" in v.reason
    v2 = sv.check([(18.2, 18.0, 18.1)] * 20)
    assert v2.usable and "model arm only" not in v2.reason


def test_the_published_surface_verdict_withholds_skill():
    """ADR-053 as an artifact invariant. If someone widens the reference-variability bar, this
    catches the published file still asserting a disqualification the guard no longer makes —
    and equally catches a skill verdict appearing where none may be issued."""
    p = ROOT / "data" / "calib" / "surface_skill.json"
    if not p.exists():
        pytest.skip("surface gate has not been analyzed in this checkout")
    d = json.loads(p.read_text())
    rd = d["reference_disqualified"]
    ratio = rd["reference_daily_change_sd_c"] / rd["insitu_daily_change_sd_c"]
    assert rd["disqualified"] is True
    assert ratio < sv.VARIABILITY_RATIO_MIN, (
        "the recorded reference now passes the live variability bar — revise ADR-053 "
        "deliberately rather than letting a skill verdict reappear by default")
    assert d["demote_leads"] == []
    assert d["verdict"].startswith("NO SKILL VERDICT")


def test_the_surface_gate_never_scores_a_row_against_its_own_year():
    """Climatology is other-years-only (rule 6). The auto range must stop before the run year,
    however far in the future that year is."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "bsg", ROOT / "scripts" / "backfill_surface_gate.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    for yr in (2025, 2026, 2031):
        years = list(range(m.CLIM_FIRST_YEAR, date(yr, 6, 1).year))
        assert years and max(years) < yr


# --- OVERSIZED requests must chunk, not just out-of-range ones (ADR-057) ----------------------
#
# A second, distinct failure mode with the same symptom. ECCC and Open-Meteo both kill a response
# that is too large MID-FLIGHT: no HTTP error, no partial page, an empty body indistinguishable
# from "this station has no data". Measured directly on ECCC: 2024-04-01..11-30 returns 6.0 MB in
# 1.5 s, and the same request one month longer is reset by the peer. `limit`/`offset` do not help
# because the server dies composing the response before paging applies.

def test_the_ingests_bound_their_request_size():
    from tbay_fishcast.ingest import eccc_wind, openmeteo_prev_runs as pr
    # Bounds must be well under the measured wall (~8 months of hourly ECCC records).
    assert 0 < eccc_wind.CHUNK_DAYS <= 180
    assert 0 < pr.ARCHIVE_CHUNK_DAYS <= 180


def test_a_multi_year_request_is_split_into_bounded_chunks(monkeypatch, tmp_path):
    """The caller asks for years; the wire sees bounded windows and every day is covered once."""
    from tbay_fishcast.ingest import eccc_wind
    seen = []

    def fake_get(url, timeout, tries=4):
        import re
        a, b = re.findall(r"datetime=([0-9-]+)T[^/]+/([0-9-]+)T", url)[0]
        seen.append((a, b))
        return {"features": []}

    monkeypatch.setattr(eccc_wind, "_get", fake_get)
    # tmp_path, not a fixed fake dir: fetch_hourly WRITES its cache on the way out, so a
    # constant path made the test pass once and then read its own leftovers forever after.
    monkeypatch.setattr(eccc_wind, "_CACHE", tmp_path / "eccc")
    eccc_wind.fetch_hourly(date(2024, 1, 1), date(2026, 6, 30))
    assert len(seen) >= 6, "a 2.5-year request must not go out as one call"
    for a, b in seen:
        span = (date.fromisoformat(b) - date.fromisoformat(a)).days + 1
        assert span <= eccc_wind.CHUNK_DAYS
    # contiguous and complete: each chunk starts the day after the previous one ends
    for (_a1, b1), (a2, _b2) in zip(seen, seen[1:]):
        assert date.fromisoformat(a2) == date.fromisoformat(b1) + timedelta(days=1)
    assert date.fromisoformat(seen[0][0]) == date(2024, 1, 1)
    assert date.fromisoformat(seen[-1][1]) == date(2026, 6, 30)


def test_a_recorded_audit_survives_a_transient_outage():
    """A measurement must not be deleted by an unrelated endpoint's rate limit. Seen live: an
    Open-Meteo hourly limit turned a recorded variability ratio of 0.84 into a null that reads
    as 'never checked'."""
    p = ROOT / "data" / "calib" / "wind_lead_skill.json"
    if not p.exists():
        pytest.skip("wind-lead gate has not been analyzed in this checkout")
    d = json.loads(p.read_text())
    audit = d.get("era5_reference_audit")
    assert audit, "the ADR-032 reference audit must be present, fresh or carried forward"
    assert audit.get("variability_ratio") is not None
    if audit.get("carried_forward_from"):
        assert audit.get("carried_forward_reason"), "a carried value must say why (rule 5)"
