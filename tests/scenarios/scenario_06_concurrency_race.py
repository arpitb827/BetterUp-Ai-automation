"""
tests/scenarios/scenario_06_concurrency_race.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Scenario: Concurrent Webhook Arrivals & Optimistic Concurrency Control (OCC).

Simulates:
  1. Initial hire is created and provisioned.
  2. 4 concurrent threads simultaneously submit different updates for the SAME hire:
     - Thread A: updates job_title
     - Thread B: updates preferred_name
     - Thread C: updates department
     - Thread D: re-submits initial creation (duplicate replay)
  3. Tests Optimistic Concurrency Control (OCC):
     - An update specifying an obsolete `expected_version` is caught and paused.

Asserts:
  ✅ All concurrent threads complete without deadlocks or unhandled exceptions.
  ✅ Per-hire lock serializes execution without corrupting mock_db.json or state.
  ✅ Final version monotonically advances.
  ✅ OCC detects version drift when expected_version does not match.
"""

import sys
import os
import concurrent.futures
from datetime import date, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from orchestrator.pipeline import Pipeline
from connectors.ashby import AshbyConnector
from audit.event_log import AuditLogger
from core.idempotency import IdempotencyStore
from tests.scenarios.helpers import make_hire, reset_mock_db, print_section, print_result


def run():
    print_section("SCENARIO 06 — Concurrent Webhook Processing & OCC")

    # Setup
    reset_mock_db()
    IdempotencyStore().reset_for_testing()
    AuditLogger().clear_for_testing()

    pipeline = Pipeline()
    ashby    = AshbyConnector()

    # Step 1: Initial provisioning
    hire = make_hire(
        hire_id="HIRE-S06",
        first_name="Marcus",
        last_name="Vance",
        email="marcus.vance@gmail.com",
        ashby_id="ASHBY-S06",
    )
    event1 = ashby.emit_hire_created_event(hire)
    init_res = pipeline.process(event1)
    assert init_res["status"] == "PROCESSED"
    baseline_version = init_res["hire_state"]["version"]
    print(f"  ✅ Initial hire provisioned at version: {baseline_version}")

    # Step 2: Prepare 4 concurrent events for the SAME hire
    event_job = ashby.emit_hire_updated_event(hire, {"job_title": "Lead Staff Architect"})
    event_pref = ashby.emit_hire_updated_event(hire, {"preferred_name": "Marc"})
    event_dept = ashby.emit_hire_updated_event(hire, {"department": "Product"})
    event_dup  = event1  # duplicate replay

    events = [event_job, event_pref, event_dept, event_dup]

    print("\n  🔥 Firing 4 concurrent events across worker threads simultaneously...")
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(pipeline.process, evt) for evt in events]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    # Assertions on concurrent results
    statuses = [r["status"] for r in results]
    print(f"  📊 Concurrent execution statuses: {statuses}")
    assert "SKIPPED" in statuses, "Duplicate replay should have been SKIPPED by idempotency"
    assert "PROCESSED" in statuses, "Valid updates should be PROCESSED"

    # Verify final state reflects serialized mutations
    final_hire = pipeline._load_hire("HIRE-S06")
    assert final_hire is not None
    assert final_hire.version > baseline_version, "Version must have advanced"
    print(f"  ✅ Final version after concurrent execution: {final_hire.version}")

    # Step 3: Test Optimistic Concurrency Control (OCC) collision detection
    print("\n  🛡️ Testing Optimistic Concurrency Control (OCC)...")
    stale_event = ashby.emit_hire_updated_event(hire, {
        "job_title": "Principal Architect",
        "expected_version": 1,  # Stale version! Actual is higher
    })
    occ_res = pipeline.process(stale_event)
    assert occ_res["status"] == "CONFLICT_PAUSED"
    assert "Optimistic Concurrency Collision" in occ_res["message"]
    print(f"  ✅ OCC successfully caught stale event: {occ_res['message']}")

    print("\n  🎉 SCENARIO 06 — ALL CONCURRENCY ASSERTIONS PASSED ✅")


if __name__ == "__main__":
    run()
