"""Ingest layer — pure Python, deterministic, no LLM (ADR-001).

LSOFS (S3 byte-range) is live and load-bearing in Phase 0. Reference ingests
(ERA5 wind, GLSEA SST, HYDAT flows, NDBC Slate buoy) are hosts currently blocked
by this environment's egress policy; their clients are written to the real API
shape and raise SourceUnavailable until the host is allowlisted.
"""


class SourceUnavailable(RuntimeError):
    """Raised when a data source cannot be reached (network/egress policy).

    Distinct from data errors so callers can degrade loudly rather than silently
    (CLAUDE rule 5: staleness is loud)."""
