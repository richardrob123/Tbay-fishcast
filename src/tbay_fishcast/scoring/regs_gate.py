"""Regs gate — SAFETY-CRITICAL. Loads knowledge/schemas/regs.yaml and answers
"is fishing prohibited at this water on this date?" so no recommendation can ever
surface closed or prohibited water (ADR-007, CLAUDE rule 4).

Design for testability-as-invariant:
  * Closures are DATA-DRIVEN off regs.yaml entries. Each safety-critical closure
    is interpreted by a small structured predicate keyed to the entry `id`; if the
    backing entry is missing from regs.yaml, the gate FAILS CLOSED for that water
    (raises on validate()), never silently opens it.
  * `validate()` enforces the provenance bar: regs entries must be Tier-1 with a
    `verified_on` date. Below-bar entries (e.g. the flagged T2 marina bylaw) are
    returned as blocking violations — the go-live check consumes this.

Phase 2 will formalize FMZ season windows; Phase 0 encodes the spatial/seasonal
hard closures already present in the seed regs so the invariant has teeth now.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from ..config import KNOWLEDGE_DIR

REGS_YAML = KNOWLEDGE_DIR / "schemas" / "regs.yaml"

# Structured interpretation of safety-critical closures, keyed to regs.yaml `id`.
# Water-name matching is substring, case-insensitive. Seasonal windows are
# inclusive month/day ranges. Presence of the backing id is required (fail-closed).
_HARD_CLOSURES = {
    "kakabeka_park_closed": {
        "match": ["kakabeka"],
        "kind": "hard",  # closed year-round
    },
    "mcintyre_sanctuary_spring": {
        "match": ["mcintyre river", "mcintyre sanctuary", "mcintyre footbridge"],
        "kind": "seasonal",
        "closed_start": (4, 1),   # Apr 1
        "closed_end": (5, 31),    # May 31 inclusive
    },
}


@dataclass(frozen=True)
class RegEntry:
    id: str
    scope: dict
    rule: str
    source: str
    verified_on: str | None
    tier: str


def load_regs(path: Path | str = REGS_YAML) -> list[RegEntry]:
    raw = yaml.safe_load(Path(path).read_text())
    out = []
    for e in raw:
        out.append(
            RegEntry(
                id=e["id"],
                scope=e.get("scope", {}),
                rule=e.get("rule", ""),
                source=e.get("source", ""),
                verified_on=str(e["verified_on"]) if e.get("verified_on") else None,
                tier=e.get("tier", ""),
            )
        )
    return out


def _in_window(d: date, start: tuple[int, int], end: tuple[int, int]) -> bool:
    s = date(d.year, *start)
    e = date(d.year, *end)
    return s <= d <= e


class RegsGate:
    def __init__(self, entries: list[RegEntry]):
        self._entries = entries
        self._by_id = {e.id: e for e in entries}

    @classmethod
    def load(cls, path: Path | str = REGS_YAML) -> "RegsGate":
        return cls(load_regs(path))

    def is_prohibited(self, water: str, on: date) -> bool:
        """True if fishing is prohibited at `water` on date `on`.

        Fails CLOSED: if a hard-closure's backing regs entry is missing, matching
        that water still returns True (prohibited) rather than opening it.
        """
        w = (water or "").lower()
        for reg_id, spec in _HARD_CLOSURES.items():
            if not any(m in w for m in spec["match"]):
                continue
            if spec["kind"] == "hard":
                return True
            if spec["kind"] == "seasonal":
                return _in_window(on, spec["closed_start"], spec["closed_end"])
        return False

    def filter_recommendable(self, waters, on: date):
        """Return only waters that are not prohibited on `on`. This is the choke
        point every recommender must pass through."""
        return [w for w in waters if not self.is_prohibited(w, on)]

    def validate(self) -> list[str]:
        """Provenance + integrity violations that must block go-live (ADR-007/008).

        - every regs entry must be Tier-1 with a verified_on date;
        - every hard-closure interpretation must have a backing regs entry.
        """
        violations: list[str] = []
        for e in self._entries:
            if e.tier != "T1":
                violations.append(
                    f"{e.id}: tier {e.tier or '<none>'} < T1 (regs are T1-only, ADR-007)"
                )
            if not e.verified_on:
                violations.append(f"{e.id}: missing verified_on")
        for reg_id in _HARD_CLOSURES:
            if reg_id not in self._by_id:
                violations.append(
                    f"hard-closure '{reg_id}' has no backing regs.yaml entry (fail-closed)"
                )
        return violations
