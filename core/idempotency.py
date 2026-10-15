"""
core/idempotency.py — SQLite-backed idempotency store.

Prevents duplicate processing of replayed webhooks, which could cause:
  - Double laptop orders
  - Duplicate Okta accounts
  - Multiple Slack DMs for the same hire
  - Redundant provisioning calls

Design:
  - Key = SHA256(hire_id + event_type + sorted_payload_json)  [from ChangeEvent]
  - Backed by SQLite — zero infra, survives restarts
  - TTL of 30 days — old keys are purged to keep the DB lean
"""

from __future__ import annotations

import sqlite3
import os
from datetime import datetime, timedelta
from contextlib import contextmanager
from typing import Optional

from config import IDEMPOTENCY_DB_PATH


# ── Constants ─────────────────────────────────────────────────────────────────

TTL_DAYS = 30  # Keys older than this are pruned during maintenance


# ── IdempotencyStore ──────────────────────────────────────────────────────────

class IdempotencyStore:
    """
    Thread-safe SQLite-backed idempotency store.

    Usage:
        store = IdempotencyStore()
        if store.has_seen(event.idempotency_key):
            return  # Already processed — skip
        # ... process the event ...
        store.mark_seen(event.idempotency_key, event.hire_id)
    """

    def __init__(self, db_path: str = IDEMPOTENCY_DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._initialize_db()

    # ── DB Setup ──────────────────────────────────────────────────────────────

    def _initialize_db(self) -> None:
        """Create the idempotency table if it does not exist."""
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS idempotency_keys (
                    key         TEXT PRIMARY KEY,
                    hire_id     TEXT NOT NULL,
                    seen_at     TEXT NOT NULL,
                    expires_at  TEXT NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_hire_id ON idempotency_keys(hire_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_expires_at ON idempotency_keys(expires_at)"
            )

    @contextmanager
    def _connect(self):
        """Context manager for SQLite connections with auto-commit."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ── Core Operations ───────────────────────────────────────────────────────

    def has_seen(self, key: str) -> bool:
        """
        Returns True if this idempotency key has already been processed
        AND has not yet expired.
        """
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT key FROM idempotency_keys WHERE key = ? AND expires_at > ?",
                (key, now),
            ).fetchone()
        return row is not None

    def mark_seen(self, key: str, hire_id: str) -> None:
        """
        Record that this key has been processed.
        Sets TTL to now + TTL_DAYS.
        If the key already exists (race condition), silently ignores.
        """
        now = datetime.utcnow()
        expires_at = (now + timedelta(days=TTL_DAYS)).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO idempotency_keys (key, hire_id, seen_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (key, hire_id, now.isoformat(), expires_at),
            )

    def remove(self, key: str) -> None:
        """
        Delete a key from the store.
        Useful in tests or when a previously failed event needs to be reprocessed.
        """
        with self._connect() as conn:
            conn.execute("DELETE FROM idempotency_keys WHERE key = ?", (key,))

    # ── Maintenance ───────────────────────────────────────────────────────────

    def purge_expired(self) -> int:
        """
        Delete all expired keys from the store.
        Returns the number of rows deleted.
        Run this periodically (e.g., daily cron) to keep the DB lean.
        """
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM idempotency_keys WHERE expires_at <= ?", (now,)
            )
            return cursor.rowcount

    def keys_for_hire(self, hire_id: str) -> list[dict]:
        """Return all non-expired idempotency records for a given hire_id."""
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT key, hire_id, seen_at, expires_at
                FROM idempotency_keys
                WHERE hire_id = ? AND expires_at > ?
                ORDER BY seen_at DESC
                """,
                (hire_id, now),
            ).fetchall()
        return [dict(row) for row in rows]

    def count(self) -> int:
        """Return total number of non-expired keys in the store."""
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM idempotency_keys WHERE expires_at > ?",
                (now,),
            ).fetchone()
        return row["cnt"] if row else 0

    def reset_for_testing(self) -> None:
        """
        Wipe all keys from the store. 
        FOR TESTING ONLY — do not call in production.
        """
        with self._connect() as conn:
            conn.execute("DELETE FROM idempotency_keys")
