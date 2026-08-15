"""How much should the upwelling layer be trusted at each lead? (ADR-055)

WHAT THIS REPLACES. The phase timeline used to label its own confidence like this:

    confidence="med" if lead <= 48 else "low"

Two picked numbers and a picked boundary, chosen before anything had been measured, applied
identically to a forecast whose actual error more than doubles across the range they cover.
ADR-055 measured that range against a real anemometer inside Thunder Bay — 20,195 paired hours —
so the label can now be read off the measurement instead.

THE LADDER IS DERIVED, NOT PICKED. Every rung is a comparison already computed by the gate, and
none of them is a magnitude anyone chose:

    "measured"     the block-bootstrap interval clears the ADR-006 bar: this lead is DEMONSTRATED
                   to beat the harder of persistence and climatology, not merely observed to.
    "informative"  it beats observed persistence outright but its interval does not clear the
                   oracle composite. Real information; not a proven win.
    "weak"         it does not beat persistence. Shown, because hiding a weak lead is how a map
                   comes to imply confidence it has never earned, but shown as weak.
    "unmeasured"   no measurement exists at or near this lead. Not a synonym for "fine".

THE LADDER IS NOT AN ACCURACY RANKING, and the field is named `skill_vs_baseline` rather than
`confidence` because on the real data it is not even monotonic: +24 h comes out "informative"
while +48 h and +72 h come out "measured". That is not a defect and it is not a claim that a
one-day forecast is worse than a two-day one — its raw error is the LOWEST of any lead. It is
that persistence is hardest to beat at short lead, so the bar the +24 h forecast has to clear is
the highest one in the table. Collapsing that into a word called "confidence" would tell a reader
the opposite of the truth.

`sign_agreement` is the number a reader should actually act on, and it IS monotonic: the forecast
calls upwelling-versus-downwelling correctly 76% of the time at +24 h and 64% at +120 h, against
a 50% coin. That decay is the honest content of a five-day upwelling map.
"""
from __future__ import annotations

import json
from pathlib import Path

OBSERVED = "observed"
MEASURED = "measured"
INFORMATIVE = "informative"
WEAK = "weak"
UNMEASURED = "unmeasured"

# A lead may be labelled from a nearby measured lead, but not from an arbitrarily distant one:
# the whole point is that error changes with lead. 24 h is the gate's own bin spacing.
MAX_LEAD_GAP_H = 24


def load(path) -> dict | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except ValueError:
        return None


def for_lead(skill: dict | None, lead_h: int, *, observed: bool = False) -> dict:
    """-> {skill_vs_baseline, basis, ...measured numbers} for one forecast lead.

    ``observed=True`` marks the nowcast, which is not a forecast at all and must not borrow a
    forecast's label in either direction.
    """
    if observed:
        return {"skill_vs_baseline": OBSERVED, "basis": "in-situ observation, not a forecast",
                "lead_h": lead_h, "measured_at_lead_h": None}
    per = (skill or {}).get("per_lead") or {}
    if not per:
        return {"skill_vs_baseline": UNMEASURED,
                "basis": "no wind-lead skill measurement available",
                "lead_h": lead_h, "measured_at_lead_h": None}

    exact = per.get(str(lead_h))
    if exact is not None:
        entry, at = exact, lead_h
    else:
        cands = [(abs(int(k) - lead_h), int(k)) for k in per]
        gap, at = min(cands)
        if gap > MAX_LEAD_GAP_H:
            return {"skill_vs_baseline": UNMEASURED,
                    "basis": f"nearest measured lead is {at} h away, too far to speak for "
                             f"{lead_h} h", "lead_h": lead_h, "measured_at_lead_h": None}
        entry = per[str(at)]

    ci = entry.get("ci95") or [None, None]
    hi = ci[1] if len(ci) > 1 else None
    vs_p = entry.get("ratio_vs_persistence")
    if hi is not None and hi < 1.0:
        label, basis = MEASURED, ("interval clears the ADR-006 bar against the harder of "
                                  "persistence and climatology")
    elif vs_p is not None and vs_p < 1.0:
        label, basis = INFORMATIVE, ("beats observed persistence but the interval does not clear "
                                     "the oracle composite baseline")
    else:
        label, basis = WEAK, "does not beat observed persistence at this lead"
    return {"skill_vs_baseline": label, "basis": basis,
            "not_an_accuracy_ranking": (
                "says whether the lead is shown to beat the cheap baseline, NOT how accurate it "
                "is. Persistence is hardest to beat at short lead, so +24 h can rank below "
                "+48 h. Use sign_agreement for accuracy."),
            "lead_h": lead_h, "measured_at_lead_h": at,
            "sign_agreement": entry.get("favorable_sign_agreement"),
            "drive_mae_kth": entry.get("drive_mae_kth"),
            "drive_bias_kth": entry.get("drive_bias_kth"),
            "skill_ratio": entry.get("skill_ratio"), "ci95": ci,
            "ratio_vs_persistence": vs_p}
