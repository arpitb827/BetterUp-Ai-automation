"""
core/state_machine.py — HireState transition rules and gate enforcement.

Defines the valid state transitions for a hire record's lifecycle.
The state machine is the authoritative gatekeeper: no status change
happens anywhere in the codebase without going through this module.

State Flow:
  DISCOVERED → VALIDATING → PENDING_SYNC → SYNCING → SYNCED → DAY1_READY
                                    ↓
                             CONFLICT_PAUSED  ←→  (resolved) → PENDING_SYNC
                                    ↓
                                 FAILED  (dead letter)
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from core.models.hire_state import HireState, HireStatus, StageGates


# ── Allowed Transitions ───────────────────────────────────────────────────────

# Maps each state → set of states it is allowed to transition to
ALLOWED_TRANSITIONS: dict[HireStatus, set[HireStatus]] = {
    HireStatus.DISCOVERED: {
        HireStatus.VALIDATING,
        HireStatus.FAILED,           # Immediate fail on catastrophic input
    },
    HireStatus.VALIDATING: {
        HireStatus.PENDING_SYNC,
        HireStatus.CONFLICT_PAUSED,  # Validation found a conflict
        HireStatus.FAILED,
    },
    HireStatus.PENDING_SYNC: {
        HireStatus.SYNCING,
        HireStatus.CONFLICT_PAUSED,
        HireStatus.FAILED,
    },
    HireStatus.SYNCING: {
        HireStatus.SYNCED,
        HireStatus.CONFLICT_PAUSED,
        HireStatus.FAILED,
    },
    HireStatus.CONFLICT_PAUSED: {
        HireStatus.PENDING_SYNC,     # Conflict resolved → re-queue
        HireStatus.FAILED,           # Unresolvable → dead letter
    },
    HireStatus.SYNCED: {
        HireStatus.DAY1_READY,
        HireStatus.PENDING_SYNC,     # A new change arrived after sync
        HireStatus.CONFLICT_PAUSED,
    },
    HireStatus.DAY1_READY: {
        HireStatus.PENDING_SYNC,     # Late changes still possible
        HireStatus.CONFLICT_PAUSED,
    },
    HireStatus.FAILED: set(),        # Terminal state — no exit
}


# ── Transition Error ──────────────────────────────────────────────────────────

class InvalidTransitionError(Exception):
    """Raised when an attempted state transition is not allowed."""

    def __init__(self, hire_id: str, from_status: HireStatus, to_status: HireStatus):
        self.hire_id = hire_id
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(
            f"[{hire_id}] Invalid transition: {from_status.value} → {to_status.value}. "
            f"Allowed from {from_status.value}: "
            f"{[s.value for s in ALLOWED_TRANSITIONS.get(from_status, set())]}"
        )


# ── State Machine ─────────────────────────────────────────────────────────────

class StateMachine:
    """
    Validates and executes state transitions for HireState objects.

    Usage:
        sm = StateMachine()
        new_state = sm.transition(hire, HireStatus.VALIDATING)
    """

    def transition(
        self,
        hire: HireState,
        to_status: HireStatus,
        conflict_reason: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> HireState:
        """
        Attempt to transition a HireState to a new status.

        Args:
            hire:             The current HireState object.
            to_status:        The desired target status.
            conflict_reason:  Required when transitioning to CONFLICT_PAUSED.
            error_message:    Required when transitioning to FAILED.

        Returns:
            A NEW HireState object with updated status and metadata.
            The original object is never mutated.

        Raises:
            InvalidTransitionError: If the transition is not allowed.
            ValueError:             If required args for target state are missing.
        """
        from_status = hire.status

        # ── Guard: is this transition allowed? ───────────────────────────────
        allowed = ALLOWED_TRANSITIONS.get(from_status, set())
        if to_status not in allowed:
            raise InvalidTransitionError(hire.hire_id, from_status, to_status)

        # ── Guard: conflict reason required when pausing ──────────────────────
        if to_status == HireStatus.CONFLICT_PAUSED and not conflict_reason:
            raise ValueError(
                f"[{hire.hire_id}] 'conflict_reason' must be provided when transitioning "
                "to CONFLICT_PAUSED."
            )

        # ── Guard: error message required when failing ────────────────────────
        if to_status == HireStatus.FAILED and not error_message:
            raise ValueError(
                f"[{hire.hire_id}] 'error_message' must be provided when transitioning "
                "to FAILED."
            )

        # ── Guard: DAY1_READY requires all gates passed ───────────────────────
        if to_status == HireStatus.DAY1_READY:
            self._assert_all_gates_passed(hire)

        # ── Build the updated state ───────────────────────────────────────────
        updates: dict = {"status": to_status}

        if conflict_reason is not None:
            updates["conflict_reason"] = conflict_reason
        elif to_status != HireStatus.CONFLICT_PAUSED:
            # Clear conflict reason on exit from CONFLICT_PAUSED
            updates["conflict_reason"] = None

        if error_message is not None:
            updates["error_message"] = error_message

        new_hire = hire.with_updates(**updates)

        return new_hire

    def can_transition(self, hire: HireState, to_status: HireStatus) -> bool:
        """
        Non-raising check: returns True if transition is structurally allowed.
        Does NOT check gate completion (use transition() for enforcement).
        """
        return to_status in ALLOWED_TRANSITIONS.get(hire.status, set())

    def _assert_all_gates_passed(self, hire: HireState) -> None:
        """Raise ValueError if any stage gate is not yet complete."""
        pending = hire.stage_gates.pending_gates()
        if pending:
            raise ValueError(
                f"[{hire.hire_id}] Cannot transition to DAY1_READY. "
                f"Pending stage gates: {pending}"
            )

    def get_allowed_transitions(self, hire: HireState) -> list[HireStatus]:
        """Return list of valid next statuses from the hire's current state."""
        return list(ALLOWED_TRANSITIONS.get(hire.status, set()))

    def complete_gate(self, hire: HireState, gate_name: str) -> HireState:
        """
        Mark a single stage gate as complete and return a new HireState.

        Args:
            hire:      Current HireState.
            gate_name: Attribute name on StageGates (e.g. 'okta_account_provisioned').

        Returns:
            New HireState with the gate flipped to True.

        Raises:
            AttributeError: If gate_name is not a valid gate.
        """
        gates_data = hire.stage_gates.model_dump()

        if gate_name not in gates_data:
            valid_gates = list(gates_data.keys())
            raise AttributeError(
                f"'{gate_name}' is not a valid stage gate. "
                f"Valid gates: {valid_gates}"
            )

        gates_data[gate_name] = True
        new_gates = StageGates(**gates_data)
        return hire.with_updates(stage_gates=new_gates)

    def summary(self, hire: HireState) -> dict:
        """Return a human-readable status summary dict for logging / Slack messages."""
        return {
            "hire_id":          hire.hire_id,
            "name":             hire.display_name,
            "status":           hire.status.value,
            "version":          hire.version,
            "gates_passed":     hire.stage_gates.all_gates_passed(),
            "pending_gates":    hire.stage_gates.pending_gates(),
            "allowed_next":     [s.value for s in self.get_allowed_transitions(hire)],
            "conflict_reason":  hire.conflict_reason,
            "error_message":    hire.error_message,
        }
