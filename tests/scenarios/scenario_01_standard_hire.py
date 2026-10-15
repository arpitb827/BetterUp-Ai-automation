"""
tests/scenarios/scenario_01_standard_hire.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Scenario: Standard new hire created in Ashby.

Expected flow:
  Ashby webhook → Pipeline → Full provision:
    Workday (employee record)
    Okta (account + work email)
    Lumos (app access)
    Equipment (laptop ordered)
    Slack (cohort added)
  All gates complete → DAY1_READY

Asserts:
  ✅ Pipeline returns PROCESSED
  ✅ HireState status = DAY1_READY
  ✅ work_email assigned by Okta
  ✅ All stage gates = True
  ✅ Equipment order exists in mock DB
  ✅ Slack cohort message sent
  ✅ Audit log contains all expected actions
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from datetime import date, timedelta
from orchestrator.pipeline import Pipeline
from connectors.ashby import AshbyConnector
from connectors.equipment import EquipmentConnector
from connectors.slack import SlackConnector
from audit.event_log import AuditLogger
from core.idempotency import IdempotencyStore
from tests.scenarios.helpers import make_hire, make_address, reset_mock_db, print_section, print_result


def run():
    print_section("SCENARIO 01 — Standard New Hire (Happy Path)")

    # ── Setup ─────────────────────────────────────────────────────────────────
    reset_mock_db()
    idempotency = IdempotencyStore()
    idempotency.reset_for_testing()
    audit = AuditLogger()
    audit.clear_for_testing()

    hire = make_hire(
        hire_id    = "HIRE-S01",
        first_name = "Alice",
        last_name  = "Johnson",
        email      = "alice.johnson@gmail.com",
        start_date = date.today() + timedelta(days=21),
        department = "Engineering",
        team       = "Platform",
        ashby_id   = "ASHBY-S01",
    )

    ashby = AshbyConnector()
    event_payload = ashby.emit_hire_created_event(hire)

    # ── Run ───────────────────────────────────────────────────────────────────
    pipeline = Pipeline()
    result   = pipeline.process(event_payload)
    print_result(result)

    # ── Assertions ────────────────────────────────────────────────────────────
    assert result["status"] == "PROCESSED", f"Expected PROCESSED, got {result['status']}"

    hire_state = result["hire_state"]
    assert hire_state["status"] == "DAY1_READY", f"Expected DAY1_READY, got {hire_state['status']}"
    assert hire_state["work_email"] is not None, "work_email should be set by Okta"
    assert hire_state["work_email"] == "alice.johnson@betterup.com", f"Unexpected work_email: {hire_state['work_email']}"

    gates = hire_state["stage_gates"]
    for gate_name, value in gates.items():
        assert value is True, f"Gate '{gate_name}' should be True, got {value}"

    equipment = EquipmentConnector()
    order = equipment.get_order("HIRE-S01")
    assert order is not None, "Equipment order should exist"
    assert order["status"] == "ORDERED", f"Equipment status should be ORDERED, got {order['status']}"

    slack = SlackConnector()
    messages = slack.get_messages()
    cohort_msgs = [m for m in messages if "day1-cohort" in m["recipient"]]
    assert len(cohort_msgs) >= 1, "Should have at least 1 Slack cohort message"

    audit_entries = audit.read_for_hire("HIRE-S01")
    actions = [e["action"] for e in audit_entries]
    for expected in ["OKTA_PROVISIONED", "EQUIPMENT_ORDERED", "COHORT_ADDED", "DAY1_READY"]:
        assert expected in actions, f"Audit log missing action: {expected}"

    print("\n  🎉 SCENARIO 01 — ALL ASSERTIONS PASSED ✅")


if __name__ == "__main__":
    run()
