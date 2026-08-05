"""Forecast reachability-window logic tests (pure)."""
from datetime import datetime, timedelta, timezone

from tbay_fishcast.features.forecast import (
    ForecastPoint,
    reachable_windows,
    summarize,
)

T0 = datetime(2026, 8, 4, 12, tzinfo=timezone.utc)


def _pt(lead, reachable, iso=3.0):
    return ForecastPoint(T0 + timedelta(hours=lead), lead, iso, 40.0, reachable)


def test_single_open_window_from_now():
    pts = [_pt(0, True), _pt(24, True), _pt(48, False), _pt(72, False)]
    w = reachable_windows(pts)
    assert len(w) == 1
    assert w[0].open_now and w[0].start_lead_h == 0 and w[0].end_lead_h == 24


def test_two_disjoint_windows():
    pts = [_pt(0, True), _pt(24, False), _pt(48, True), _pt(72, True), _pt(96, False)]
    w = reachable_windows(pts)
    assert len(w) == 2
    assert w[0].open_now
    assert not w[1].open_now and w[1].start_lead_h == 48 and w[1].end_lead_h == 72


def test_no_window():
    pts = [_pt(0, False), _pt(24, False)]
    assert reachable_windows(pts) == []


def test_window_open_at_horizon_end():
    pts = [_pt(0, False), _pt(24, True), _pt(48, True)]
    w = reachable_windows(pts)
    assert len(w) == 1 and w[0].end_lead_h == 48


def test_summarize_open_now_then_closes():
    pts = [_pt(0, True), _pt(24, True), _pt(48, False)]
    s = summarize(pts, reachable_windows(pts))
    assert "open now" in s and "closes" in s


def test_summarize_no_window():
    pts = [_pt(0, False), _pt(24, False)]
    assert "no reachable window" in summarize(pts, reachable_windows(pts))


def test_summarize_opens_later():
    pts = [_pt(0, False), _pt(24, False), _pt(48, True), _pt(72, True)]
    s = summarize(pts, reachable_windows(pts))
    assert "opens" in s and "still open" in s
