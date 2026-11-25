"""Append-only, hash-chained ledger.

One JSON object per line. Every row carries the hash of the row before it and
its own SHA-256, so a hand-edited row breaks the chain at the row that was
edited. Rows are never rewritten or deleted: the only operation is ``append``.

The chain catches editing, which is what a reviewer actually worries about:
someone changing "refused" to "completed" after the fact.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

GENESIS = "0" * 64


def canonical(row: dict) -> str:
    """A stable JSON form, so the same content always hashes the same."""
    return json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def row_hash(row: dict) -> str:
    body = {key: value for key, value in row.items() if key != "hash"}
    return hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()


def payload_hash(payload: dict) -> str:
    """SHA-256 of the canonical payload. This is what an approval is bound to."""
    return hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Ledger:
    """An append-only JSONL file of hash-chained rows."""

    def __init__(self, path, clock=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock or utcnow
        if not self.path.exists():
            self.path.touch()

    def __len__(self) -> int:
        return len(self.rows())

    def rows(self) -> list[dict]:
        text = self.path.read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def last_hash(self) -> str:
        rows = self.rows()
        return rows[-1]["hash"] if rows else GENESIS

    def append(self, event: str, approval_id: str, payload_hash_value: str | None = None,
               **detail) -> dict:
        """Append one row and return it. There is no update and no delete."""
        rows = self.rows()
        row = {
            "seq": len(rows) + 1,
            "ts": self._clock().isoformat(),
            "event": event,
            "approval_id": approval_id,
            "payload_hash": payload_hash_value or "",
            "detail": detail,
            "prev": rows[-1]["hash"] if rows else GENESIS,
        }
        row["hash"] = row_hash(row)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(canonical(row) + "\n")
        return row

    def verify_chain(self) -> dict:
        """Recompute the chain and name the first row that does not hold.

        Returns ``{"ok": bool, "rows": int, "broken_seq": int|None, "reason": str|None}``.
        """
        rows = self.rows()
        previous = GENESIS
        for expected_seq, row in enumerate(rows, start=1):
            if row.get("seq") != expected_seq:
                return _broken(len(rows), expected_seq,
                               f"row {expected_seq} carries seq {row.get('seq')!r}")
            if row.get("prev") != previous:
                return _broken(len(rows), expected_seq,
                               f"row {expected_seq} does not chain onto the row before it")
            if row.get("hash") != row_hash(row):
                return _broken(len(rows), expected_seq,
                               f"row {expected_seq} was edited after it was written")
            previous = row["hash"]
        return {"ok": True, "rows": len(rows), "broken_seq": None, "reason": None}


def _broken(total: int, seq: int, reason: str) -> dict:
    return {"ok": False, "rows": total, "broken_seq": seq, "reason": reason}
