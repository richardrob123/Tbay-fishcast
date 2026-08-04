"""Scoring layer. Phase 0 ships ONLY the safety-critical regs gate (ADR-007).

The forecast cube, priors, and lift-vs-climatology scoring are Phase 3 and are
intentionally absent here (NOT_BUILDING: nothing beyond the current phase). The
regs gate is present now because "the system must be incapable of recommending
closed or prohibited water" is an invariant, tested from day one.
"""
