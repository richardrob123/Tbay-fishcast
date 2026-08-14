"""The scalar the upwelling layer actually depends on, computed identically from obs and forecast.

ADR-055. Scoring a wind forecast on raw SPEED error answers the wrong question. The product does
not claim a wind speed; it claims upwelling, and upwelling responds to the *west-quadrant*
component sustained over a *window*. A forecast can be 3 kt off on speed and still call the
mechanism perfectly, or be 1 kt off and get the direction wrong enough to invert the answer.

So two derived quantities, and NOTHING here is a new tuned number — every constant is imported
from the module that already owned it, so the gate cannot drift from the layer it is testing:

  * FAVOURABLE COMPONENT (kt, signed). The wind projected onto the upwelling-favorable axis.
    Positive = upwelling-favorable, negative = downwelling-favorable. Continuous, so it degrades
    gracefully instead of flipping at a sector edge, and its SIGN is the thing the map's headline
    claim rests on. The axis is the midpoint of `wind.FAVORABLE_SECTOR`, not a fresh constant.

  * DRIVE (kt*h). The trailing-window integral of IN-SECTOR WIND SPEED — the actual physical
    precondition for upwelling, and precisely what `wind.favorable_wind_run` already computes for
    the product. Reused rather than reimplemented, deliberately: the gate must test the
    definition that SHIPS, so if the product's notion of a favorable wind run is wrong, this
    measures the wrong thing in the same way and a fix moves both together.

    NOTE that drive is therefore NOT the integral of the component above — it hard-gates on the
    sector and integrates full speed, where the component projects continuously. They are two
    different views on purpose (one is the product's, one degrades gracefully), and an earlier
    draft of this docstring claimed drive was the integral of the component. It is not.

The EVENT is drive crossing the Wedderburn bar sustained across the window, derived as
`upwelling_phase.OBSERVED_THRESHOLD_KN * WINDOW_H`: a window held at exactly the threshold. It is
arithmetic on two existing constants, not a third picked one.
"""
from __future__ import annotations

import math

from .upwelling_phase import OBSERVED_THRESHOLD_KN
from .wind import FAVORABLE_SECTOR, favorable_wind_run

WINDOW_H = 24.0

# Midpoint of the favorable sector, wrap-safe. For (225, 315) this is 270 deg — due west — which
# is the axis the seed corpus names for offshore Ekman transport on the city/north shore.
_lo, _hi = FAVORABLE_SECTOR
FAVORABLE_AXIS_DEG = ((_lo + ((_hi - _lo) % 360.0) / 2.0) % 360.0)

# A window held at exactly the sustained-blow threshold. Derived, not chosen.
EVENT_DRIVE_KTH = OBSERVED_THRESHOLD_KN * WINDOW_H


def favorable_component(speed_kn: float | None, dir_deg: float | None) -> float | None:
    """Signed kt along the upwelling-favorable axis. None when the wind is unknown.

    A CALM (speed 0 with no reported direction) is a real observation of zero drive, not a gap —
    dropping calms would delete exactly the quiescent hours that make a blow a blow.
    """
    if speed_kn is None or not math.isfinite(speed_kn):
        return None
    if dir_deg is None:
        return 0.0 if speed_kn == 0 else None
    if not math.isfinite(dir_deg):
        return None
    return speed_kn * math.cos(math.radians(dir_deg - FAVORABLE_AXIS_DEG))


def drive_series(times, speed_kn, dir_deg, *, window_h: float = WINDOW_H):
    """-> {utc_datetime: drive_kt_h}, the trailing favorable wind-run integral at each time.

    Delegates to `wind.favorable_wind_run`, the function the product itself uses, so the gate can
    never test a different definition of "favorable wind run" than the one that ships.
    """
    # A calm arrives as (0 kt, direction None). It must be KEPT as a zero-speed hour, not
    # dropped: the integral is trapezoidal over the hours present, so deleting the quiet hours
    # inside a window shrinks the window and inflates the drive of every blow that follows one.
    # Direction is arbitrary at zero speed, so 0.0 stands in without changing the integral.
    rows = [(t, s, (0.0 if (d is None and s == 0) else d))
            for t, s, d in zip(times, speed_kn, dir_deg) if s is not None]
    rows = [r for r in rows if r[2] is not None]
    if len(rows) < 2:
        return {}
    rows.sort(key=lambda r: r[0])
    t = [r[0] for r in rows]
    run = favorable_wind_run(t, [r[1] for r in rows], [r[2] for r in rows], window_h=window_h)
    return {tt: float(v) for tt, v in zip(t, run)}


def is_event(drive_kth: float | None, *, bar: float = EVENT_DRIVE_KTH) -> bool | None:
    """Did the trailing window carry enough favorable wind to drive upwelling?"""
    if drive_kth is None or not math.isfinite(drive_kth):
        return None
    return drive_kth >= bar
