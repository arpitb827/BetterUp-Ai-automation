"""
tests/scenarios/scenario_07_missed_webhook_recovery.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Scenario: Webhook Dropped In-Flight & Scheduled Reconciliation Poller Recovery.

Simulates:
  1. A new offer is accepted in Ashby, but the webhook gets dropped by a network blip.
     The pipeline receives NOTHING.
  2. The scheduled ReconciliationPoller runs its periodic cycle.
     - Detects the un-ingested hire.
     - Auto-synthesizes the event and executes full provisioning.
  3. A start date update is made in Ashby, but again the webhook is lost in flight.
  4. The ReconciliationPoller runs its next cycle:
     - Diffs the upstream Ashby record against our canonical state.
     - Identifies the start_date drift.
     - Synthesizes the delta event and syncs Okta & Workday.

Asserts:
  ✅ Poller heals the completely missed hire without manual human chasing.
  ✅ Poller detects silent field drift and updates Okta downstream.
  ✅ People Ops receives Slack alert summarizing the healed drift.
  ✅ Audit trail records POLLER_HEALED_MISSED_HIRE and POLLER_HEALED_DRIFT.
"""

import sys
import os
from datetime import date, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from orchestrator.pipeline import Pipeline
from orchestrator.poller import ReconciliationPoller
from connectors.ashby import AshbyConnector
from connectors.okta import OktaConnector
from connectors.slack import SlackConnector
from audit.event_log import AuditLogger
from core.idempotency import IdempotencyStore
from tests.scenarios.helpers import make_hire, reset_mock_db, print_section, print_result


def run():
    print_section("SCENARIO 07 — Missed Webhook Recovery via Reconciliation Poller")

    # Setup
    reset_mock_db()
    IdempotencyStore().reset_for_testing()
    AuditLogger().clear_for_testing()

    pipeline = Pipeline()
    ashby    = AshbyConnector()
    poller   = ReconciliationPoller(pipeline=pipeline)
    okta     = OktaConnector()
    audit    = AuditLogger()

    # ── Test 1: Completely dropped new hire creation webhook ──────────────────
    print("\n--- Test 1: Simulating dropped new hire webhook ---")
    hire = make_hire(
        hire_id="HIRE-S07",
        first_name="Serena",
        last_name="Williams",
        email="serena.williams@gmail.com",
        ashby_id="ASHBY-S07",
        start_date=date.today() + timedelta(days=28),
    )
    # Save directly to Ashby ATS mock to simulate recruiter action in ATS
    # BUT NEVER emit the webhook to the pipeline!
    ashby.save_hire(hire)

    # Verify pipeline currently knows NOTHING about Serena
    assert pipeline._load_hire("HIRE-S07") is None, "Hire should not yet exist in pipeline"
    print("  📡 Ashby has the candidate, but webhook was LOST in transit.")

    # Run scheduled poller sweep
    report1 = poller.run_reconciliation_cycle()
    assert "HIRE-S07" in report1.healed_creates, "Poller should have healed the missed hire"

    # Verify Serena is now fully provisioned in our system
    healed_hire = pipeline._load_hire("HIRE-S07")
    assert healed_hire is not None
    assert healed_hire.status.value == "DAY1_READY"
    print(f"  ✅ Poller auto-provisioned missed hire: {healed_hire.display_name} → {healed_hire.status.value}")

    # ── Test 2: In-flight update dropped (Silent Data Drift) ───────────────────
    print("\n--- Test 2: Simulating dropped start_date update webhook ---")
    new_date = date.today() + timedelta(days=45)
    # Recruiter updates start date in Ashby directly, but webhook fails
    updated_upstream = healed_hire.with_updates(start_date=new_date)
    ashby.save_hire(updated_upstream)
    print(f"  📡 Recruiter pushed start date to {new_date} in Ashby. Webhook lost!")

    # Verify our Okta currently still has the old start date
    okta_user = okta.get_user("HIRE-S07")
    assert okta_user["start_date"] != str(new_date), "Okta should still have old start date"

    # Run second poller sweep
    report2 = poller.run_reconciliation_cycle()
    assert any("HIRE-S07" in item for item in report2.healed_updates), "Poller should have healed the drift"

    # Verify downstream Okta now has the new date
    okta_user_after = okta.get_user("HIRE-S07")
    assert okta_user_after["start_date"] == str(new_date), f"Okta not healed: {okta_user_after}"
    print(f"  ✅ Poller healed data drift: Okta start date synced to {new_date}")

    # ── Verify Audit Log ──────────────────────────────────────────────────────
    audit_entries = audit.read_for_hire("HIRE-S07")
    actions = [e["action"] for e in audit_entries]
    assert "POLLER_HEALED_MISSED_HIRE" in actions, "Missing POLLER_HEALED_MISSED_HIRE in audit"
    assert "POLLER_HEALED_DRIFT" in actions, "Missing POLLER_HEALED_DRIFT in audit"

    # Verify People Ops was alerted about the sweep
    slack = SlackConnector()
    messages = slack.get_messages()
    recon_alerts = [m for m in messages if "Reconciliation Sweep" in m["message"]]
    assert len(recon_alerts) >= 1, "People Ops should have received reconciliation report"
    print(f"  ✅ People Ops notified: {recon_alerts[-1]['message'][:90]}...")

    print("\n  🎉 SCENARIO 07 — ALL POLLER RECOVERY ASSERTIONS PASSED ✅")


if __name__ == "__main__":
    run()
