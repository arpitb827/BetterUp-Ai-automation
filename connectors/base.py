"""
connectors/base.py — Base connector class with shared mock DB access.

All connectors inherit from BaseConnector.
They share a single mock_db.json as their persistent state store.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any

from config import MOCK_DB_PATH

# Global re-entrant lock for mock database file access
_MOCK_DB_LOCK = threading.RLock()


class BaseConnector:
    """
    Base class for all mock API connectors.

    Provides shared read/write access to the mock DB (data/mock_db.json).
    All operations are synchronized using a re-entrant thread lock.
    """

    def _load_db(self) -> dict:
        """Load the full mock DB from disk thread-safely."""
        with _MOCK_DB_LOCK:
            if not os.path.exists(MOCK_DB_PATH):
                return {}
            with open(MOCK_DB_PATH, "r") as f:
                return json.load(f)

    def _save_db(self, db: dict) -> None:
        """Persist the full mock DB to disk thread-safely."""
        with _MOCK_DB_LOCK:
            os.makedirs(os.path.dirname(MOCK_DB_PATH), exist_ok=True)
            with open(MOCK_DB_PATH, "w") as f:
                json.dump(db, f, indent=2, default=str)

    def _get_section(self, section: str) -> dict:
        with _MOCK_DB_LOCK:
            return self._load_db().get(section, {})

    def _set_section(self, section: str, data: Any) -> None:
        with _MOCK_DB_LOCK:
            db = self._load_db()
            db[section] = data
            self._save_db(db)
