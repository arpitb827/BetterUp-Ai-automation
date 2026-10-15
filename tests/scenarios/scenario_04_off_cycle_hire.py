"""
tests/scenarios/scenario_04_off_cycle_hire.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Scenario: Off-cycle hire created directly in Workday (no Ashby record).

This tests the case where HR creates an employee record directly in Workday
(contractor → FTE conversion, rehires, executive hires) bypassing Ashby entirely.

Expected flow:
  Workday EMPLOYEE_CREATED webhook → Pipeline
  Source = WORKDAY, no ashby_id
  Full provisioning still runs:
    Okta provisioned
    Lumos access assigned
    Equipment ordered
    Slack cohort added
  → DAY1_READY

Asserts:
  ✅ PROCESSED without ashby_id
  ✅ Workday ID preserved
  ✅ Okta provisioned with correct work email
  ✅ All gates complete → DAY1_READY
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from datetime import date, timedelta
from orchestrator.pipeline import Pipeline
from connectors.workday import WorkdayConnector
from connectors.okta import OktaConnector
from audit.event_log import AuditLogger
from core.idempotency import IdempotencyStore
from tests.scenarios.helpers import make_hire, make_address, reset_mock_db, print_section, print_result
from core.models.hire_state import HireSource


def run():
    print_section("SCENARIO 04 — Off-Cycle Hire (Workday-Only, No Ashby)")

    # ── Setup ─────────────────────────────────────────────────────────────────
    reset_mock_db()
    idempotency = IdempotencyStore()
    idempotency.reset_for_testing()
    audit = AuditLogger()
    audit.clear_for_testing()

    hire = make_hire(
        hire_id    = "HIRE-S04",
        first_name = "Diana",
        last_name  = "Patel",
        email      = "diana.patel@gmail.com",
        start_date = date.today() + timedelta(days=14),
        department = "People",
        team       = "HR Business Partners",
        source     = HireSource.WORKDAY,
        workday_id = "WD-S04-OFFCYCLE",
        ashby_id   = None,   # No Ashby record
    )

    # Build the Workday webhook event manually
    workday = WorkdayConnector()
    event_payload = workday.emit_employee_created_event(hire)

    pipeline = Pipeline()
    result   = pipeline.process(event_payload)
    print_result(result)

    # ── Assertions ────────────────────────────────────────────────────────────
    assert result["status"] == "PROCESSED", f"Expected PROCESSED, got {result['status']}"

    hire_state = result["hire_state"]
    assert hire_state["status"] == "DAY1_READY", f"Expected DAY1_READY, got {hire_state['status']}"
    assert hire_state["workday_id"] is not None, "workday_id should be preserved"
    assert hire_state["ashby_id"] is None, "ashby_id should remain None for off-cycle hire"
    assert hire_state["source"] == "WORKDAY", f"Source should be WORKDAY, got {hire_state['source']}"

    work_email = hire_state.get("work_email")
    assert work_email is not None, "work_email should be set by Okta"
    assert "@betterup.com" in work_email, f"Unexpected work_email: {work_email}"
    print(f"  ✅ Okta provisioned: {work_email}")

    gates = hire_state["stage_gates"]
    for gate_name, value in gates.items():
        assert value is True, f"Gate '{gate_name}' should be True, got {value}"

    audit_entries = audit.read_for_hire("HIRE-S04")
    actions = [e["action"] for e in audit_entries]
    assert "WORKDAY_CREATED" in actions, "Should have WORKDAY_CREATED in audit"
    assert "OKTA_PROVISIONED" in actions, "Should have OKTA_PROVISIONED in audit"
    assert "DAY1_READY" in actions, "Should have DAY1_READY in audit"

    print("\n  🎉 SCENARIO 04 — ALL ASSERTIONS PASSED ✅")


if __name__ == "__main__":
    run()
