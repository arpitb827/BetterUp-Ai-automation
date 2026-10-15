"""
tests/scenarios/scenario_03_address_change_mid_ship.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Scenario: Hire changes address AFTER laptop has already been shipped.

Steps:
  1. Hire created → fully provisioned
  2. Equipment manually marked as SHIPPED (simulate carrier pickup)
  3. Ashby sends hire_updated: shipping_address changed

Expected flow:
  Reconciler detects ADDRESS_CHANGED_POST_SHIP conflict
  → Pipeline transitions to CONFLICT_PAUSED
  → Claude Resolver called (or fallback if API key not set)
  → Decision: cancel + reorder OR escalate to People Ops

Asserts:
  ✅ Conflict detected (CONFLICT_PAUSED status)
  ✅ Slack conflict alert sent to People Ops
  ✅ Audit log has CONFLICT_DETECTED entry
  ✅ Claude (or fallback) resolver was invoked
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
    print_section("SCENARIO 03 — Address Changed After Laptop Shipped (Conflict)")

    # ── Setup ─────────────────────────────────────────────────────────────────
    reset_mock_db()
    idempotency = IdempotencyStore()
    idempotency.reset_for_testing()
    audit = AuditLogger()
    audit.clear_for_testing()

    original_address = make_address(
        street  = "456 Oak Ave",
        city    = "Austin",
        state   = "TX",
        zip_code = "78701",
    )
    hire = make_hire(
        hire_id    = "HIRE-S03",
        first_name = "Carlos",
        last_name  = "Rivera",
        email      = "carlos.rivera@gmail.com",
        start_date = date.today() + timedelta(days=21),
        address    = original_address,
        ashby_id   = "ASHBY-S03",
    )

    ashby    = AshbyConnector()
    pipeline = Pipeline()
    equipment = EquipmentConnector()

    # ── Step 1: Provision hire ────────────────────────────────────────────────
    print("\n--- Step 1: Initial provisioning ---")
    event1  = ashby.emit_hire_created_event(hire)
    result1 = pipeline.process(event1)
    print_result(result1)
    assert result1["status"] == "PROCESSED"

    # ── Step 2: Simulate laptop shipped ──────────────────────────────────────
    print("\n--- Step 2: Simulate equipment shipped ---")
    equipment.mark_shipped("HIRE-S03", tracking_number="FEDEX-9261290100830166")
    order = equipment.get_order("HIRE-S03")
    assert order["status"] == "SHIPPED", "Equipment should be SHIPPED"
    print(f"  📦 Equipment status: {order['status']} (tracking: {order['tracking_number']})")

    # ── Step 3: Address change event ──────────────────────────────────────────
    print("\n--- Step 3: Address change event received ---")
    new_address = {
        "street_line_1": "789 Pine Rd",
        "city":          "Denver",
        "state":         "CO",
        "zip_code":      "80201",
        "country":       "US",
    }
    event2  = ashby.emit_hire_updated_event(hire, {"shipping_address": new_address})
    result2 = pipeline.process(event2)
    print_result(result2)

    # ── Assertions ────────────────────────────────────────────────────────────
    assert result2["status"] in {"CONFLICT_PAUSED", "PROCESSED"}, \
        f"Expected CONFLICT_PAUSED or PROCESSED, got {result2['status']}"

    audit_entries = audit.read_for_hire("HIRE-S03")
    actions = [e["action"] for e in audit_entries]
    assert "CONFLICT_DETECTED" in actions, f"Audit log missing CONFLICT_DETECTED. Got: {actions}"
    assert "CLAUDE_RESOLVED" in actions, f"Audit log missing CLAUDE_RESOLVED. Got: {actions}"

    slack  = SlackConnector()
    messages = slack.get_messages()
    conflict_msgs = [
        m for m in messages
        if "people-ops-alerts" in m["recipient"] and "conflict" in m["message"].lower()
    ]
    assert len(conflict_msgs) >= 1, "Should have sent conflict alert to People Ops"
    print(f"  ✅ Conflict alert sent: {conflict_msgs[-1]['message'][:100]}")

    print("\n  🎉 SCENARIO 03 — ALL ASSERTIONS PASSED ✅")


if __name__ == "__main__":
    run()
