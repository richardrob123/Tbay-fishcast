"""Temporal-split assignment (ADR-004/018, CLAUDE rule 6): a date is tune,
validate, or neither — never both, so a threshold can't be tuned on reported data.
"""
from datetime import date


def test_split_windows_loaded(config):
    ts = config.temporal_split
    assert ts.tune.start == date(2024, 3, 26)
    assert ts.validate.start == date(2025, 1, 1)


def test_assign_tune_validate_none(config):
    ts = config.temporal_split
    assert ts.assign(date(2024, 7, 1)) == "tune"
    assert ts.assign(date(2025, 8, 4)) == "validate"
    assert ts.assign(date(2026, 6, 1)) == "validate"
    assert ts.assign(date(2023, 1, 1)) is None  # pre-archive: neither


def test_tune_and_validate_do_not_overlap(config):
    ts = config.temporal_split
    assert ts.tune.end < ts.validate.start  # no leakage across the boundary
