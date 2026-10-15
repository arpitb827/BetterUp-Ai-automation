"""
core/models/hire_state.py — Canonical HireState schema.

This is the single source of truth for a hire record across all systems.
Every connector reads from / writes to a normalized HireState object.
No connector ever talks to another connector directly.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, field_validator, model_validator
import re


# ── Enums ────────────────────────────────────────────────────────────────────

class HireStatus(str, Enum):
    """Top-level lifecycle status of a hire record."""
    DISCOVERED         = "DISCOVERED"         # Event received, not yet validated
    VALIDATING         = "VALIDATING"         # Schema + cross-system checks in progress
    PENDING_SYNC       = "PENDING_SYNC"       # Validated, waiting to sync downstream
    SYNCING            = "SYNCING"            # Actively syncing to downstream systems
    CONFLICT_PAUSED    = "CONFLICT_PAUSED"    # Conflict detected, awaiting resolution
    SYNCED             = "SYNCED"             # All downstream systems updated
    DAY1_READY         = "DAY1_READY"         # All gates passed, ready for Day 1
    FAILED             = "FAILED"             # Unrecoverable error — in dead letter queue


class EquipmentStatus(str, Enum):
    """Tracks hardware logistics state."""
    NOT_ORDERED  = "NOT_ORDERED"
    ORDERED      = "ORDERED"
    SHIPPED      = "SHIPPED"
    DELIVERED    = "DELIVERED"
    CANCELLED    = "CANCELLED"


class OktaStatus(str, Enum):
    """Okta provisioning state."""
    NOT_PROVISIONED = "NOT_PROVISIONED"
    PROVISIONED     = "PROVISIONED"
    UPDATED         = "UPDATED"
    DEPROVISIONED   = "DEPROVISIONED"


class HireSource(str, Enum):
    """Which system originated this hire record."""
    ASHBY    = "ASHBY"    # Standard: came through recruiting pipeline
    WORKDAY  = "WORKDAY"  # Off-cycle: created directly in HRIS


# ── Sub-models ───────────────────────────────────────────────────────────────

class Address(BaseModel):
    """Shipping and home address for equipment logistics."""
    street_line_1: str
    street_line_2: Optional[str] = None
    city: str
    state: str                     # 2-letter US state code, e.g. "CA"
    zip_code: str
    country: str = "US"

    @field_validator("state")
    @classmethod
    def validate_state(cls, v: str) -> str:
        if len(v) != 2 or not v.isalpha():
            raise ValueError(f"State must be a 2-letter code, got: '{v}'")
        return v.upper()

    @field_validator("zip_code")
    @classmethod
    def validate_zip(cls, v: str) -> str:
        if not re.match(r"^\d{5}(-\d{4})?$", v):
            raise ValueError(f"Invalid US zip code format: '{v}'")
        return v

    def one_line(self) -> str:
        """Return address as a single string for shipping labels."""
        parts = [self.street_line_1]
        if self.street_line_2:
            parts.append(self.street_line_2)
        parts.append(f"{self.city}, {self.state} {self.zip_code}")
        return ", ".join(parts)


class StageGates(BaseModel):
    """
    Boolean flags for all critical onboarding milestones.
    All must be True before a hire can reach DAY1_READY.
    """
    background_check_complete: bool = False
    it_hardware_survey_submitted: bool = False
    okta_account_provisioned: bool = False
    lumos_access_assigned: bool = False
    equipment_ordered: bool = False
    cohort_added_to_slack: bool = False
    day1_orientation_scheduled: bool = False

    def all_gates_passed(self) -> bool:
        """Returns True only when every gate is satisfied."""
        return all([
            self.background_check_complete,
            self.it_hardware_survey_submitted,
            self.okta_account_provisioned,
            self.lumos_access_assigned,
            self.equipment_ordered,
            self.cohort_added_to_slack,
            self.day1_orientation_scheduled,
        ])

    def pending_gates(self) -> list[str]:
        """Returns list of gate names that are still False."""
        return [
            field
            for field, value in self.model_dump().items()
            if value is False
        ]


# ── Canonical HireState ───────────────────────────────────────────────────────

class HireState(BaseModel):
    """
    The canonical, normalized hire record shared across all systems.

    Design rules:
    - This is the ONLY model connectors read from / write to.
    - Fields use legal names (for compliance), not nicknames.
    - preferred_name is used for Slack/Cohort display only.
    - All updates produce a NEW HireState snapshot; old ones are never mutated.
    """

    # ── Identity ─────────────────────────────────────────────────────────────
    hire_id: str = Field(..., description="Unique hire ID from Ashby or Workday")
    workday_id: Optional[str] = Field(None, description="Workday employee ID once created")
    ashby_id: Optional[str] = Field(None, description="Ashby application/candidate ID")
    source: HireSource = Field(..., description="Which system originated this hire")

    # ── Personal Info ─────────────────────────────────────────────────────────
    legal_first_name: str
    legal_last_name: str
    preferred_name: Optional[str] = None       # Display name for Slack/cohort
    personal_email: str
    work_email: Optional[str] = None           # Generated by Okta on provisioning

    # ── Role ─────────────────────────────────────────────────────────────────
    job_title: str
    department: str
    team: str
    manager_email: str
    start_date: date

    # ── Location & Equipment ──────────────────────────────────────────────────
    shipping_address: Address
    laptop_model: str = "MacBook Pro 14\""    # Default hardware

    # ── System Statuses ───────────────────────────────────────────────────────
    status: HireStatus = HireStatus.DISCOVERED
    okta_status: OktaStatus = OktaStatus.NOT_PROVISIONED
    equipment_status: EquipmentStatus = EquipmentStatus.NOT_ORDERED
    stage_gates: StageGates = Field(default_factory=StageGates)

    # ── Audit Metadata ────────────────────────────────────────────────────────
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    version: int = Field(default=1, description="Increments on every state update")
    conflict_reason: Optional[str] = Field(None, description="Populated when status=CONFLICT_PAUSED")
    error_message: Optional[str] = Field(None, description="Populated when status=FAILED")

    # ── Validators ────────────────────────────────────────────────────────────

    @field_validator("personal_email", "work_email", mode="before")
    @classmethod
    def validate_email(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not re.match(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$", v):
            raise ValueError(f"Invalid email format: '{v}'")
        return v.lower().strip()

    @field_validator("legal_first_name", "legal_last_name")
    @classmethod
    def validate_legal_name(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 1:
            raise ValueError("Legal name cannot be empty")
        if not re.match(r"^[a-zA-Z\s\-'\.]+$", v):
            raise ValueError(
                f"Legal name '{v}' contains invalid characters. "
                "Only letters, spaces, hyphens, apostrophes, and periods are allowed."
            )
        return v

    @field_validator("start_date", mode="before")
    @classmethod
    def validate_start_date(cls, v) -> date:
        if isinstance(v, str):
            return date.fromisoformat(v)
        return v

    @model_validator(mode="after")
    def validate_id_source_consistency(self) -> "HireState":
        """Ashby-sourced hires must have ashby_id; Workday-sourced must have workday_id."""
        if self.source == HireSource.ASHBY and not self.ashby_id:
            raise ValueError("Ashby-sourced hire must have ashby_id set")
        if self.source == HireSource.WORKDAY and not self.workday_id:
            raise ValueError("Workday off-cycle hire must have workday_id set")
        return self

    # ── Helpers ───────────────────────────────────────────────────────────────

    @property
    def display_name(self) -> str:
        """Preferred name if set, otherwise legal first name."""
        first = self.preferred_name or self.legal_first_name
        return f"{first} {self.legal_last_name}"

    @property
    def full_legal_name(self) -> str:
        return f"{self.legal_first_name} {self.legal_last_name}"

    def with_updates(self, **kwargs) -> "HireState":
        """
        Return a new HireState with specified fields updated.
        Automatically bumps version and updated_at timestamp.
        Never mutates the original object.
        """
        data = self.model_dump()
        data.update(kwargs)
        data["version"] = self.version + 1
        data["updated_at"] = datetime.utcnow()
        return HireState(**data)

    def __repr__(self) -> str:
        return (
            f"HireState(hire_id={self.hire_id!r}, "
            f"name={self.full_legal_name!r}, "
            f"status={self.status.value}, "
            f"version={self.version})"
        )
