"""
orchestrator/dispatcher.py — Routes HireStateDiff to the correct downstream connectors.

The Dispatcher is responsible for:
  1. Reading a HireStateDiff (from the Reconciler)
  2. Deciding which connectors need to be called
  3. Calling them in the right order
  4. Updating HireState with results (e.g., work_email from Okta)

Design Rules:
  - Dispatcher only handles STANDARD (non-conflict) diffs.
  - Conflict diffs are routed to ClaudeResolver BEFORE reaching the Dispatcher.
  - Dispatcher is the only place connectors are called directly.
"""

from __future__ import annotations

from core.models.hire_state import HireState, HireStatus, OktaStatus, EquipmentStatus
from core.reconciler import HireStateDiff
from core.state_machine import StateMachine
from connectors.okta import OktaConnector
from connectors.lumos import LumosConnector
from connectors.equipment import EquipmentConnector
from connectors.slack import SlackConnector
from connectors.workday import WorkdayConnector
from audit.event_log import AuditLogger


class Dispatcher:
    """
    Routes diffs to downstream connectors and updates HireState accordingly.
    """

    def __init__(self):
        self.okta      = OktaConnector()
        self.lumos     = LumosConnector()
        self.equipment = EquipmentConnector()
        self.slack     = SlackConnector()
        self.workday   = WorkdayConnector()
        self.sm        = StateMachine()
        self.audit     = AuditLogger()

    def dispatch(self, hire: HireState, diff: HireStateDiff) -> HireState:
        """
        Execute all connector calls required by the diff.

        For a NEW hire (no previous state / DISCOVERED):
          → Full provisioning: Workday + Okta + Lumos + Equipment + Slack

        For an UPDATE (existing hire):
          → Only re-sync connectors affected by changed fields.

        Returns an updated HireState with all connector results applied.
        """
        changed = diff.field_names()
        from core.models.hire_state import OktaStatus
        is_new_hire = hire.okta_status == OktaStatus.NOT_PROVISIONED

        if is_new_hire:
            hire = self._full_provision(hire)
        else:
            hire = self._selective_sync(hire, changed)

        # ── Check if all gates now pass → advance to DAY1_READY ──────────────
        if hire.stage_gates.all_gates_passed() and hire.status == HireStatus.SYNCED:
            hire = self.sm.transition(hire, HireStatus.DAY1_READY)
            self.slack.notify_hire_day1_ready(hire)
            self.audit.log(
                hire_id=hire.hire_id,
                action="DAY1_READY",
                detail={"name": hire.display_name, "start_date": str(hire.start_date)},
                version=hire.version,
            )

        return hire

    # ── Full Provisioning (New Hire) ──────────────────────────────────────────

    def _full_provision(self, hire: HireState) -> HireState:
        """Run the complete provisioning sequence for a brand-new hire."""
        print(f"\n  🚀 Full provisioning: {hire.display_name}")

        # 1. Create Workday employee record
        workday_id = self.workday.create_employee(hire)
        hire = hire.with_updates(workday_id=workday_id)
        hire = self.sm.complete_gate(hire, "background_check_complete")
        self.audit.log(hire.hire_id, "WORKDAY_CREATED", {"workday_id": workday_id}, hire.version)

        # 2. Provision Okta account
        work_email = self.okta.provision_user(hire)
        hire = hire.with_updates(work_email=work_email, okta_status=OktaStatus.PROVISIONED)
        hire = self.sm.complete_gate(hire, "okta_account_provisioned")
        self.audit.log(hire.hire_id, "OKTA_PROVISIONED", {"work_email": work_email}, hire.version)

        # 3. Assign Lumos app access
        apps = self.lumos.assign_access(hire)
        hire = self.sm.complete_gate(hire, "lumos_access_assigned")
        self.audit.log(hire.hire_id, "LUMOS_ACCESS_ASSIGNED", {"apps": apps}, hire.version)

        # 4. Order equipment
        order_id = self.equipment.order_laptop(hire)
        hire = hire.with_updates(equipment_status=EquipmentStatus.ORDERED)
        hire = self.sm.complete_gate(hire, "equipment_ordered")
        self.audit.log(hire.hire_id, "EQUIPMENT_ORDERED", {"order_id": order_id}, hire.version)

        # 5. Add to Day-1 Slack cohort
        self.slack.add_to_cohort(hire)
        hire = self.sm.complete_gate(hire, "cohort_added_to_slack")
        self.audit.log(hire.hire_id, "COHORT_ADDED", {"channel": "#day1-cohort-oct"}, hire.version)

        # 6. Mark IT survey gate (sent by IT via separate flow in prod)
        hire = self.sm.complete_gate(hire, "it_hardware_survey_submitted")
        hire = self.sm.complete_gate(hire, "day1_orientation_scheduled")

        # 7. Transition to SYNCED
        hire = self.sm.transition(hire, HireStatus.SYNCED)
        self.audit.log(hire.hire_id, "STATE_TRANSITION", {"to": "SYNCED"}, hire.version)

        return hire

    # ── Selective Sync (Update) ───────────────────────────────────────────────

    def _selective_sync(self, hire: HireState, changed_fields: list[str]) -> HireState:
        """Re-sync only the connectors affected by the specific changed fields."""
        print(f"\n  🔄 Selective sync for {hire.display_name}: {changed_fields}")

        # ── Start date changed ────────────────────────────────────────────────
        if "start_date" in changed_fields:
            self.okta.update_user(hire.hire_id, {"start_date": str(hire.start_date)})
            self.workday.update_employee(hire.workday_id or f"WD-{hire.hire_id[:8].upper()}", {
                "start_date": str(hire.start_date)
            })
            self.slack.alert_people_ops(
                subject=f"Start Date Updated — {hire.display_name}",
                detail=f"New start date: {hire.start_date}. Cohort and equipment schedule updated.",
            )
            self.audit.log(hire.hire_id, "START_DATE_UPDATED", {"start_date": str(hire.start_date)}, hire.version)

        # ── Address changed (pre-ship) ────────────────────────────────────────
        if "shipping_address" in changed_fields:
            updated = self.equipment.update_shipping_address(hire.hire_id, hire.shipping_address)
            status = "UPDATED" if updated else "FAILED_ALREADY_SHIPPED"
            self.audit.log(hire.hire_id, "ADDRESS_UPDATED", {"status": status, "address": hire.shipping_address.one_line()}, hire.version)

        # ── Department or team changed → Lumos re-assignment ──────────────────
        if "department" in changed_fields or "team" in changed_fields:
            apps = self.lumos.update_access(hire.hire_id, hire.department, hire.team)
            self.audit.log(hire.hire_id, "LUMOS_ACCESS_UPDATED", {"department": hire.department, "apps": apps}, hire.version)

        # ── Job title changed → Okta + Workday ───────────────────────────────
        if "job_title" in changed_fields:
            self.okta.update_user(hire.hire_id, {"job_title": hire.job_title})
            self.workday.update_employee(hire.workday_id or "", {"job_title": hire.job_title})
            self.audit.log(hire.hire_id, "JOB_TITLE_UPDATED", {"job_title": hire.job_title}, hire.version)

        # ── Manager changed ───────────────────────────────────────────────────
        if "manager_email" in changed_fields:
            self.okta.update_user(hire.hire_id, {"manager_email": hire.manager_email})
            self.workday.update_employee(hire.workday_id or "", {"manager_email": hire.manager_email})
            self.audit.log(hire.hire_id, "MANAGER_UPDATED", {"manager_email": hire.manager_email}, hire.version)

        # ── Preferred name changed → Slack display ────────────────────────────
        if "preferred_name" in changed_fields:
            self.slack.send_dm(
                hire.manager_email,
                f"ℹ️ FYI: {hire.full_legal_name} has updated their preferred name to '{hire.preferred_name}'.",
            )
            self.audit.log(hire.hire_id, "PREFERRED_NAME_UPDATED", {"preferred_name": hire.preferred_name}, hire.version)

        # ── Transition back to SYNCED ─────────────────────────────────────────
        hire = self.sm.transition(hire, HireStatus.SYNCED)
        self.audit.log(hire.hire_id, "STATE_TRANSITION", {"to": "SYNCED"}, hire.version)

        return hire
