"""
orchestrator/pipeline.py — Main orchestration pipeline entry point.

This is the single entry point for all incoming events.
It coordinates: Idempotency → Validation → Reconcile → Dispatch/Resolve → Audit

Processing Flow:
  1. Receive ChangeEvent
  2. Check idempotency (skip if already processed)
  3. Parse + validate payload into HireState
  4. Look up existing hire state (if any)
  5. Run Reconciler diff
  6. If conflict → Claude Resolver
  7. If standard → Dispatcher
  8. Mark idempotency key as seen
  9. Audit log every step
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from core.models.hire_state import HireState, HireStatus, HireSource, Address, StageGates
from core.models.events import ChangeEvent, EventType
from core.state_machine import StateMachine, InvalidTransitionError
from core.reconciler import Reconciler
from core.idempotency import IdempotencyStore
from connectors.ashby import AshbyConnector
from connectors.workday import WorkdayConnector
from connectors.slack import SlackConnector
from orchestrator.dispatcher import Dispatcher
from agent.claude_resolver import ClaudeResolver
from audit.event_log import AuditLogger
import threading


class ConcurrencyConflictError(Exception):
    """Raised when Optimistic Concurrency Control detects a version collision."""
    pass


class LockManager:
    """Thread-safe per-hire lock manager ensuring sequential processing per hire."""
    def __init__(self):
        self._locks: dict[str, threading.RLock] = {}
        self._master_lock = threading.Lock()

    def get_lock(self, hire_id: str) -> threading.RLock:
        with self._master_lock:
            if hire_id not in self._locks:
                self._locks[hire_id] = threading.RLock()
            return self._locks[hire_id]


class Pipeline:
    """
    The central orchestration pipeline with concurrency safety and OCC.

    Usage:
        pipeline = Pipeline()
        result = pipeline.process(event_dict)
    """

    def __init__(self):
        self.sm           = StateMachine()
        self.reconciler   = Reconciler()
        self.idempotency  = IdempotencyStore()
        self.dispatcher   = Dispatcher()
        self.resolver     = ClaudeResolver()
        self.audit        = AuditLogger()
        self.ashby        = AshbyConnector()
        self.workday_c    = WorkdayConnector()
        self.slack        = SlackConnector()
        self.lock_manager = LockManager()
        self._store_lock  = threading.RLock()

        # In-memory hire state store (production: use a real DB)
        self._hire_store: dict[str, HireState] = {}

    # ── Public Entry Point ────────────────────────────────────────────────────

    def process(self, raw_event: dict) -> dict:
        """
        Process a raw webhook payload dict.

        Args:
            raw_event: Dict matching ChangeEvent schema fields.

        Returns:
            dict with keys:
              - status: "PROCESSED" | "SKIPPED" | "CONFLICT_PAUSED" | "FAILED"
              - hire_id: str
              - hire_state: dict  (final HireState dump)
              - message: str
        """
        print(f"\n{'='*60}")
        print(f"📥 Pipeline received event: {raw_event.get('event_type')} / hire_id={raw_event.get('hire_id')}")
        print(f"{'='*60}")

        # ── Step 1: Parse ChangeEvent ─────────────────────────────────────────
        try:
            event = ChangeEvent(**raw_event)
        except Exception as e:
            self.audit.log("UNKNOWN", "PARSE_ERROR", {"error": str(e), "raw": str(raw_event)[:200]})
            return self._error_result("UNKNOWN", f"Failed to parse event: {e}")

        # Acquire per-hire mutex lock to serialize concurrent events for the same employee
        lock = self.lock_manager.get_lock(event.hire_id)
        with lock:
            # ── Step 2: Idempotency check ─────────────────────────────────────
            if self.idempotency.has_seen(event.idempotency_key):
                print(f"  ⏭️  Duplicate event — skipping (key={event.idempotency_key[:12]}...)")
                self.audit.log(event.hire_id, "IDEMPOTENCY_SKIP", {"key": event.idempotency_key[:16]})
                return {
                    "status":     "SKIPPED",
                    "hire_id":    event.hire_id,
                    "hire_state": {},
                    "message":    "Duplicate event — already processed.",
                }

            # ── Step 3: Load or initialize HireState ──────────────────────────
            existing_hire = self._load_hire(event.hire_id)

            # Optimistic Concurrency Control (OCC): check expected version if supplied
            expected_ver = event.payload.get("expected_version")
            if expected_ver is not None and existing_hire and existing_hire.version != expected_ver:
                err_msg = (
                    f"Optimistic Concurrency Collision for {event.hire_id}: "
                    f"Event expected version {expected_ver}, but current version is {existing_hire.version}."
                )
                print(f"  ⚠️  OCC Conflict: {err_msg}")
                self.audit.log(event.hire_id, "OCC_COLLISION", {
                    "expected_version": expected_ver,
                    "current_version": existing_hire.version,
                }, existing_hire.version)
                return {
                    "status":     "CONFLICT_PAUSED",
                    "hire_id":    event.hire_id,
                    "hire_state": existing_hire.model_dump(),
                    "message":    err_msg,
                }

            try:
                incoming_hire = self._parse_hire_from_event(event, existing_hire)
            except Exception as e:
                self._send_to_dead_letter(event, str(e))
                return self._error_result(event.hire_id, f"Validation error: {e}")

            # ── Step 4: Transition to VALIDATING ──────────────────────────────
            try:
                incoming_hire = self.sm.transition(incoming_hire, HireStatus.VALIDATING)
            except InvalidTransitionError as e:
                # Already past VALIDATING (e.g., existing hire getting an update)
                pass

            self.audit.log(
                event.hire_id, "EVENT_RECEIVED",
                {"event_type": event.event_type.value, "source": event.source.value},
                incoming_hire.version,
            )

            # ── Step 5: Reconcile (diff) ──────────────────────────────────────
            if existing_hire:
                diff = self.reconciler.diff(existing_hire, incoming_hire)
            else:
                # New hire — no previous state. Use a minimal diff (all fields are "new")
                diff = self.reconciler.diff(incoming_hire, incoming_hire)
                diff.changed_fields = []  # New hire — trigger full provision path

            if diff.is_no_op and existing_hire:
                print(f"  ⏭️  No changes detected — no-op.")
                self.idempotency.mark_seen(event.idempotency_key, event.hire_id)
                return {
                    "status":     "SKIPPED",
                    "hire_id":    event.hire_id,
                    "hire_state": incoming_hire.model_dump(),
                    "message":    "No changes detected.",
                }

            # ── Step 6: Transition to PENDING_SYNC ───────────────────────────
            try:
                incoming_hire = self.sm.transition(incoming_hire, HireStatus.PENDING_SYNC)
            except InvalidTransitionError:
                pass  # Already in PENDING_SYNC or beyond

            # ── Step 7: Route — Conflict or Standard ──────────────────────────
            if diff.has_conflict:
                print(f"\n  ⚠️  CONFLICT detected: {diff.conflict_type}")
                print(f"  ℹ️  {diff.conflict_detail}")

                # Pause state
                incoming_hire = self.sm.transition(
                    incoming_hire,
                    HireStatus.CONFLICT_PAUSED,
                    conflict_reason=diff.conflict_detail,
                )
                self.audit.log(
                    event.hire_id, "CONFLICT_DETECTED",
                    {"type": diff.conflict_type, "detail": diff.conflict_detail},
                    incoming_hire.version,
                )

                # Ask Claude to resolve
                audit_log = self.audit.read_for_hire(event.hire_id)
                decision  = self.resolver.resolve(incoming_hire, diff, audit_log)

                # If Claude did not escalate to human, re-sync
                if not decision.get("escalate_to_human", True):
                    try:
                        incoming_hire = self.sm.transition(incoming_hire, HireStatus.PENDING_SYNC, conflict_reason=None)
                        incoming_hire = self.dispatcher.dispatch(incoming_hire, diff)
                    except Exception as e:
                        incoming_hire = self.sm.transition(incoming_hire, HireStatus.FAILED, error_message=str(e))
                        self._send_to_dead_letter(event, str(e))

                self._save_hire(incoming_hire)
                self.idempotency.mark_seen(event.idempotency_key, event.hire_id)

                return {
                    "status":     "CONFLICT_PAUSED",
                    "hire_id":    event.hire_id,
                    "hire_state": incoming_hire.model_dump(),
                    "message":    f"Conflict: {diff.conflict_type}. Claude decision: {decision.get('conflict_summary', '')}",
                }

            # ── Standard path: dispatch ───────────────────────────────────────
            try:
                incoming_hire = self.sm.transition(incoming_hire, HireStatus.SYNCING)
                incoming_hire = self.dispatcher.dispatch(incoming_hire, diff)
            except Exception as e:
                err = str(e)
                print(f"  ❌ Dispatch error: {err}")
                self._send_to_dead_letter(event, err)
                incoming_hire = self.sm.transition(incoming_hire, HireStatus.FAILED, error_message=err)
                self._save_hire(incoming_hire)
                return self._error_result(event.hire_id, err)

            # ── Step 8: Persist + mark idempotency ───────────────────────────
            self._save_hire(incoming_hire)
            self.idempotency.mark_seen(event.idempotency_key, event.hire_id)

            print(f"\n  ✅ Pipeline complete: {incoming_hire.display_name} → {incoming_hire.status.value}")
            self.audit.log(
                event.hire_id, "PIPELINE_COMPLETE",
                {"final_status": incoming_hire.status.value, "version": incoming_hire.version},
                incoming_hire.version,
            )

            return {
                "status":     "PROCESSED",
                "hire_id":    event.hire_id,
                "hire_state": incoming_hire.model_dump(),
                "message":    f"Successfully processed. Final status: {incoming_hire.status.value}",
            }

    # ── Hire Store (in-memory + Ashby/Workday lookup) ────────────────────────

    def _load_hire(self, hire_id: str) -> Optional[HireState]:
        """
        Load existing HireState from in-memory store, then fall back to Ashby mock.
        Also syncs equipment_status and okta_status from their respective connectors
        so conflict rules always see the current live state.
        """
        from connectors.equipment import EquipmentConnector
        from connectors.okta import OktaConnector
        from core.models.hire_state import EquipmentStatus, OktaStatus

        with self._store_lock:
            hire = None
            if hire_id in self._hire_store:
                hire = self._hire_store[hire_id]
            else:
                stored = self.ashby.get_hire(hire_id)
                # Only treat as existing ingested hire if it has already been provisioned
                if stored and (stored.work_email or stored.workday_id or stored.status != HireStatus.DISCOVERED):
                    hire = stored
                else:
                    hire = None

            if hire is None:
                return None

            # Sync live equipment status from Equipment connector
            eq_conn = EquipmentConnector()
            live_eq_status = eq_conn.get_status(hire_id)
            if live_eq_status and live_eq_status != hire.equipment_status:
                hire = hire.with_updates(equipment_status=live_eq_status)

            # Sync live okta status
            ok_conn = OktaConnector()
            ok_user = ok_conn.get_user(hire_id)
            if ok_user and ok_user.get("status") == "PROVISIONED" and hire.okta_status == OktaStatus.NOT_PROVISIONED:
                hire = hire.with_updates(okta_status=OktaStatus.PROVISIONED)

            return hire

    def _save_hire(self, hire: HireState) -> None:
        """Persist HireState to in-memory store and Ashby mock DB with lock protection."""
        with self._store_lock:
            self._hire_store[hire.hire_id] = hire
            self.ashby.save_hire(hire)

    # ── Event Parsing ─────────────────────────────────────────────────────────

    def _parse_hire_from_event(
        self,
        event: ChangeEvent,
        existing: Optional[HireState],
    ) -> HireState:
        """
        Parse a HireState from a ChangeEvent payload.
        If an existing hire is found, merges the payload on top of it.
        If no existing hire, builds a fresh HireState from scratch.
        """
        payload = event.payload

        if existing:
            # Merge: apply only the changed fields from the payload
            return self.reconciler.apply_patch(existing, payload)

        # ── Build fresh HireState ─────────────────────────────────────────────
        address_data = payload.get("shipping_address", {})
        if isinstance(address_data, dict):
            address = Address(**address_data)
        else:
            raise ValueError("Missing or invalid shipping_address in payload")

        source_val = payload.get("source", "ASHBY")
        source = HireSource(source_val)

        return HireState(
            hire_id          = payload["hire_id"],
            ashby_id         = payload.get("ashby_id"),
            workday_id       = payload.get("workday_id"),
            source           = source,
            legal_first_name = payload["legal_first_name"],
            legal_last_name  = payload["legal_last_name"],
            preferred_name   = payload.get("preferred_name"),
            personal_email   = payload["personal_email"],
            job_title        = payload["job_title"],
            department       = payload["department"],
            team             = payload["team"],
            manager_email    = payload["manager_email"],
            start_date       = payload["start_date"],
            shipping_address = address,
            laptop_model     = payload.get("laptop_model", "MacBook Pro 14\""),
            status           = HireStatus.DISCOVERED,
            stage_gates      = StageGates(),
        )

    # ── Dead Letter & Error Helpers ───────────────────────────────────────────

    def _send_to_dead_letter(self, event: ChangeEvent, error: str) -> None:
        """Log to audit and alert People Ops via Slack."""
        print(f"  💀 Dead letter: hire_id={event.hire_id} — {error}")
        self.audit.log(event.hire_id, "DEAD_LETTER", {"error": error, "event_type": event.event_type.value})
        self.slack.notify_dead_letter(event.hire_id, error)

    def _error_result(self, hire_id: str, message: str) -> dict:
        return {
            "status":     "FAILED",
            "hire_id":    hire_id,
            "hire_state": {},
            "message":    message,
        }
