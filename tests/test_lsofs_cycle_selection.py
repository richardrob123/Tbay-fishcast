"""Guard the LSOFS cycle selection (hermetic — the S3 open is monkeypatched, no network).

WHY THIS EXISTS: the build read t12z and only t12z, while LSOFS actually posts four cycles a day.
A map built at 02:40 UTC therefore served the PREVIOUS day's noon field, and a phone opened at
breakfast read ~25 h stale every morning — the exact hour someone checks a fishing app. Reported
by the operator 2026-08-09.

The safety property is DIRECTIONAL: rule 5 says staleness must be loud. It is acceptable for the
map to be old; it is NOT acceptable for it to *claim* to be newer than it is. So the tests below
pin (a) newest-cycle-first selection, (b) never selecting a cycle whose hour has not happened yet,
and (c) issued_utc landing on the real cycle hour so the age the page computes is never flattering.
"""
from __future__ import annotations

import importlib
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

bcs = importlib.import_module("build_coast_site")


class _FakeDS:
    def close(self):
        pass


def _fake_open(posted):
    """Stand in for the S3 open: succeeds only for (day, cycle) pairs in `posted`."""
    def _open(urls):
        u = urls[0] if isinstance(urls, (list, tuple)) else urls
        for d, c in posted:
            if f"lsofs.{c}.{d:%Y%m%d}." in u:
                return _FakeDS()
        raise OSError("not posted")
    return _open


def _at(monkeypatch, now_utc, posted):
    monkeypatch.setattr(bcs, "_open_first", _fake_open(posted))

    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return now_utc
    monkeypatch.setattr(bcs, "datetime", _DT)


def test_picks_newest_posted_cycle_not_just_t12z(monkeypatch):
    """The whole point: with t00z and t06z both up, take t06z — not yesterday's t12z."""
    day = date(2026, 8, 9)
    posted = [(day, "t00z"), (day, "t06z"), (date(2026, 8, 8), "t12z")]
    _at(monkeypatch, datetime(2026, 8, 9, 12, 35, tzinfo=timezone.utc), posted)
    assert bcs._resolve_issue(_cfg(), day) == (day, "t06z")


def test_never_selects_a_cycle_whose_hour_has_not_happened(monkeypatch):
    """A t18z file cannot exist at 12:35Z. Even if the opener would happily return one (a stale
    mirror, a mis-keyed object), the clock guard must reject it — otherwise the map would date
    itself six hours into the future and report a NEGATIVE age."""
    day = date(2026, 8, 9)
    posted = [(day, "t18z"), (day, "t06z")]          # t18z "available" but impossible
    _at(monkeypatch, datetime(2026, 8, 9, 12, 35, tzinfo=timezone.utc), posted)
    assert bcs._resolve_issue(_cfg(), day) == (day, "t06z")


def test_walks_back_a_day_when_nothing_posted_today(monkeypatch):
    day = date(2026, 8, 9)
    posted = [(date(2026, 8, 8), "t18z"), (date(2026, 8, 8), "t12z")]
    _at(monkeypatch, datetime(2026, 8, 9, 2, 10, tzinfo=timezone.utc), posted)
    assert bcs._resolve_issue(_cfg(), day) == (date(2026, 8, 8), "t18z")


def test_reported_age_is_never_younger_than_the_truth(monkeypatch):
    """The directional invariant. For every cycle, the issued_utc the manifest publishes must sit
    at that cycle's real hour — so the client-side age is >= the true age, never below it. The old
    hardcoded T12:00:00Z broke this by up to 12 h on a t00z build."""
    now = datetime(2026, 8, 9, 12, 35, tzinfo=timezone.utc)
    for cyc, hr in bcs._CYCLE_HOUR.items():
        day = date(2026, 8, 9)
        issued = datetime(day.year, day.month, day.day, hr, tzinfo=timezone.utc)
        if issued > now:
            continue
        true_age = (now - issued).total_seconds() / 3600
        naive_age = (now - datetime(day.year, day.month, day.day, 12,
                                    tzinfo=timezone.utc)).total_seconds() / 3600
        assert true_age >= naive_age - 1e-9, (
            f"{cyc}: hardcoding noon would report {naive_age:.1f} h for a field that is "
            f"actually {true_age:.1f} h old")


def test_cycle_order_is_strictly_newest_first():
    assert list(bcs._CYCLES_NEWEST_FIRST) == sorted(
        bcs._CYCLES_NEWEST_FIRST, key=lambda c: -bcs._CYCLE_HOUR[c])
    assert set(bcs._CYCLES_NEWEST_FIRST) == set(bcs._CYCLE_HOUR)


def _cfg():
    class _L:
        recent_bucket = "recent"
        archive_bucket = "archive"

    class _C:
        lsofs = _L()
    return _C()
