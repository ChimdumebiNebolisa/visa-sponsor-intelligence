"""Read-only query boundary for the employer explorer user interface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

EvidenceStatus = Literal["UNKNOWN"]

EVIDENCE_DISCLAIMER = (
    "This product reports historical and official evidence. It does not provide legal advice "
    "or guarantee sponsorship for a particular person or role."
)


@dataclass(frozen=True, slots=True)
class ExplorerStatus:
    """Current availability state for the explorer presentation layer."""

    phase: str
    build_id: str
    data_available: bool
    evidence_status: EvidenceStatus
    message: str
    disclaimer: str


@runtime_checkable
class ExplorerService(Protocol):
    """Contract that keeps presentation code independent of analytical storage."""

    def get_status(self) -> ExplorerStatus:
        """Return data availability and evidence semantics for the current build."""

        ...


class FoundationExplorerService:
    """Phase 0 implementation that never fabricates unavailable evidence."""

    def get_status(self) -> ExplorerStatus:
        return ExplorerStatus(
            phase="Phase 0",
            build_id="foundation",
            data_available=False,
            evidence_status="UNKNOWN",
            message="No source data has been ingested. Evidence remains UNKNOWN.",
            disclaimer=EVIDENCE_DISCLAIMER,
        )


def get_explorer_service() -> ExplorerService:
    """Construct the current read-only explorer service."""

    return FoundationExplorerService()
