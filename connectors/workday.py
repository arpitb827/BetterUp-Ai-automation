"""
connectors/workday.py — Mock Workday HRIS client.

Simulates Workday employee record management.
In production, replace with Workday REST/SOAP API calls.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from connectors.base import BaseConnector
from core.models.hire_state import HireState, HireSource


class WorkdayConnector(BaseConnector):
    """Mock Workday HRIS connector."""

    SECTION = "employees"

    def get_employee(self, workday_id: str) -> Optional[dict]:
        """Fetch a raw employee record by Workday ID."""
        employees = self._get_section(self.SECTION)
        return employees.get(workday_id)

    def get_employee_by_hire_id(self, hire_id: str) -> Optional[dict]:
        """Look up an employee by their hire_id cross-reference field."""
        employees = self._get_section(self.SECTION)
        for emp in employees.values():
            if emp.get("hire_id") == hire_id:
                return emp
        return None

    def create_employee(self, hire: HireState) -> str:
        """
        Create a new Workday employee record from a HireState.
        Returns the generated workday_id.
        """
        workday_id = hire.workday_id or f"WD-{hire.hire_id[:8].upper()}"
        employees = self._get_section(self.SECTION)
        employees[workday_id] = {
            "workday_id":       workday_id,
            "hire_id":          hire.hire_id,
            "legal_first_name": hire.legal_first_name,
            "legal_last_name":  hire.legal_last_name,
            "personal_email":   hire.personal_email,
            "job_title":        hire.job_title,
            "department":       hire.department,
            "team":             hire.team,
            "manager_email":    hire.manager_email,
            "start_date":       hire.start_date.isoformat(),
            "created_at":       datetime.utcnow().isoformat(),
            "updated_at":       datetime.utcnow().isoformat(),
        }
        self._set_section(self.SECTION, employees)
        print(f"  [Workday] ✅ Employee record created: {workday_id} — {hire.full_legal_name}")
        return workday_id

    def update_employee(self, workday_id: str, fields: dict) -> bool:
        """Update specific fields on an existing Workday employee record."""
        employees = self._get_section(self.SECTION)
        if workday_id not in employees:
            print(f"  [Workday] ❌ Employee not found: {workday_id}")
            return False
        employees[workday_id].update(fields)
        employees[workday_id]["updated_at"] = datetime.utcnow().isoformat()
        self._set_section(self.SECTION, employees)
        print(f"  [Workday] ✅ Employee updated: {workday_id} — Fields: {list(fields.keys())}")
        return True

    def emit_employee_created_event(self, hire: HireState) -> dict:
        """
        Simulate webhook payload for an off-cycle hire created directly in Workday.
        Off-cycle hires have no Ashby record.
        """
        return {
            "event_id":   f"wd_evt_{hire.hire_id}_created",
            "event_type": "WORKDAY_EMPLOYEE_CREATED",
            "source":     "WORKDAY",
            "hire_id":    hire.hire_id,
            "payload": {
                "hire_id":          hire.hire_id,
                "workday_id":       hire.workday_id,
                "source":           HireSource.WORKDAY.value,
                "legal_first_name": hire.legal_first_name,
                "legal_last_name":  hire.legal_last_name,
                "preferred_name":   hire.preferred_name,
                "personal_email":   hire.personal_email,
                "job_title":        hire.job_title,
                "department":       hire.department,
                "team":             hire.team,
                "manager_email":    hire.manager_email,
                "start_date":       hire.start_date.isoformat(),
                "shipping_address": hire.shipping_address.model_dump(),
                "laptop_model":     hire.laptop_model,
            },
        }

    def emit_employee_updated_event(self, hire: HireState, changed_fields: dict) -> dict:
        """Simulate webhook payload for a field update made directly in Workday."""
        payload = {
            "hire_id":    hire.hire_id,
            "workday_id": hire.workday_id,
        }
        payload.update(changed_fields)
        return {
            "event_id":   f"wd_evt_{hire.hire_id}_{datetime.utcnow().timestamp():.0f}",
            "event_type": "WORKDAY_EMPLOYEE_UPDATED",
            "source":     "WORKDAY",
            "hire_id":    hire.hire_id,
            "payload":    payload,
        }
