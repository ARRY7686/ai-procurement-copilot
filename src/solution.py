from __future__ import annotations

from src.agents import run
from src.contracts import Architecture, ProcurementDecision
from src.data_access import get_request


def handle_request(request_id: str, architecture: Architecture = "single") -> ProcurementDecision:
    """Assessment adapter: 'single' = Architecture A, 'staged' = Architecture B (analyst -> reviewer)."""
    decision, _trace = run(get_request(request_id), architecture)
    return decision


def handle_request_payload(request: dict, architecture: Architecture = "single") -> tuple[ProcurementDecision, dict]:
    """Same pipeline for an ad-hoc request (UI form); also returns the run trace."""
    return run(request, architecture)
