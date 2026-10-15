"""
connectors/lumos.py — Mock Lumos access governance client.

Simulates Lumos app access assignment for new hires based on department/role.
In production, replace with Lumos API calls.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from connectors.base import BaseConnector
from core.models.hire_state import HireState


# Default app bundles by department
DEPARTMENT_APP_BUNDLES: dict[str, list[str]] = {
    "Engineering":     ["github", "jira", "confluence", "figma", "datadog", "aws"],
    "Product":         ["jira", "confluence", "figma", "notion", "mixpanel"],
    "People":          ["workday", "ashby", "lattice", "notion", "greenhouse"],
    "Sales":           ["salesforce", "outreach", "zoom", "slack", "notion"],
    "Marketing":       ["hubspot", "notion", "canva", "figma", "google-analytics"],
    "Finance":         ["netsuite", "expensify", "notion", "google-workspace"],
    "Default":         ["slack", "google-workspace", "zoom", "notion"],
}


class LumosConnector(BaseConnector):
    """Mock Lumos access governance connector."""

    SECTION = "lumos_access"

    def _get_app_bundle(self, department: str) -> list[str]:
        """Return the app access list for a given department."""
        return DEPARTMENT_APP_BUNDLES.get(department, DEPARTMENT_APP_BUNDLES["Default"])

    def assign_access(self, hire: HireState) -> list[str]:
        """
        Assign the appropriate app access bundle to a new hire based on department.
        Returns the list of apps granted.
        """
        access_records = self._get_section(self.SECTION)
        apps = self._get_app_bundle(hire.department)

        access_records[hire.hire_id] = {
            "hire_id":     hire.hire_id,
            "department":  hire.department,
            "team":        hire.team,
            "apps":        apps,
            "assigned_at": datetime.utcnow().isoformat(),
            "status":      "ACTIVE",
        }
        self._set_section(self.SECTION, access_records)
        print(f"  [Lumos] ✅ Access assigned to {hire.display_name}: {apps}")
        return apps

    def update_access(self, hire_id: str, department: str, team: str) -> list[str]:
        """Update app access when department/team changes."""
        access_records = self._get_section(self.SECTION)
        apps = self._get_app_bundle(department)
        if hire_id in access_records:
            access_records[hire_id].update({
                "department":  department,
                "team":        team,
                "apps":        apps,
                "updated_at":  datetime.utcnow().isoformat(),
            })
            self._set_section(self.SECTION, access_records)
        print(f"  [Lumos] ✅ Access updated for hire {hire_id}: {apps}")
        return apps

    def revoke_access(self, hire_id: str) -> bool:
        """Revoke all app access for a hire (e.g., offer rescinded)."""
        access_records = self._get_section(self.SECTION)
        if hire_id not in access_records:
            return False
        access_records[hire_id]["status"] = "REVOKED"
        access_records[hire_id]["revoked_at"] = datetime.utcnow().isoformat()
        self._set_section(self.SECTION, access_records)
        print(f"  [Lumos] ⚠️  Access revoked for hire: {hire_id}")
        return True

    def get_access(self, hire_id: str) -> Optional[dict]:
        """Fetch current access record for a hire."""
        return self._get_section(self.SECTION).get(hire_id)
