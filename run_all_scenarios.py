"""
run_all_scenarios.py — Master scenario test runner.

Runs all 5 end-to-end scenarios in sequence.
Each scenario resets the mock DB and idempotency store before running.

Usage:
    python run_all_scenarios.py
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from tests.scenarios import (
    scenario_01_standard_hire,
    scenario_02_start_date_change,
    scenario_03_address_change_mid_ship,
    scenario_04_off_cycle_hire,
    scenario_05_name_mismatch,
    scenario_06_concurrency_race,
    scenario_07_missed_webhook_recovery,
)

SCENARIOS = [
    ("01 — Standard New Hire",                    scenario_01_standard_hire),
    ("02 — Start Date Change",                    scenario_02_start_date_change),
    ("03 — Address Changed After Ship",           scenario_03_address_change_mid_ship),
    ("04 — Off-Cycle Hire (Workday-Only)",        scenario_04_off_cycle_hire),
    ("05 — Legal Name Mismatch",                  scenario_05_name_mismatch),
    ("06 — Concurrency Race & OCC",               scenario_06_concurrency_race),
    ("07 — Missed Webhook Recovery (Poller)",     scenario_07_missed_webhook_recovery),
]


def main():
    print("\n" + "🚀 " * 20)
    print("  BetterUp Onboarding Reconciliation Engine — Full Scenario Suite")
    print("🚀 " * 20)

    passed = []
    failed = []

    for name, module in SCENARIOS:
        try:
            module.run()
            passed.append(name)
        except AssertionError as e:
            print(f"\n  ❌ FAILED: {name}\n     {e}")
            failed.append((name, str(e)))
        except Exception as e:
            print(f"\n  💥 ERROR: {name}\n     {type(e).__name__}: {e}")
            failed.append((name, f"{type(e).__name__}: {e}"))

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("  RESULTS SUMMARY")
    print("="*60)
    for name in passed:
        print(f"  ✅ PASSED: {name}")
    for name, err in failed:
        print(f"  ❌ FAILED: {name} — {err}")
    print(f"\n  Total: {len(passed)} passed, {len(failed)} failed out of {len(SCENARIOS)} scenarios")
    print("="*60 + "\n")

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
