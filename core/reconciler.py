"""
core/reconciler.py — Diff engine: compares old vs. new HireState, detects conflicts.

The reconciler is the core business logic layer. It answers three questions:
  1. What fields changed?
  2. What is the risk level of each change?
  3. Is there a conflict that requires special handling (Claude or human review)?

Design Rules:
  - Reconciler is PURE — no I/O, no side effects.
  - It never touches connectors. It only produces a HireStateDiff.
  - The orchestrator decides what to do with the diff.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from core.models.hire_state import HireState, HireStatus, EquipmentStatus
from core.models.events import RiskLevel, risk_for_field


# ── ChangedField ──────────────────────────────────────────────────────────────

@dataclass
class ChangedField:
    """Represents a single field that changed between two HireState snapshots."""
    field_name: str
    old_value: Any
    new_value: Any
    risk_level: RiskLevel = field(init=False)

    def __post_init__(self):
        self.risk_level = risk_for_field(self.field_name)

    def __repr__(self) -> str:
        return (
            f"ChangedField({self.field_name}: "
            f"{self.old_value!r} → {self.new_value!r}, "
            f"risk={self.risk_level.value})"
        )


# ── ConflictType ──────────────────────────────────────────────────────────────

class ConflictType(str):
    """String constants for known conflict scenarios."""
    ADDRESS_CHANGED_POST_SHIP    = "ADDRESS_CHANGED_POST_SHIP"
    START_DATE_WITHIN_72H        = "START_DATE_WITHIN_72H"
    LEGAL_NAME_MISMATCH          = "LEGAL_NAME_MISMATCH"
    MULTIPLE_HIGH_RISK_CHANGES   = "MULTIPLE_HIGH_RISK_CHANGES"


# ── HireStateDiff ─────────────────────────────────────────────────────────────

@dataclass
class HireStateDiff:
    """
    The result of comparing two HireState snapshots.

    Attributes:
        hire_id:          The hire this diff belongs to.
        changed_fields:   List of all fields that changed.
        has_conflict:     True if a known conflict pattern was detected.
        conflict_type:    The type of conflict (if any).
        conflict_detail:  Human-readable description of the conflict.
        highest_risk:     The highest risk level across all changed fields.
        is_no_op:         True if nothing changed.
    """
    hire_id: str
    changed_fields: list[ChangedField]
    has_conflict: bool = False
    conflict_type: Optional[str] = None
    conflict_detail: Optional[str] = None

    @property
    def is_no_op(self) -> bool:
        return len(self.changed_fields) == 0

    @property
    def highest_risk(self) -> Optional[RiskLevel]:
        if not self.changed_fields:
            return None
        order = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}
        return max(self.changed_fields, key=lambda f: order[f.risk_level]).risk_level

    def fields_by_risk(self, risk: RiskLevel) -> list[ChangedField]:
        """Return all changed fields matching a given risk level."""
        return [f for f in self.changed_fields if f.risk_level == risk]

    def field_names(self) -> list[str]:
        return [f.field_name for f in self.changed_fields]

    def __repr__(self) -> str:
        return (
            f"HireStateDiff(hire_id={self.hire_id!r}, "
            f"changes={self.field_names()}, "
            f"highest_risk={self.highest_risk}, "
            f"conflict={self.has_conflict})"
        )


# ── Reconciler ────────────────────────────────────────────────────────────────

class Reconciler:
    """
    Compares two HireState snapshots and returns a HireStateDiff.

    Usage:
        reconciler = Reconciler()
        diff = reconciler.diff(old_state, new_state)
        if diff.has_conflict:
            # Route to Claude resolver
        elif not diff.is_no_op:
            # Route to dispatcher
    """

    # Fields excluded from diff (metadata managed internally, not from webhooks)
    _EXCLUDED_FIELDS = {
        "status", "version", "created_at", "updated_at",
        "conflict_reason", "error_message", "okta_status", "equipment_status",
    }

    def diff(self, old: HireState, new: HireState) -> HireStateDiff:
        """
        Compute the diff between old and new HireState.

        Args:
            old: The previously stored/canonical HireState.
            new: The incoming HireState parsed from the webhook payload.

        Returns:
            HireStateDiff with all changed fields, risk levels, and any conflicts.
        """
        assert old.hire_id == new.hire_id, (
            f"Cannot diff states from different hires: "
            f"{old.hire_id} vs {new.hire_id}"
        )

        changed: list[ChangedField] = []

        old_data = old.model_dump()
        new_data = new.model_dump()

        for field_name in old_data:
            if field_name in self._EXCLUDED_FIELDS:
                continue

            old_val = old_data[field_name]
            new_val = new_data[field_name]

            if old_val != new_val:
                changed.append(ChangedField(
                    field_name=field_name,
                    old_value=old_val,
                    new_value=new_val,
                ))

        diff = HireStateDiff(hire_id=old.hire_id, changed_fields=changed)

        # ── Run conflict detection rules ──────────────────────────────────────
        self._detect_conflicts(old, new, diff)

        return diff

    def _detect_conflicts(
        self,
        old: HireState,
        new: HireState,
        diff: HireStateDiff,
    ) -> None:
        """
        Mutates diff in-place to flag conflicts.
        Runs rules in priority order; stops at first conflict found.

        Conflict Rules (in priority order):
          1. Address changed AND equipment already SHIPPED/DELIVERED
          2. Start date changed AND new date is within 72 hours from now
          3. Legal name changed (first OR last)
          4. Multiple HIGH_RISK fields changed in a single event
        """
        from datetime import date, timedelta

        changed_names = diff.field_names()

        # ── Rule 1: Address changed after laptop shipped ───────────────────────
        if "shipping_address" in changed_names and old.equipment_status in {
            EquipmentStatus.SHIPPED,
            EquipmentStatus.DELIVERED,
        }:
            diff.has_conflict = True
            diff.conflict_type = ConflictType.ADDRESS_CHANGED_POST_SHIP
            diff.conflict_detail = (
                f"Shipping address changed for {old.display_name} "
                f"but equipment is already {old.equipment_status.value}. "
                f"Old address: {old.shipping_address.one_line()}. "
                f"New address: {new.shipping_address.one_line()}."
            )
            return

        # ── Rule 2: Start date pushed to within 72 hours ─────────────────────
        if "start_date" in changed_names:
            days_until_start = (new.start_date - date.today()).days
            if days_until_start <= 3:
                diff.has_conflict = True
                diff.conflict_type = ConflictType.START_DATE_WITHIN_72H
                diff.conflict_detail = (
                    f"Start date for {new.display_name} changed to {new.start_date} "
                    f"which is only {days_until_start} day(s) away. "
                    f"Previous start date was {old.start_date}. "
                    "Urgent review required."
                )
                return

        # ── Rule 3: Legal name changed ────────────────────────────────────────
        if "legal_first_name" in changed_names or "legal_last_name" in changed_names:
            diff.has_conflict = True
            diff.conflict_type = ConflictType.LEGAL_NAME_MISMATCH
            diff.conflict_detail = (
                f"Legal name changed: '{old.full_legal_name}' → '{new.full_legal_name}'. "
                "This may indicate a passport/ID mismatch. "
                "Background check and Okta email must be re-verified."
            )
            return

        # ── Rule 4: Multiple HIGH_RISK changes in one event ───────────────────
        high_risk_fields = diff.fields_by_risk(RiskLevel.HIGH)
        if len(high_risk_fields) >= 2:
            diff.has_conflict = True
            diff.conflict_type = ConflictType.MULTIPLE_HIGH_RISK_CHANGES
            diff.conflict_detail = (
                f"Multiple high-risk fields changed simultaneously for {new.display_name}: "
                f"{[f.field_name for f in high_risk_fields]}. "
                "Manual review recommended before syncing downstream."
            )

    def apply_patch(self, current: HireState, payload: dict) -> HireState:
        """
        Create a new HireState by applying a partial update dict to the current state.
        Used to build the 'new' state from a webhook payload without replacing the whole record.

        Args:
            current: The existing stored HireState.
            payload: Dict of fields from the webhook (only changed fields need be present).

        Returns:
            New HireState with payload fields merged in.
        """
        current_data = current.model_dump()

        # Handle nested models (address, stage_gates) as dicts
        for key, value in payload.items():
            if key in self._EXCLUDED_FIELDS:
                continue  # Ignore attempts to directly set protected fields
            current_data[key] = value

        return HireState(**current_data)
