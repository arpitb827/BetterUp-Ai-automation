"""
tests/scenarios/scenario_02_start_date_change.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Scenario: Start date pushed back after initial hire provisioning.

Steps:
  1. Hire created → fully provisioned (DAY1_READY)
  2. Ashby sends hire_updated: start_date changed (T+35 instead of T+21)

Expected flow:
  Selective sync:
    Okta updated with new start_date
    Workday updated with new start_date
    Slack People Ops alert sent

Asserts:
  ✅ Second event PROCESSED
  ✅ Okta record has new start_date
  ✅ Slack alert sent to People Ops
  ✅ Idempotency: replaying same event skips
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from datetime import date, timedelta
from orchestrator.pipeline import Pipeline
from connectors.ashby import AshbyConnector
from connectors.okta import OktaConnector
from connectors.slack import SlackConnector
from audit.event_log import AuditLogger
from core.idempotency import IdempotencyStore
from tests.scenarios.helpers import make_hire, reset_mock_db, print_section, print_result


def run():
    print_section("SCENARIO 02 — Start Date Change (Mid-Flight Update)")

    # ── Setup ─────────────────────────────────────────────────────────────────
    reset_mock_db()
    idempotency = IdempotencyStore()
    idempotency.reset_for_testing()
    audit = AuditLogger()
    audit.clear_for_testing()

    original_start = date.today() + timedelta(days=21)
    hire = make_hire(
        hire_id    = "HIRE-S02",
        first_name = "Bob",
        last_name  = "Chen",
        email      = "bob.chen@gmail.com",
        start_date = original_start,
        ashby_id   = "ASHBY-S02",
    )

    ashby    = AshbyConnector()
    pipeline = Pipeline()

    # ── Step 1: Initial provisioning ─────────────────────────────────────────
    print("\n--- Step 1: Initial hire provisioning ---")
    event1   = ashby.emit_hire_created_event(hire)
    result1  = pipeline.process(event1)
    print_result(result1)
    assert result1["status"] == "PROCESSED"
    assert result1["hire_state"]["status"] == "DAY1_READY"

    # ── Step 2: Start date change ─────────────────────────────────────────────
    print("\n--- Step 2: Start date pushed back ---")
    new_start = date.today() + timedelta(days=35)
    event2    = ashby.emit_hire_updated_event(hire, {"start_date": new_start.isoformat()})
    result2   = pipeline.process(event2)
    print_result(result2)

    assert result2["status"] == "PROCESSED", f"Expected PROCESSED, got {result2['status']}"

    okta = OktaConnector()
    okta_user = okta.get_user("HIRE-S02")
    assert okta_user is not None
    assert okta_user.get("start_date") == str(new_start), \
        f"Okta start_date not updated. Got: {okta_user.get('start_date')}"

    slack = SlackConnector()
    messages = slack.get_messages()
    people_ops_msgs = [m for m in messages if "people-ops-alerts" in m["recipient"]]
    assert len(people_ops_msgs) >= 1, "Should have alerted People Ops about date change"
    print(f"  ✅ People Ops alerted: {people_ops_msgs[-1]['message'][:80]}")

    # ── Step 3: Replay same event — should skip ───────────────────────────────
    print("\n--- Step 3: Replay event (idempotency check) ---")
    result3 = pipeline.process(event2)
    assert result3["status"] == "SKIPPED", f"Replay should be SKIPPED, got {result3['status']}"
    print(f"  ✅ Replay correctly skipped: {result3['message']}")

    print("\n  🎉 SCENARIO 02 — ALL ASSERTIONS PASSED ✅")


if __name__ == "__main__":
    run()
