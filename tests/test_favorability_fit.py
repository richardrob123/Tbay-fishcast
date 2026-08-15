"""Statistics for the upwelling recalibration, and the two traps that fired (ADR-058)."""
from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from tbay_fishcast.features import favorability_fit as ff

ROOT = Path(__file__).resolve().parents[1]


def test_spearman_is_rank_based_and_survives_a_monotone_warp():
    x = list(range(1, 200))
    y = [v ** 3 for v in x]                     # monotone but wildly non-linear
    assert ff.spearman(x, y)["rho"] == 1.0
    assert ff.spearman(x, [-v for v in y])["rho"] == -1.0


def test_ties_are_averaged_not_arbitrary():
    """Satellite values repeat at 0.01 C, so ties are the common case, not an edge case."""
    assert ff._rank([5.0, 5.0, 5.0]) == [2.0, 2.0, 2.0]
    assert ff._rank([1.0, 2.0, 2.0, 3.0]) == [1.0, 2.5, 2.5, 4.0]


def test_spearman_refuses_a_thin_sample():
    assert ff.spearman([1, 2, 3], [1, 2, 3])["rho"] is None


def test_auc_is_a_half_for_a_useless_score():
    random.seed(7)
    labels = [i % 2 == 0 for i in range(400)]
    scores = [random.random() for _ in range(400)]
    assert 0.42 < ff.auc(scores, labels) < 0.58


def test_auc_is_one_for_a_perfect_separator():
    labels = [False] * 50 + [True] * 50
    assert ff.auc(list(range(100)), labels) == 1.0


def test_partial_correlation_removes_a_shared_driver():
    """THE CONFOUND. West wind drives upwelling AND brings cold air; cold air cools the surface
    on its own. Without this control a wind-vs-cooling correlation proves nothing."""
    random.seed(11)
    z = [random.gauss(0, 1) for _ in range(500)]
    x = [v + random.gauss(0, 0.3) for v in z]
    y = [v + random.gauss(0, 0.3) for v in z]
    assert ff.spearman(x, y)["rho"] > 0.8
    assert abs(ff.partial_spearman(x, y, z)["rho"]) < 0.15


def test_partial_correlation_keeps_a_genuine_link():
    random.seed(12)
    z = [random.gauss(0, 1) for _ in range(500)]
    x = [random.gauss(0, 1) for _ in range(500)]
    y = [a + c + random.gauss(0, 0.2) for a, c in zip(x, z)]
    assert ff.partial_spearman(x, y, z)["rho"] > 0.7


def test_a_p_value_of_exactly_zero_is_the_strongest_result_not_a_missing_one():
    """THE TRAP THAT FIRED. `(p or 1) < 0.001` reads a p of 0.0 — the strongest possible
    result — as 1, because 0.0 is falsy. It inverted this analysis's headline verdict, printing
    "NO SIGNAL" over rho = -0.13 at p ~ 0. Never use `or` to default a numeric that can be zero.
    """
    x = list(range(1, 400))
    y = [v * 2 for v in x]
    p = ff.spearman(x, y)["p_two_sided"]
    assert p == 0.0
    assert (p or 1) == 1, "the trap itself — kept so the test documents why the guard exists"

    def _p(v):
        return 1.0 if v is None else float(v)

    assert _p(p) < 0.001 and _p(None) == 1.0


def test_a_non_discriminating_logistic_is_refused_not_reported():
    """A degenerate MLE is how the ORIGINAL calibration produced `s50=425.4` for a quantity in
    knots: with no separation the slope collapses and -a/b explodes. Refusing beats reporting."""
    random.seed(13)
    x = [random.gauss(10, 3) for _ in range(600)]
    labels = [random.random() < 0.05 for _ in range(600)]     # label independent of x
    fit = ff.fit_logistic(x, labels)
    assert fit is None or abs(fit[0]) > 50, "a meaningless fit must not look like a real s50"


def test_logistic_recovers_a_known_curve():
    random.seed(14)
    s50, width = 13.0, 3.0
    xs, ys = [], []
    for _ in range(4000):
        v = random.uniform(0, 30)
        p = 1.0 / (1.0 + pow(2.718281828, -(v - s50) / width))
        xs.append(v)
        ys.append(random.random() < p)
    got = ff.fit_logistic(xs, ys)
    assert got is not None
    assert abs(got[0] - s50) < 1.5 and abs(got[1] - width) < 1.0


def test_the_noise_floor_refuses_a_thin_sample():
    assert ff.noise_floor([0.1] * 10) is None
    assert ff.noise_floor([0.0] * 40) == 0.0


def test_an_event_count_bar_exists_and_is_meaningful():
    """The bar cuts a 3% tail, so EVENTS are the sample size. A held-out AUC computed on 7 of
    them was nearly published as a finding — the same error ADR-057 was written to prevent."""
    assert ff.MIN_EVENTS >= 20


def test_the_published_calibration_withholds_what_it_cannot_support():
    p = ROOT / "data" / "calib" / "upwelling_favorability_local.json"
    if not p.exists():
        pytest.skip("local calibration has not been run in this checkout")
    d = json.loads(p.read_text())
    assert d["status"].startswith("MEASUREMENT ONLY"), "a curve change needs sign-off (rule 11)"
    for name, e in d["logistic_fits"].items():
        if e["events_holdout"] < ff.MIN_EVENTS:
            assert e["auc_holdout"] is None, f"{name} published an AUC on too few events"
            assert e["auc_holdout_withheld"]
    # a detected mechanism and a usable curve are separate claims and must not be conflated
    if not d["recommend_recalibrate"]:
        assert "prior stands" in d["verdict"]


# --- the falsy-zero class, as a repo-wide invariant (ADR-060) ---------------------------------

def test_no_surface_temperature_or_gradient_is_gated_on_truthiness():
    """A 0.0 C Lake Superior surface reading is REAL (and is what GLSEA reports over ice), and a
    0.0 C/m gradient is a measured isothermal column. Both were being read as "missing".

    The sweep that found this showed the tell: the SAME expression was already correct in
    build_coast_site.py and forecast_window.py and wrong in build_dynamic_map.py and
    backtest_upwelling.py — copy-drift, fixed in two places and missed in two. This test exists
    so the next copy cannot drift back.
    """
    import re
    # NAMES THAT HOLD A NUMBER. Bare `g` is excluded deliberately: it is used for both a
    # temperature (backtest_upwelling.py) and for the fetch RESULT OBJECT
    # (accumulate_forecast_gate.py:126, `g.sst_c if g else None`). Guarding an object against
    # None with truthiness is correct; guarding a measurement that way is not, and a test that
    # cannot tell them apart would either miss real bugs or train people to ignore it.
    NUMERIC = r"(g_sst|surf_sst|sst_c|gradient_c_per_m|p\.gradient_c_per_m|temp_c|bottom_c)"
    bad = []
    for p in sorted(ROOT.glob("scripts/*.py")) + sorted(ROOT.rglob("src/tbay_fishcast/**/*.py")):
        for i, line in enumerate(p.read_text().splitlines(), 1):
            m = re.search(r"if\s+" + NUMERIC + r"\s+else", line)
            if not m or "is not None" in line:
                continue
            name = m.group(1)
            # `X.attr if X else ...` is an OBJECT guard, not a numeric one.
            if re.search(re.escape(name) + r"\s*\.", line):
                continue
            bad.append(f"{p.relative_to(ROOT)}:{i}: {line.strip()}")
    assert not bad, "measured quantities gated on truthiness (zero is real here):\n" + "\n".join(bad)
