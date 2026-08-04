"""tbay-fishcast — deterministic fishing-conditions hindcast/forecast for Thunder Bay.

Phase 0: hindcast validation. No LLM in this package — ingest/features/scoring are
pure, deterministic Python (ADR-001). LLMs live at the edges (brief writing, repair
PRs, research subagents, calibration review) and never import from here at runtime.
"""

__version__ = "0.0.0"  # Phase 0, pre-commissioning
