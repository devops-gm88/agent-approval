"""Request, approve, execute: a single-use, expiring, payload-bound token.

The rule this module enforces is the one a reviewer should be able to repeat
from memory: an approval is not a permission. It is a token that names exactly
one payload, can be spent exactly once, stops working at a fixed time, and
writes what happened to an append-only ledger.

Every refusal is recorded in the ledger before it is raised, so the log shows
the attempt that was turned away, not only the ones that succeeded.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .ledger import Ledger, payload_hash, utcnow

DEFAULT_TTL = timedelta(minutes=15)


class ApprovalError(Exception):
    """Base class for every refusal."""


class UnknownApproval(ApprovalError):
    """No approval with that id was ever requested."""


class NotApproved(ApprovalError):
    """The approval exists but nobody has approved it yet."""


class AlreadyDecided(ApprovalError):
    """This approval has already been approved (or refused)."""


class PayloadMismatch(ApprovalError):
    """The payload offered at execution is not the payload that was approved."""


class Expired(ApprovalError):
    """The approval's expiry time has passed."""


class TokenSpent(ApprovalError):
    """This token was already used. A second write with it is a replay."""


PENDING = "pending"
APPROVED = "approved"
SPENT = "spent"


@dataclass
class Approval:
    id: str
    payload_hash: str
    created_at: datetime
    expires_at: datetime
    state: str = PENDING
    decided_at: datetime | None = None
    spent_at: datetime | None = None
    history: list[str] = field(default_factory=list)

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "payload_hash": self.payload_hash,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "state": self.state,
            "decided_at": self.decided_at.isoformat() if self.decided_at else None,
            "spent_at": self.spent_at.isoformat() if self.spent_at else None,
            "history": list(self.history),
        }


class Approvals:
    """A store of approvals backed by one :class:`Ledger`."""

    def __init__(self, ledger: Ledger, clock=None, ttl: timedelta = DEFAULT_TTL):
        self.ledger = ledger
        self._clock = clock or ledger._clock
        self.ttl = ttl
        self._by_id: dict[str, Approval] = {}

    def request(self, payload: dict, ttl: timedelta | None = None) -> Approval:
        """Open a pending approval bound to this payload's hash."""
        if not isinstance(payload, dict) or not payload:
            raise ValueError("an approval must be bound to a non-empty payload dict")
        now = self._clock()
        approval = Approval(
            id="ap_" + uuid.uuid4().hex[:16],
            payload_hash=payload_hash(payload),
            created_at=now,
            expires_at=now + (ttl if ttl is not None else self.ttl),
        )
        self._by_id[approval.id] = approval
        self.ledger.append("requested", approval.id, approval.payload_hash,
                           expires_at=approval.expires_at.isoformat())
        return approval

    def get(self, approval_id: str) -> Approval:
        approval = self._by_id.get(approval_id)
        if approval is None:
            self.ledger.append("refused", approval_id or "", None, reason="unknown_approval")
            raise UnknownApproval(f"no approval with id {approval_id!r}")
        return approval

    def approve(self, approval_id: str) -> Approval:
        """Approve once. A second approval of the same id is a refusal."""
        approval = self.get(approval_id)
        if approval.state != PENDING:
            self.ledger.append("refused", approval.id, approval.payload_hash,
                               reason="already_decided", state=approval.state)
            raise AlreadyDecided(
                f"approval {approval.id} is already {approval.state}; it cannot be approved again"
            )
        now = self._clock()
        if approval.is_expired(now):
            self.ledger.append("refused", approval.id, approval.payload_hash, reason="expired")
            raise Expired(f"approval {approval.id} expired at {approval.expires_at.isoformat()}")
        approval.state = APPROVED
        approval.decided_at = now
        approval.history.append("approved")
        self.ledger.append("approved", approval.id, approval.payload_hash,
                           expires_at=approval.expires_at.isoformat())
        return approval

    def execute(self, approval_id: str, payload: dict, action=None):
        """Spend the token on this payload, run ``action(payload)``, record it.

        Refusals, in the order they are checked: unknown id, expired token,
        already spent (a replay), not approved, then a payload whose hash is not
        the approved hash. Each one is written to the ledger before it raises.
        """
        approval = self.get(approval_id)
        now = self._clock()

        if approval.state == SPENT:
            self.ledger.append("refused", approval.id, payload_hash(payload), reason="token_spent")
            raise TokenSpent(
                f"approval {approval.id} was already spent at "
                f"{approval.spent_at.isoformat() if approval.spent_at else 'unknown'}; "
                "a second write with it is a replay"
            )
        if approval.is_expired(now):
            self.ledger.append("refused", approval.id, payload_hash(payload), reason="expired")
            raise Expired(f"approval {approval.id} expired at {approval.expires_at.isoformat()}")
        if approval.state != APPROVED:
            self.ledger.append("refused", approval.id, payload_hash(payload),
                               reason="not_approved", state=approval.state)
            raise NotApproved(f"approval {approval.id} is {approval.state}; approve it first")

        offered = payload_hash(payload)
        if offered != approval.payload_hash:
            self.ledger.append("refused", approval.id, offered, reason="payload_mismatch",
                               approved_hash=approval.payload_hash)
            raise PayloadMismatch(
                f"payload hash {offered[:12]}... is not the approved hash "
                f"{approval.payload_hash[:12]}..."
            )

        approval.state = SPENT
        approval.spent_at = now
        approval.history.append("executed")
        self.ledger.append("executed", approval.id, offered)
        result = action(payload) if action is not None else None
        self.ledger.append("completed", approval.id, offered,
                           result_hash=payload_hash({"result": repr(result)}))
        return {"approval_id": approval.id, "payload_hash": offered, "result": result}

    def ledger_view(self) -> list[dict]:
        return self.ledger.rows()


def utcnow_tz() -> datetime:
    """A timezone-aware now, exported so callers can build their own clocks."""
    return datetime.now(timezone.utc)
