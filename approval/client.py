"""Two clients for the approval prompt: a no-op one for tests and a phone-like one.

The client is only the surface that asks the human. It has no power to approve
anything by itself: :func:`approval.approvals.Approvals.approve` is a separate
call, so a bug in a notification channel cannot become a grant.
"""
from __future__ import annotations


class NoopClient:
    """Records what it was asked to show and always confirms. For tests."""

    name = "noop"

    def __init__(self):
        self.shown: list[dict] = []

    def notify(self, approval, payload=None) -> dict:
        message = {
            "channel": self.name,
            "approval_id": approval.id,
            "payload_hash": approval.payload_hash,
            "expires_at": approval.expires_at.isoformat(),
            "payload_keys": sorted((payload or {}).keys()),
        }
        self.shown.append(message)
        return message

    def confirm(self, approval_id: str, reply: str | None = None) -> bool:
        return True


class PhoneClient:
    """Prints the prompt a phone would show, and reads the owner's reply."""

    name = "phone"

    def __init__(self, out=print):
        self.out = out
        self.shown: list[str] = []

    def notify(self, approval, payload=None) -> str:
        keys = ", ".join(sorted((payload or {}).keys())) or "no fields shown"
        lines = [
            "  [phone] approval request",
            f"    id      {approval.id}",
            f"    payload {approval.payload_hash[:16]}...  ({keys})",
            f"    expires {approval.expires_at.isoformat()}",
            f"    reply   YES {approval.id}   or   NO {approval.id}",
        ]
        text = "\n".join(lines)
        self.shown.append(text)
        self.out(text)
        return text

    def confirm(self, approval_id: str, reply: str | None = None) -> bool:
        """A reply is required. ``None`` means the owner did not answer."""
        if reply is None:
            return False
        return reply.strip() == f"YES {approval_id}"
