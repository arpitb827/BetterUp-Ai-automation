"""
core/__init__.py — Package init for core.
"""

from .state_machine import StateMachine, InvalidTransitionError, ALLOWED_TRANSITIONS
from .reconciler import Reconciler, HireStateDiff, ChangedField, ConflictType
from .idempotency import IdempotencyStore

__all__ = [
    "StateMachine",
    "InvalidTransitionError",
    "ALLOWED_TRANSITIONS",
    "Reconciler",
    "HireStateDiff",
    "ChangedField",
    "ConflictType",
    "IdempotencyStore",
]
