"""The shared fetch layer (ADR-059) — one place that knows how remote sources fail.

Every case here is a real failure this project hit, reduced to a test. The point of the module is
that a NEW caller inherits all of them without knowing they exist.
"""
from __future__ import annotations

from datetime import date, timedelta

from tbay_fishcast.ingest import windowed as w

TODAY = date(2026, 8, 15)


def _recorder(fail_windows=(), records_per_chunk=3):
    """A fake fetch that records the windows it was asked for and can fail chosen ones."""
    seen = []
    fails = {tuple(f) for f in fail_windows}

    def fetch(a, b):
        seen.append((a.isoformat(), b.isoformat()))
        if (a.isoformat(), b.isoformat()) in fails:
            return []
        return [(a, i) for i in range(records_per_chunk)]

    return fetch, seen


# --- clamping: ERDDAP 404s the WHOLE range, Open-Meteo hard-errors a future end ----------------

def test_end_is_clamped_to_source_coverage():
    s, e, clamped, why = w.clamp_window(date(2026, 5, 1), date(2026, 10, 31),
                                        coverage_end="2026-08-13")
    assert e == date(2026, 8, 13) and clamped
    assert "coverage ends 2026-08-13" in why


def test_end_is_clamped_to_today_when_the_source_has_no_future():
    s, e, clamped, why = w.clamp_window(date(2026, 8, 1), date(2026, 8, 22),
                                        not_after_today=True, today=TODAY)
    assert e == TODAY and clamped and "future" in why


def test_both_clamps_compose_and_the_tighter_one_wins():
    _s, e, clamped, why = w.clamp_window(date(2026, 8, 1), date(2026, 8, 30),
                                         coverage_end="2026-08-10", not_after_today=True,
                                         today=TODAY)
    assert e == date(2026, 8, 10) and clamped
    assert "future" in why and "coverage" in why


def test_an_untouched_window_is_not_reported_as_clamped():
    _s, e, clamped, why = w.clamp_window(date(2026, 6, 1), date(2026, 6, 10),
                                         coverage_end="2026-08-13", not_after_today=True,
                                         today=TODAY)
    assert e == date(2026, 6, 10) and not clamped and why is None


def test_a_window_entirely_past_coverage_returns_empty_not_a_reversed_range():
    fetch, seen = _recorder()
    recs, rep = w.fetch_windowed(date(2026, 9, 1), date(2026, 9, 30), fetch=fetch,
                                 coverage_end="2026-08-13", sleep=lambda _s: None)
    assert recs == [] and seen == [], "must not reach the network with end < start"
    assert rep.clamp_reason and not rep.partial


# --- chunking: ECCC resets the connection on an oversized response ------------------------------

def test_a_long_window_is_split_into_bounded_contiguous_chunks():
    fetch, seen = _recorder()
    _recs, rep = w.fetch_windowed(date(2024, 1, 1), date(2026, 6, 30), fetch=fetch,
                                  chunk_days=90, sleep=lambda _s: None)
    assert rep.chunks == len(seen) >= 10
    for a, b in seen:
        assert (date.fromisoformat(b) - date.fromisoformat(a)).days + 1 <= 90
    for (_a1, b1), (a2, _b2) in zip(seen, seen[1:]):
        assert date.fromisoformat(a2) == date.fromisoformat(b1) + timedelta(days=1)
    assert seen[0][0] == "2024-01-01" and seen[-1][1] == "2026-06-30"


# --- retry: silence is the failure signal, so silence must be retried ---------------------------

def test_an_empty_chunk_is_retried_before_being_given_up_on():
    calls = {"n": 0}

    def flaky(a, b):
        calls["n"] += 1
        return [] if calls["n"] < 3 else [("ok", a)]

    recs, rep = w.fetch_windowed(date(2026, 6, 1), date(2026, 6, 10), fetch=flaky,
                                 sleep=lambda _s: None)
    assert len(recs) == 1 and rep.attempts == 3 and not rep.partial


def test_a_raising_chunk_is_retried_too():
    """ECCC resets the connection, Open-Meteo returns an empty body, Iowa State returns a
    plain-text notice with HTTP 200. The transport signals differ; the treatment does not."""
    calls = {"n": 0}

    def boom(a, b):
        calls["n"] += 1
        if calls["n"] < 2:
            raise ConnectionResetError("Recv failure: Connection reset by peer")
        return [("ok", a)]

    recs, rep = w.fetch_windowed(date(2026, 6, 1), date(2026, 6, 10), fetch=boom,
                                 sleep=lambda _s: None)
    assert len(recs) == 1 and rep.attempts == 2 and not rep.partial


# --- the whole point: the caller cannot get the data without the evidence ----------------------

def test_a_permanently_empty_window_is_reported_missing_not_silently_dropped():
    """THE BUG THIS MODULE EXISTS FOR. A rate-limited year silently vanished from a 2018-2024
    training set while the label still said 2018-2024."""
    fetch, _seen = _recorder(fail_windows=[("2024-03-31", "2024-06-28")])
    recs, rep = w.fetch_windowed(date(2024, 1, 1), date(2024, 9, 30), fetch=fetch,
                                 chunk_days=90, tries=2, sleep=lambda _s: None)
    assert rep.partial and rep.missing == [("2024-03-31", "2024-06-28")]
    assert rep.records == len(recs) and rep.records > 0      # partial, not empty
    assert "INCOMPLETE" in rep.describe()


def test_the_report_says_what_was_actually_covered_not_what_was_asked():
    fetch, _seen = _recorder()
    _recs, rep = w.fetch_windowed(date(2026, 5, 1), date(2026, 10, 31), fetch=fetch,
                                  coverage_end="2026-08-13", chunk_days=90,
                                  sleep=lambda _s: None)
    d = rep.as_dict()
    assert d["requested"] == ["2026-05-01", "2026-10-31"]
    assert d["effective"][1] == "2026-08-13"
    assert d["clamped"] is True and d["partial"] is False
    assert "clamped to 2026-08-13" in rep.describe()


def test_records_count_is_measured_not_assumed():
    fetch, _seen = _recorder(records_per_chunk=7)
    recs, rep = w.fetch_windowed(date(2026, 1, 1), date(2026, 6, 30), fetch=fetch,
                                 chunk_days=90, sleep=lambda _s: None)
    assert rep.records == len(recs) == 7 * rep.chunks
