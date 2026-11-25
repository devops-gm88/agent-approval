"""End to end: request -> approve -> execute -> duplicate rejected.

Run it:

    python3 demo.py

Exit code 0 only when all four refusals happened and the ledger verifies:
double approval, a payload that is not the approved one, a replay after a
successful write, and an expired approval. It also checks that the write
function ran exactly once, which is the point of the whole exercise.
"""
from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

from approval import (
    AlreadyDecided,
    Approvals,
    Expired,
    Ledger,
    NoopClient,
    PayloadMismatch,
    PhoneClient,
    TokenSpent,
)

HERE = Path(__file__).resolve().parent
LEDGER_PATH = HERE / "demo_ledger.jsonl"
EFFECTS_PATH = HERE / "demo_effects.jsonl"

PAYLOAD = {
    "tool": "sheets.append_row",
    "sheet": "Q4-reconciliation",
    "row": {"invoice": "INV-1042", "amount_cad": 2682, "status": "held"},
}


def write_row(payload: dict) -> dict:
    """The real action: the one thing the token exists to allow exactly once."""
    with EFFECTS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")
    return {"written": payload["row"]["invoice"]}


def refusal(store: Approvals, label: str, call) -> bool:
    """Run a call that must be refused. Returns True when it was."""
    try:
        call()
    except Exception as error:  # noqa: BLE001 - the refusal type is printed
        print(f"  refused as expected: {type(error).__name__}: {error}")
        return True
    print(f"  FAILED: {label} was not refused")
    return False


def main() -> int:
    for path in (LEDGER_PATH, EFFECTS_PATH):
        if path.exists():
            path.unlink()

    ledger = Ledger(LEDGER_PATH)
    store = Approvals(ledger)
    client = PhoneClient()
    refusals: set[str] = set()

    print("step 1  request an approval for a write the agent cannot perform alone")
    approval = store.request(PAYLOAD)
    print(f"  pending {approval.id}  payload {approval.payload_hash[:16]}...  "
          f"expires {approval.expires_at.isoformat()}")
    client.notify(approval, PAYLOAD)

    print("step 2  the owner approves it once")
    approved = store.approve(approval.id)
    print(f"  {approved.id} is {approved.state}")

    print("step 3  a second approval of the same id must be refused")
    if refusal(store, "double approval", lambda: store.approve(approval.id)):
        refusals.add("already_decided")

    print("step 4  a payload that is not the approved payload must be refused")
    tampered = dict(PAYLOAD, row={"invoice": "INV-1042", "amount_cad": 9999, "status": "paid"})
    if refusal(store, "payload mismatch", lambda: store.execute(approval.id, tampered, write_row)):
        refusals.add("payload_mismatch")

    print("step 5  the approved payload executes, and the write happens exactly once")
    receipt = store.execute(approval.id, PAYLOAD, write_row)
    print(f"  executed {receipt['approval_id']}  result {receipt['result']}")

    print("step 6  the same token is offered again; a replay must be refused")
    if refusal(store, "replay", lambda: store.execute(approval.id, PAYLOAD, write_row)):
        refusals.add("token_spent")

    print("step 7  an approval that expired before use must be refused")
    expired = store.request(PAYLOAD, ttl=timedelta(seconds=-1))
    if refusal(store, "expired approval (approve)", lambda: store.approve(expired.id)):
        refusals.add("expired")
    refusal(store, "expired approval (execute)", lambda: store.execute(expired.id, PAYLOAD, write_row))

    written = EFFECTS_PATH.read_text(encoding="utf-8").splitlines() if EFFECTS_PATH.exists() else []
    print(f"step 8  writes that actually happened: {len(written)}")
    print("step 9  ledger chain")
    chain = ledger.verify_chain()
    print(f"  rows {chain['rows']}  ok {chain['ok']}")
    events = [row["event"] for row in ledger.rows()]
    print(f"  events {events}")

    required = {"already_decided", "payload_mismatch", "token_spent", "expired"}
    missing = sorted(required - refusals)
    ok = not missing and chain["ok"] and len(written) == 1
    if missing:
        print(f"FAILED: refusals that did not happen: {missing}")
    if len(written) != 1:
        print(f"FAILED: expected exactly one write, got {len(written)}")
    print("demo PASSED: one write, four refusals, ledger verified" if ok else "demo FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
