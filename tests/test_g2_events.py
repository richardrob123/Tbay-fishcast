"""Unit tests for G2 event detection, clustering, and matching (pre-registered rules)."""
from datetime import date, datetime, timedelta, timezone

from tbay_fishcast.verification.g2 import (
    Episode, cluster_episodes, detect_onsets, match_episodes,
)

BASE = datetime(2025, 7, 1, 0, 0, tzinfo=timezone.utc)


def _series(temps):
    return [BASE + timedelta(hours=i) for i in range(len(temps))], temps


def test_sustained_drop_fires_onset():
    # 12 h at 18C, then drop to 13C sustained 8 h (>= persist)
    temps = [18.0] * 12 + [13.0] * 8
    onsets = detect_onsets(*_series(temps), drop_c=4.0, persist_h=6)
    assert len(onsets) == 1
    assert onsets[0] == BASE + timedelta(hours=12)


def test_single_sample_spike_suppressed_by_persistence():
    # a 1-hour cold spike then recovery -> NOT an event (persistence guard)
    temps = [18.0] * 12 + [13.0] + [18.0] * 8
    onsets = detect_onsets(*_series(temps), drop_c=4.0, persist_h=6)
    assert onsets == []


def test_no_persistence_guard_allows_spike():
    temps = [18.0] * 12 + [13.0] + [18.0] * 8
    onsets = detect_onsets(*_series(temps), drop_c=4.0, persist_h=0)
    assert len(onsets) == 1  # with guard off, the spike fires


def test_shallow_drop_below_threshold_ignored():
    temps = [18.0] * 12 + [15.5] * 8  # only 2.5C drop
    assert detect_onsets(*_series(temps), drop_c=4.0, persist_h=6) == []


def test_cluster_merges_within_gap():
    onsets = [datetime(2025, 7, 1, tzinfo=timezone.utc),
              datetime(2025, 7, 2, tzinfo=timezone.utc),
              datetime(2025, 7, 8, tzinfo=timezone.utc)]
    eps = cluster_episodes(onsets, merge_gap_days=1)
    assert len(eps) == 2
    assert eps[0].n_days == 2 and eps[0].onset == date(2025, 7, 1)
    assert eps[1].onset == date(2025, 7, 8)


def test_match_hit_within_tolerance():
    truth = [Episode(date(2025, 7, 5), date(2025, 7, 5), date(2025, 7, 6), 2)]
    det = [Episode(date(2025, 7, 6), date(2025, 7, 6), date(2025, 7, 6), 1)]
    m = match_episodes(truth, det, tau_days=1)
    assert m.hits == 1 and m.misses == 0 and m.false_alarms == 0


def test_match_outside_tolerance_is_miss_and_false_alarm():
    truth = [Episode(date(2025, 7, 5), date(2025, 7, 5), date(2025, 7, 5), 1)]
    det = [Episode(date(2025, 7, 10), date(2025, 7, 10), date(2025, 7, 10), 1)]
    m = match_episodes(truth, det, tau_days=1)
    assert m.hits == 0 and m.misses == 1 and m.false_alarms == 1


def test_match_one_to_one_no_double_count():
    # one truth, two nearby detections -> one hit, one false alarm (not two hits)
    truth = [Episode(date(2025, 7, 5), date(2025, 7, 5), date(2025, 7, 5), 1)]
    det = [Episode(date(2025, 7, 5), date(2025, 7, 5), date(2025, 7, 5), 1),
           Episode(date(2025, 7, 6), date(2025, 7, 6), date(2025, 7, 6), 1)]
    m = match_episodes(truth, det, tau_days=1)
    assert m.hits == 1 and m.false_alarms == 1


def test_pod_far_via_scorecard():
    from tbay_fishcast.verification.scorecard import Contingency
    truth = [Episode(date(2025, 7, d), date(2025, 7, d), date(2025, 7, d), 1) for d in (5, 15, 25)]
    det = [Episode(date(2025, 7, d), date(2025, 7, d), date(2025, 7, d), 1) for d in (5, 15, 20)]
    m = match_episodes(truth, det, tau_days=1)
    c = Contingency(hits=m.hits, misses=m.misses, false_alarms=m.false_alarms, correct_neg=0)
    assert m.hits == 2 and m.misses == 1 and m.false_alarms == 1
    assert abs(c.pod - 2/3) < 1e-9 and abs(c.far - 1/3) < 1e-9
