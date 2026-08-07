"""Deterministic dawn/dusk low-light windows — the WHEN dimension of shore catch.

The spatial map answers WHERE conditions match a species; the upwelling-phase banner answers WHEN
(over days) a blow sets up. This module answers the OTHER when: the crepuscular light windows within
a single day. Low light — the civil-twilight-to-shortly-after transitions at dawn and dusk — is the
single strongest intraday cue for shore catch, and it is DECISIVE for exactly the species the thermal
model underserves: the weak-temperature-cue migratory fish (salmon, steelhead) and shallow-holding
coasters, which move onto shore structure and river mouths at first and last light and drop off in
bright midday. `knowledge/schemas/species_rules.yaml` already encodes this as window_multipliers
(dawn ≈1.6, dusk ≈2.0, day ≈0.7, night ≈1.3); this is the deterministic clock behind those windows.

It is PURE ASTRONOMY — sun altitude from latitude/longitude/date via the NOAA solar-position
equations (Meeus). ZERO fetch, no model, no LLM (ADR-001); the sun's geometry is not a data source
that can go stale or be rate-limited. What it is NOT: a claim that fish bite harder at dawn HERE by
some measured factor — the effect size is a cited behavioral prior (species_rules.yaml), honestly
labelled, not locally calibrated. This module supplies only the times; the product presents them as
a timing prior, the same honesty stance as the upwelling-phase banner.

Definitions used (all standard):
- civil twilight  = sun geometric altitude -6°  (enough light to fish without a lamp)
- sunrise/sunset  = sun altitude -0.833°         (standard refraction + solar semidiameter)
The "prime low-light window" is civil-dawn → (sunrise + PRIME_AFTER) in the morning and
(sunset - PRIME_BEFORE) → civil-dusk in the evening — the transition an angler actually fishes.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

# How far past sunrise / before sunset the prime crepuscular bite typically runs. Behavioral prior
# (species_rules.yaml peaks at dawn/dusk, not at the twilight edges); bounded round numbers, labelled.
PRIME_AFTER = timedelta(minutes=45)    # morning window runs civil-dawn → sunrise+45m
PRIME_BEFORE = timedelta(minutes=45)   # evening window runs sunset-45m → civil-dusk

_CIVIL_ALT = -6.0        # deg — civil twilight
_RISESET_ALT = -0.833    # deg — refraction-corrected sunrise/sunset


@dataclass(frozen=True)
class LightWindows:
    """All times are timezone-aware UTC datetimes; None where the event does not occur that day
    (polar edge cases — irrelevant at Thunder Bay's 48°N but handled rather than crashing)."""
    civil_dawn: datetime | None
    sunrise: datetime | None
    sunset: datetime | None
    civil_dusk: datetime | None

    @property
    def morning_prime(self) -> tuple[datetime, datetime] | None:
        if self.civil_dawn is None or self.sunrise is None:
            return None
        return (self.civil_dawn, self.sunrise + PRIME_AFTER)

    @property
    def evening_prime(self) -> tuple[datetime, datetime] | None:
        if self.sunset is None or self.civil_dusk is None:
            return None
        return (self.sunset - PRIME_BEFORE, self.civil_dusk)


def _sun_alt_at(jd_utc: float, lat: float, lon: float) -> float:
    """Geometric solar altitude (deg) at Julian date jd_utc for (lat, lon). NOAA/Meeus
    low-precision (~0.01° / ~1 min) — far finer than any fishing use."""
    n = jd_utc - 2451545.0                        # days since J2000.0
    L = (280.460 + 0.9856474 * n) % 360.0         # mean longitude (deg)
    g = math.radians((357.528 + 0.9856003 * n) % 360.0)  # mean anomaly
    lam = math.radians(L + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g))  # ecliptic longitude
    eps = math.radians(23.439 - 0.0000004 * n)    # obliquity
    decl = math.asin(math.sin(eps) * math.sin(lam))  # declination
    # equation of time (minutes) from the mean-longitude / right-ascension difference
    ra = math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam))
    ra_deg = math.degrees(ra) % 360.0
    L_deg = L % 360.0
    eqt_min = 4.0 * ((L_deg - ra_deg + 540.0) % 360.0 - 180.0)  # minutes
    # true solar time → hour angle
    frac_day = (jd_utc + 0.5) - math.floor(jd_utc + 0.5)  # UTC fraction of day
    utc_min = frac_day * 1440.0
    tst_min = (utc_min + eqt_min + 4.0 * lon) % 1440.0
    ha = math.radians(tst_min / 4.0 - 180.0)      # hour angle (deg→rad)
    latr = math.radians(lat)
    sin_alt = math.sin(latr) * math.sin(decl) + math.cos(latr) * math.cos(decl) * math.cos(ha)
    return math.degrees(math.asin(max(-1.0, min(1.0, sin_alt))))


def _events_around_noon(d: date, lat: float, lon: float, alt_deg: float):
    """Return (morning_crossing_utc_jd, evening_crossing_utc_jd) where the sun crosses `alt_deg`,
    scanning a 24 h window CENTRED on the location's solar noon (not the UTC calendar day — at
    Thunder Bay's western longitude the evening events fall in the next UTC day, so a UTC-day scan
    would miss sunset). Either is None if no crossing on that side (polar edge). ~1 min accuracy."""
    # Julian day number at 00:00 UTC of the calendar date d.
    y, m, day = d.year, d.month, d.day
    if m <= 2:
        y -= 1
        m += 12
    a = y // 100
    b = 2 - a + a // 4
    jd0 = math.floor(365.25 * (y + 4716)) + math.floor(30.6001 * (m + 1)) + day + b - 1524.5
    # Approx solar noon (UTC) for this longitude; 4 min/deg east shifts noon earlier.
    noon_jd = jd0 + 0.5 - lon / 360.0
    start_jd = noon_jd - 0.5                       # 24 h window centred on solar noon
    morning = evening = None
    prev_jd = start_jd
    prev = _sun_alt_at(prev_jd, lat, lon) - alt_deg
    for minute in range(1, 1441):
        jd = start_jd + minute / 1440.0
        cur = _sun_alt_at(jd, lat, lon) - alt_deg
        if prev * cur < 0.0:
            frac = prev / (prev - cur)             # linear interpolation of the crossing
            cross = prev_jd + (jd - prev_jd) * frac
            if cross <= noon_jd:
                morning = cross
            else:
                evening = cross
        prev_jd, prev = jd, cur
    return morning, evening


def _jd_to_utc(jd: float) -> datetime:
    secs = (jd - 2440587.5) * 86400.0
    return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=round(secs))


def compute(d: date, lat: float, lon: float) -> LightWindows:
    """Civil dawn, sunrise, sunset, civil dusk (UTC) for a location and local calendar date."""
    dawn, dusk = _events_around_noon(d, lat, lon, _CIVIL_ALT)
    sunrise, sunset = _events_around_noon(d, lat, lon, _RISESET_ALT)
    return LightWindows(
        civil_dawn=_jd_to_utc(dawn) if dawn is not None else None,
        sunrise=_jd_to_utc(sunrise) if sunrise is not None else None,
        sunset=_jd_to_utc(sunset) if sunset is not None else None,
        civil_dusk=_jd_to_utc(dusk) if dusk is not None else None,
    )
