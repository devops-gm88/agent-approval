"""agent-approval: a single-use, expiring, payload-bound approval with a hash-chained ledger.

    from approval import Approvals, Ledger, TokenSpent

An approval is not a permission. It names one payload, it works once, it stops
working at a fixed time, and every attempt (including the refused ones) lands
in an append-only ledger whose rows are chained by SHA-256.
"""
from __future__ import annotations

from .approvals import (
    APPROVED,
    DEFAULT_TTL,
    PENDING,
    SPENT,
    AlreadyDecided,
    Approval,
    ApprovalError,
    Approvals,
    Expired,
    NotApproved,
    PayloadMismatch,
    TokenSpent,
    UnknownApproval,
)
from .client import NoopClient, PhoneClient
from .ledger import GENESIS, Ledger, payload_hash, row_hash, utcnow

__all__ = [
    "Approvals",
    "Approval",
    "ApprovalError",
    "UnknownApproval",
    "NotApproved",
    "AlreadyDecided",
    "PayloadMismatch",
    "Expired",
    "TokenSpent",
    "Ledger",
    "payload_hash",
    "row_hash",
    "utcnow",
    "GENESIS",
    "PENDING",
    "APPROVED",
    "SPENT",
    "DEFAULT_TTL",
    "NoopClient",
    "PhoneClient",
]

__version__ = "1.0.0"
