"""
config.py — Central configuration loader.
Reads from .env file and exposes typed settings for the entire project.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Gemini / Google AI ───────────────────────────────────────────────────────
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL: str   = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

# ── Feature Flags ────────────────────────────────────────────────────────────
CLAUDE_RESOLVER_ENABLED: bool = os.getenv("CLAUDE_RESOLVER_ENABLED", "true").lower() == "true"

# ── Storage Paths ────────────────────────────────────────────────────────────
AUDIT_LOG_PATH: str       = os.getenv("AUDIT_LOG_PATH", "audit/event_log.jsonl")
IDEMPOTENCY_DB_PATH: str  = os.getenv("IDEMPOTENCY_DB_PATH", "data/idempotency.db")
MOCK_DB_PATH: str         = os.getenv("MOCK_DB_PATH", "data/mock_db.json")
