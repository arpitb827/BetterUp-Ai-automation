"""
connectors/ashby.py — Mock Ashby ATS client.

Simulates the Ashby API for candidate/hire data retrieval.
In production, replace with real Ashby REST API calls + webhook verification.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from connectors.base import BaseConnector
from core.models.hire_state import HireState, HireSource, Address, StageGates, HireStatus


class AshbyConnector(BaseConnector):
    """Mock Ashby ATS connector."""

    SECTION = "hires"

    def get_hire(self, hire_id: str) -> Optional[HireState]:
        """Fetch a hire record by Ashby application ID."""
        hires = self._get_section(self.SECTION)
        data = hires.get(hire_id)
        if not data:
            return None
        return HireState(**data)

    def save_hire(self, hire: HireState) -> None:
        """
        Upsert a hire record into the mock Ashby store.
        Used by test fixtures to seed data.
        """
        hires = self._get_section(self.SECTION)
        hires[hire.hire_id] = hire.model_dump()
        self._set_section(self.SECTION, hires)

    def list_hires(self) -> list[HireState]:
        """Return all hires currently in the mock store."""
        hires = self._get_section(self.SECTION)
        return [HireState(**data) for data in hires.values()]

    def emit_hire_created_event(self, hire: HireState) -> dict:
        """
        Simulate the webhook payload Ashby would POST to our endpoint
        when a new offer is accepted.
        """
        return {
            "event_id":   f"ashby_evt_{hire.hire_id}_created",
            "event_type": "ASHBY_HIRE_CREATED",
            "source":     "ASHBY",
            "hire_id":    hire.hire_id,
            "payload": {
                "hire_id":           hire.hire_id,
                "ashby_id":          hire.ashby_id,
                "source":            hire.source.value,
                "legal_first_name":  hire.legal_first_name,
                "legal_last_name":   hire.legal_last_name,
                "preferred_name":    hire.preferred_name,
                "personal_email":    hire.personal_email,
                "job_title":         hire.job_title,
                "department":        hire.department,
                "team":              hire.team,
                "manager_email":     hire.manager_email,
                "start_date":        hire.start_date.isoformat(),
                "shipping_address":  hire.shipping_address.model_dump(),
                "laptop_model":      hire.laptop_model,
            },
        }

    def emit_hire_updated_event(self, hire: HireState, changed_fields: dict) -> dict:
        """
        Simulate the webhook payload Ashby would POST when a hire record is updated.
        `changed_fields` is a dict of {field_name: new_value}.
        """
        payload = {"hire_id": hire.hire_id, "ashby_id": hire.ashby_id}
        payload.update(changed_fields)
        return {
            "event_id":   f"ashby_evt_{hire.hire_id}_{datetime.utcnow().timestamp():.0f}",
            "event_type": "ASHBY_HIRE_UPDATED",
            "source":     "ASHBY",
            "hire_id":    hire.hire_id,
            "payload":    payload,
        }
