"""Seasonal regime — the context layer that says what drives shore catch THIS time of year.

The fish-behavior review (docs/FISH_BEHAVIOR_REVIEW.md, "Seasonal regimes") is explicit that the
target shifts by season, and that the map's cold-reachability-plus-edge model is a SUMMER-
stratified regime. Presenting it year-round without that context would overstate it in spring
(whole shore already cold — upwelling matters least) and miss the fall shoal-staging window.

This is DIRECTION only, from the review — not fabricated spatial parameters. It returns a regime
label + guidance + how much weight to give the upwelling/edge signal, shown as a context badge.
The spatial zones themselves are unchanged; the badge tells the angler how to read them now.
Pure, date-only, no I/O, no LLM (ADR-001)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Regime:
    season: str        # winter | spring | summer | fall
    label: str         # short human label
    upwelling: str     # 'primary' | 'secondary' | 'minimal' — how much the edge/upwelling signal drives catch now
    note: str          # one-line guidance


def regime(d: date) -> Regime:
    """Seasonal regime for NW Lake Superior shore fishing, by month (review-backed)."""
    m = d.month
    if m in (1, 2, 3, 4):
        return Regime("winter", "Winter / ice", "minimal",
                      "Hard-water or just-opening shore — the open-water thermal model barely applies; check ice & regs.")
    if m in (5, 6):
        return Regime("spring", "Spring (cold shoulder)", "minimal",
                      "Whole shore is still cold, so upwelling matters least — target river plumes, warming bays and structure.")
    if m in (7, 8, 9):
        return Regime("summer", "Summer (stratified)", "primary",
                      "The model's core regime: cold-reachability + the thermal/structure edge predict opportunity; reward the relaxation after a blow.")
    if m in (10, 11):
        return Regime("fall", "Fall (turnover / staging)", "secondary",
                      "Best shore window: lake trout stage on rocky shoals (~8–11 °C), cooling/photoperiod-driven — fish shallow structure; upwelling is secondary.")
    return Regime("winter", "Early winter", "minimal",
                  "Water is turning over and cooling toward ice — shore season winding down; structure over isotherm.")
