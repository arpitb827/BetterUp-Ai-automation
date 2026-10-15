"""
connectors/okta.py — Mock Okta identity provisioning client.

Simulates Okta user provisioning for new hires.
In production, replace with Okta Management API calls.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from connectors.base import BaseConnector
from core.models.hire_state import HireState


class OktaConnector(BaseConnector):
    """Mock Okta connector for identity provisioning."""

    SECTION = "okta_users"

    def _generate_work_email(self, hire: HireState) -> str:
        """
        Generate work email from legal name.
        Format: firstname.lastname@betterup.com
        Handles hyphens and apostrophes by stripping them.
        """
        first = hire.legal_first_name.lower().replace(" ", "").replace("-", "").replace("'", "")
        last  = hire.legal_last_name.lower().replace(" ", "").replace("-", "").replace("'", "")
        return f"{first}.{last}@betterup.com"

    def provision_user(self, hire: HireState) -> str:
        """
        Create a new Okta user account for the hire.
        Returns the generated work email address.
        """
        users = self._get_section(self.SECTION)

        work_email = self._generate_work_email(hire)

        users[hire.hire_id] = {
            "hire_id":          hire.hire_id,
            "work_email":       work_email,
            "legal_first_name": hire.legal_first_name,
            "legal_last_name":  hire.legal_last_name,
            "job_title":        hire.job_title,
            "department":       hire.department,
            "manager_email":    hire.manager_email,
            "start_date":       hire.start_date.isoformat(),
            "status":           "PROVISIONED",
            "provisioned_at":   datetime.utcnow().isoformat(),
        }
        self._set_section(self.SECTION, users)
        print(f"  [Okta] ✅ User provisioned: {work_email}")
        return work_email

    def update_user(self, hire_id: str, fields: dict) -> bool:
        """Update an existing Okta user's attributes."""
        users = self._get_section(self.SECTION)
        if hire_id not in users:
            print(f"  [Okta] ❌ User not found for hire: {hire_id}")
            return False
        users[hire_id].update(fields)
        users[hire_id]["updated_at"] = datetime.utcnow().isoformat()
        self._set_section(self.SECTION, users)
        print(f"  [Okta] ✅ User updated: hire_id={hire_id}, fields={list(fields.keys())}")
        return True

    def deprovision_user(self, hire_id: str) -> bool:
        """Deprovision (suspend) an Okta user account."""
        users = self._get_section(self.SECTION)
        if hire_id not in users:
            return False
        users[hire_id]["status"] = "DEPROVISIONED"
        users[hire_id]["deprovisioned_at"] = datetime.utcnow().isoformat()
        self._set_section(self.SECTION, users)
        print(f"  [Okta] ⚠️  User deprovisioned: hire_id={hire_id}")
        return True

    def get_user(self, hire_id: str) -> Optional[dict]:
        """Fetch an Okta user record by hire_id."""
        return self._get_section(self.SECTION).get(hire_id)
