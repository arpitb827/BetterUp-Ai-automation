"""
core/models/events.py — Typed event envelope for all incoming webhooks.

Every change coming from Ashby or Workday is wrapped in a ChangeEvent.
The orchestrator pipeline receives ChangeEvent objects and never raw dicts.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field
import hashlib
import json


# ── Event Types ───────────────────────────────────────────────────────────────

class EventType(str, Enum):
    """All event types the system can receive from source systems."""

    # Ashby events
    ASHBY_HIRE_CREATED   = "ASHBY_HIRE_CREATED"    # New offer accepted in Ashby
    ASHBY_HIRE_UPDATED   = "ASHBY_HIRE_UPDATED"    # Any field changed in Ashby
    ASHBY_HIRE_CANCELLED = "ASHBY_HIRE_CANCELLED"  # Offer rescinded

    # Workday events
    WORKDAY_EMPLOYEE_CREATED = "WORKDAY_EMPLOYEE_CREATED"  # Off-cycle: direct in Workday
    WORKDAY_EMPLOYEE_UPDATED = "WORKDAY_EMPLOYEE_UPDATED"  # HR edits in Workday


class EventSource(str, Enum):
    """Which system sent the event."""
    ASHBY   = "ASHBY"
    WORKDAY = "WORKDAY"


# ── Change Field Descriptors ──────────────────────────────────────────────────

class RiskLevel(str, Enum):
    """
    Risk level of a change — determines routing:
    LOW    → sync immediately, no review needed
    MEDIUM → sync, but notify People Ops
    HIGH   → pause, run through Claude resolver if conflict detected
    """
    LOW    = "LOW"
    MEDIUM = "MEDIUM"
    HIGH   = "HIGH"


# Maps each HireState field to its risk level when changed
FIELD_RISK_MAP: dict[str, RiskLevel] = {
    "preferred_name":              RiskLevel.LOW,
    "laptop_model":                RiskLevel.LOW,
    "cohort_added_to_slack":       RiskLevel.LOW,
    "day1_orientation_scheduled":  RiskLevel.LOW,

    "job_title":                   RiskLevel.MEDIUM,
    "department":                  RiskLevel.MEDIUM,
    "team":                        RiskLevel.MEDIUM,
    "manager_email":               RiskLevel.MEDIUM,
    "start_date":                  RiskLevel.MEDIUM,
    "work_email":                  RiskLevel.MEDIUM,

    "legal_first_name":            RiskLevel.HIGH,
    "legal_last_name":             RiskLevel.HIGH,
    "personal_email":              RiskLevel.HIGH,
    "shipping_address":            RiskLevel.HIGH,
    "workday_id":                  RiskLevel.HIGH,
}


def risk_for_field(field_name: str) -> RiskLevel:
    """Look up risk level for a changed field. Defaults to MEDIUM if unknown."""
    return FIELD_RISK_MAP.get(field_name, RiskLevel.MEDIUM)


# ── The ChangeEvent Envelope ──────────────────────────────────────────────────

class ChangeEvent(BaseModel):
    """
    The normalized event envelope for all incoming webhooks.

    Every change from Ashby or Workday must be wrapped in a ChangeEvent
    before it touches the orchestration pipeline. This ensures:
    - A consistent shape for idempotency key generation
    - Separation of raw webhook payload from business logic
    - Full audit traceability (source, timestamp, type, hire_id)
    """

    event_id: str = Field(..., description="Unique event identifier from the source system")
    event_type: EventType
    source: EventSource
    hire_id: str = Field(..., description="The hire_id this event relates to")
    received_at: datetime = Field(default_factory=datetime.utcnow)
    payload: dict[str, Any] = Field(
        ...,
        description="Raw webhook payload from the source system. "
                    "The reconciler extracts fields from here."
    )
    idempotency_key: Optional[str] = Field(
        None,
        description="Auto-computed SHA256 key on creation. Used to deduplicate replayed events."
    )

    def model_post_init(self, __context: Any) -> None:
        """Auto-compute idempotency key if not explicitly set."""
        if not self.idempotency_key:
            self.idempotency_key = self._compute_idempotency_key()

    def _compute_idempotency_key(self) -> str:
        """
        SHA256( hire_id + event_type + sorted_payload_json ).
        Stable: same logical change from the same source always produces the same key,
        even if the webhook is delivered multiple times.
        """
        stable_payload = json.dumps(self.payload, sort_keys=True, default=str)
        raw = f"{self.hire_id}::{self.event_type.value}::{stable_payload}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def __repr__(self) -> str:
        return (
            f"ChangeEvent(event_id={self.event_id!r}, "
            f"type={self.event_type.value}, "
            f"hire_id={self.hire_id!r}, "
            f"key={self.idempotency_key[:12]}...)"  # type: ignore[index]
        )
