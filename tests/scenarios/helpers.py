"""
tests/scenarios/helpers.py — Shared test helpers and fixture factories.
"""

from __future__ import annotations

import json
import os
from datetime import date, timedelta

from core.models.hire_state import HireState, HireSource, Address, StageGates, HireStatus


def make_address(
    street="123 Market St",
    city="San Francisco",
    state="CA",
    zip_code="94105",
) -> Address:
    return Address(street_line_1=street, city=city, state=state, zip_code=zip_code)


def make_hire(
    hire_id: str = "HIRE-001",
    first_name: str = "Jane",
    last_name: str = "Smith",
    email: str = "jane.smith@gmail.com",
    start_date: date = None,
    department: str = "Engineering",
    team: str = "Platform",
    address: Address = None,
    source: HireSource = HireSource.ASHBY,
    workday_id: str = None,
    ashby_id: str = None,
) -> HireState:
    if start_date is None:
        start_date = date.today() + timedelta(days=21)
    if address is None:
        address = make_address()
    if source == HireSource.ASHBY and not ashby_id:
        ashby_id = f"ASHBY-{hire_id}"
    if source == HireSource.WORKDAY and not workday_id:
        workday_id = f"WD-{hire_id[:8].upper()}"

    return HireState(
        hire_id          = hire_id,
        ashby_id         = ashby_id,
        workday_id       = workday_id,
        source           = source,
        legal_first_name = first_name,
        legal_last_name  = last_name,
        personal_email   = email,
        job_title        = "Software Engineer",
        department       = department,
        team             = team,
        manager_email    = "manager@betterup.com",
        start_date       = start_date,
        shipping_address = address,
    )


def reset_mock_db(mock_db_path: str = "data/mock_db.json") -> None:
    """Wipe all mock data between test scenarios."""
    empty = {
        "hires": {},
        "employees": {},
        "okta_users": {},
        "lumos_access": {},
        "equipment_orders": {},
        "slack_messages": []
    }
    os.makedirs(os.path.dirname(mock_db_path), exist_ok=True)
    with open(mock_db_path, "w") as f:
        json.dump(empty, f, indent=2)


def print_section(title: str) -> None:
    width = 60
    print(f"\n{'='*width}")
    print(f"  {title}")
    print(f"{'='*width}")


def print_result(result: dict) -> None:
    print(f"\n  📊 Result: status={result['status']}")
    print(f"  💬 Message: {result['message']}")
    if result.get("hire_state"):
        hs = result["hire_state"]
        print(f"  👤 Hire: {hs.get('legal_first_name')} {hs.get('legal_last_name')}")
        print(f"  📌 Status: {hs.get('status')} | Version: {hs.get('version')}")
        if hs.get("work_email"):
            print(f"  📧 Work Email: {hs.get('work_email')}")
        gates = hs.get("stage_gates", {})
        passed = [g for g, v in gates.items() if v]
        pending = [g for g, v in gates.items() if not v]
        if passed:
            print(f"  ✅ Gates done: {passed}")
        if pending:
            print(f"  ⏳ Gates pending: {pending}")
