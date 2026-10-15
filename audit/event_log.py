"""
audit/event_log.py — Immutable append-only JSONL audit trail.

Every action taken by the pipeline is recorded here.
The log is append-only (never overwritten) and human-readable.
Each line is a complete JSON object — grep-friendly.

Format per line:
  {
    "timestamp": "...",
    "hire_id": "...",
    "action": "...",       # e.g., "STATE_TRANSITION", "OKTA_PROVISIONED"
    "detail": {...},       # Action-specific metadata
    "version": 3           # HireState version at time of action
  }
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

from config import AUDIT_LOG_PATH


class AuditLogger:
    """
    Append-only JSONL audit logger.

    Usage:
        logger = AuditLogger()
        logger.log(hire_id="H-001", action="OKTA_PROVISIONED", detail={...}, version=2)
    """

    def __init__(self, log_path: str = AUDIT_LOG_PATH):
        self.log_path = log_path
        os.makedirs(os.path.dirname(log_path), exist_ok=True)

    def log(
        self,
        hire_id: str,
        action: str,
        detail: dict[str, Any],
        version: int = 0,
    ) -> None:
        """Append a single audit entry to the log file."""
        entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "hire_id":   hire_id,
            "action":    action,
            "version":   version,
            "detail":    detail,
        }
        with open(self.log_path, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def read_all(self) -> list[dict]:
        """Read all audit entries from the log file."""
        if not os.path.exists(self.log_path):
            return []
        entries = []
        with open(self.log_path, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        return entries

    def read_for_hire(self, hire_id: str) -> list[dict]:
        """Return all audit entries for a specific hire_id."""
        return [e for e in self.read_all() if e.get("hire_id") == hire_id]

    def clear_for_testing(self) -> None:
        """
        Delete the log file. FOR TESTING ONLY.
        Never call this in production.
        """
        if os.path.exists(self.log_path):
            os.remove(self.log_path)
