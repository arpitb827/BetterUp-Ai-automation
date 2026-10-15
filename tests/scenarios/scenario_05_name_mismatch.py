"""
tests/scenarios/scenario_05_name_mismatch.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Scenario: Legal name changed after initial provisioning.

This tests when a candidate's legal name in Ashby gets corrected
(e.g., nickname → legal name, passport correction, hyphenated names).

Steps:
  1. Hire created with name "Eva Brown" → fully provisioned
  2. Ashby sends hire_updated: legal_last_name changed to "Brown-Garcia"

Expected flow:
  Reconciler detects LEGAL_NAME_MISMATCH conflict
  → Pipeline transitions to CONFLICT_PAUSED
  → Claude Resolver called: escalate_to_human = true (rule: legal name change always escalates)
  → People Ops DM sent with full context
  → Audit log records conflict

Asserts:
  ✅ CONFLICT_PAUSED or escalated via Slack
  ✅ CONFLICT_DETECTED in audit log
  ✅ CLAUDE_RESOLVED in audit log
  ✅ People Ops notified via Slack
  ✅ Conflict type = LEGAL_NAME_MISMATCH
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from datetime import date, timedelta
from orchestrator.pipeline import Pipeline
from connectors.ashby import AshbyConnector
from connectors.slack import SlackConnector
from audit.event_log import AuditLogger
from core.idempotency import IdempotencyStore
from tests.scenarios.helpers import make_hire, reset_mock_db, print_section, print_result


def run():
    print_section("SCENARIO 05 — Legal Name Mismatch (Conflict + Escalation)")

    # ── Setup ─────────────────────────────────────────────────────────────────
    reset_mock_db()
    idempotency = IdempotencyStore()
    idempotency.reset_for_testing()
    audit = AuditLogger()
    audit.clear_for_testing()

    hire = make_hire(
        hire_id    = "HIRE-S05",
        first_name = "Eva",
        last_name  = "Brown",
        email      = "eva.brown@gmail.com",
        start_date = date.today() + timedelta(days=21),
        ashby_id   = "ASHBY-S05",
    )

    ashby    = AshbyConnector()
    pipeline = Pipeline()

    # ── Step 1: Initial provisioning ─────────────────────────────────────────
    print("\n--- Step 1: Initial provisioning ---")
    event1  = ashby.emit_hire_created_event(hire)
    result1 = pipeline.process(event1)
    print_result(result1)
    assert result1["status"] == "PROCESSED"
    assert result1["hire_state"]["work_email"] == "eva.brown@betterup.com"

    # ── Step 2: Legal name correction ─────────────────────────────────────────
    print("\n--- Step 2: Legal last name correction (Brown → Brown-Garcia) ---")
    event2  = ashby.emit_hire_updated_event(hire, {"legal_last_name": "Brown-Garcia"})
    result2 = pipeline.process(event2)
    print_result(result2)

    # ── Assertions ────────────────────────────────────────────────────────────
    assert result2["status"] in {"CONFLICT_PAUSED", "PROCESSED"}, \
        f"Unexpected status: {result2['status']}"

    audit_entries = audit.read_for_hire("HIRE-S05")
    actions = [e["action"] for e in audit_entries]

    assert "CONFLICT_DETECTED" in actions, f"Missing CONFLICT_DETECTED in audit. Got: {actions}"
    assert "CLAUDE_RESOLVED" in actions, f"Missing CLAUDE_RESOLVED in audit. Got: {actions}"

    # Verify the conflict type in audit
    conflict_entry = next(
        (e for e in audit_entries if e["action"] == "CONFLICT_DETECTED"), None
    )
    assert conflict_entry is not None
    assert conflict_entry["detail"]["type"] == "LEGAL_NAME_MISMATCH", \
        f"Expected LEGAL_NAME_MISMATCH conflict, got: {conflict_entry['detail'].get('type')}"
    print(f"  ✅ Correct conflict type: LEGAL_NAME_MISMATCH")

    # Verify People Ops was notified
    slack = SlackConnector()
    messages = slack.get_messages()
    people_ops_msgs = [m for m in messages if "people-ops-alerts" in m["recipient"]]
    assert len(people_ops_msgs) >= 1, "Should have notified People Ops about name mismatch"
    print(f"  ✅ People Ops notified: {people_ops_msgs[-1]['message'][:100]}")

    print("\n  🎉 SCENARIO 05 — ALL ASSERTIONS PASSED ✅")


if __name__ == "__main__":
    run()
