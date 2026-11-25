"""The eight behaviours, each in a test named after it.

1  request stores a pending approval with a stable id and payload hash
2  approve marks it approved once
3  approve again raises AlreadyDecided
4  execute succeeds once, runs the action, records it, marks the token used
5  replay after success raises TokenSpent and the ledger records the refusal
6  a payload whose hash differs raises PayloadMismatch and is refused
7  an approval whose expires_at is past raises Expired
8  verify_chain detects a hand-edited row and names the first broken row
"""
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from approval import (
    AlreadyDecided,
    Approvals,
    Expired,
    Ledger,
    NoopClient,
    NotApproved,
    PayloadMismatch,
    PhoneClient,
    TokenSpent,
    UnknownApproval,
    payload_hash,
)

PAYLOAD = {"tool": "sheets.append_row", "row": {"invoice": "INV-1042", "amount_cad": 2682}}


class Clock:
    """A fixed, movable clock so expiry is a fact in the test, not a wait."""

    def __init__(self, start):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, **kwargs):
        self.now = self.now + timedelta(**kwargs)
        return self.now


class ApprovalTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "ledger.jsonl"
        self.ledger = Ledger(self.path)
        self.clock = Clock(self.ledger._clock())
        self.store = Approvals(self.ledger, clock=self.clock, ttl=timedelta(minutes=15))
        self.written = []

    def action(self, payload):
        self.written.append(payload)
        return {"ok": True}

    def events(self):
        return [row["event"] for row in self.ledger.rows()]

    def refusals(self):
        return [row["detail"].get("reason") for row in self.ledger.rows()
                if row["event"] == "refused"]


class RequestTests(ApprovalTestCase):
    def test_request_stores_a_pending_approval_with_a_stable_id_and_payload_hash(self):
        approval = self.store.request(PAYLOAD)
        self.assertTrue(approval.id.startswith("ap_"))
        self.assertEqual(approval.state, "pending")
        self.assertEqual(approval.payload_hash, payload_hash(PAYLOAD))
        self.assertEqual(self.store.get(approval.id).id, approval.id)
        self.assertEqual(self.events(), ["requested"])

    def test_the_same_payload_hashes_the_same_on_every_request(self):
        first = self.store.request(PAYLOAD)
        second = self.store.request(PAYLOAD)
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(first.payload_hash, second.payload_hash)

    def test_a_different_payload_hashes_differently(self):
        first = self.store.request(PAYLOAD)
        second = self.store.request({"tool": "sheets.append_row", "row": {"invoice": "INV-9999"}})
        self.assertNotEqual(first.payload_hash, second.payload_hash)

    def test_an_empty_payload_is_refused_before_anything_is_written(self):
        with self.assertRaises(ValueError):
            self.store.request({})
        self.assertEqual(len(self.ledger), 0)

    def test_an_unknown_id_is_refused(self):
        with self.assertRaises(UnknownApproval):
            self.store.approve("ap_missing")
        self.assertEqual(self.refusals(), ["unknown_approval"])


class ApproveTests(ApprovalTestCase):
    def test_approve_marks_it_approved_once(self):
        approval = self.store.request(PAYLOAD)
        approved = self.store.approve(approval.id)
        self.assertEqual(approved.state, "approved")
        self.assertIsNotNone(approved.decided_at)
        self.assertEqual(self.events(), ["requested", "approved"])

    def test_approve_again_raises_already_decided(self):
        approval = self.store.request(PAYLOAD)
        self.store.approve(approval.id)
        with self.assertRaises(AlreadyDecided):
            self.store.approve(approval.id)
        self.assertIn("already_decided", self.refusals())


class ExecuteTests(ApprovalTestCase):
    def test_execute_succeeds_once_and_marks_the_token_used(self):
        approval = self.store.request(PAYLOAD)
        self.store.approve(approval.id)
        receipt = self.store.execute(approval.id, PAYLOAD, self.action)
        self.assertEqual(receipt["payload_hash"], approval.payload_hash)
        self.assertEqual(self.store.get(approval.id).state, "spent")
        self.assertEqual(len(self.written), 1)
        self.assertEqual(self.events()[-2:], ["executed", "completed"])

    def test_execute_before_approval_is_refused(self):
        approval = self.store.request(PAYLOAD)
        with self.assertRaises(NotApproved):
            self.store.execute(approval.id, PAYLOAD, self.action)
        self.assertEqual(self.written, [])
        self.assertIn("not_approved", self.refusals())

    def test_an_action_that_raises_leaves_the_token_spent_not_reusable(self):
        approval = self.store.request(PAYLOAD)
        self.store.approve(approval.id)

        def explode(payload):
            raise RuntimeError("write failed")

        with self.assertRaises(RuntimeError):
            self.store.execute(approval.id, PAYLOAD, explode)
        with self.assertRaises(TokenSpent):
            self.store.execute(approval.id, PAYLOAD, self.action)


class ReplayTests(ApprovalTestCase):
    def test_replay_after_success_raises_token_spent_and_the_ledger_records_it(self):
        approval = self.store.request(PAYLOAD)
        self.store.approve(approval.id)
        self.store.execute(approval.id, PAYLOAD, self.action)
        with self.assertRaises(TokenSpent):
            self.store.execute(approval.id, PAYLOAD, self.action)
        self.assertEqual(len(self.written), 1, "the duplicate must not write")
        self.assertIn("token_spent", self.refusals())
        last = self.ledger.rows()[-1]
        self.assertEqual(last["event"], "refused")
        self.assertEqual(last["approval_id"], approval.id)


class MismatchTests(ApprovalTestCase):
    def test_a_payload_whose_hash_differs_raises_payload_mismatch(self):
        approval = self.store.request(PAYLOAD)
        self.store.approve(approval.id)
        tampered = {"tool": "sheets.append_row", "row": {"invoice": "INV-1042", "amount_cad": 9999}}
        with self.assertRaises(PayloadMismatch):
            self.store.execute(approval.id, tampered, self.action)
        self.assertEqual(self.written, [])
        self.assertIn("payload_mismatch", self.refusals())

    def test_a_mismatch_does_not_spend_the_token(self):
        approval = self.store.request(PAYLOAD)
        self.store.approve(approval.id)
        with self.assertRaises(PayloadMismatch):
            self.store.execute(approval.id, {"other": True}, self.action)
        self.store.execute(approval.id, PAYLOAD, self.action)
        self.assertEqual(len(self.written), 1)


class ExpiryTests(ApprovalTestCase):
    def test_an_approval_past_its_expiry_raises_expired_on_approve(self):
        approval = self.store.request(PAYLOAD, ttl=timedelta(seconds=-1))
        with self.assertRaises(Expired):
            self.store.approve(approval.id)
        self.assertIn("expired", self.refusals())

    def test_an_approval_that_expires_after_approval_raises_expired_on_execute(self):
        approval = self.store.request(PAYLOAD, ttl=timedelta(minutes=5))
        self.store.approve(approval.id)
        self.clock.advance(minutes=6)
        with self.assertRaises(Expired):
            self.store.execute(approval.id, PAYLOAD, self.action)
        self.assertEqual(self.written, [])

    def test_a_fresh_approval_is_not_expired(self):
        approval = self.store.request(PAYLOAD)
        self.store.approve(approval.id)
        self.clock.advance(minutes=14)
        self.store.execute(approval.id, PAYLOAD, self.action)
        self.assertEqual(len(self.written), 1)


class ChainTests(ApprovalTestCase):
    def _tamper(self, seq, **changes):
        rows = self.ledger.rows()
        for row in rows:
            if row["seq"] == seq:
                row.update(changes)
        self.path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
                             encoding="utf-8")

    def test_a_clean_chain_verifies(self):
        approval = self.store.request(PAYLOAD)
        self.store.approve(approval.id)
        self.store.execute(approval.id, PAYLOAD, self.action)
        chain = self.ledger.verify_chain()
        self.assertTrue(chain["ok"])
        self.assertEqual(chain["rows"], 4)
        self.assertIsNone(chain["broken_seq"])

    def test_verify_chain_detects_a_hand_edited_row_and_names_it(self):
        approval = self.store.request(PAYLOAD)
        self.store.approve(approval.id)
        self.store.execute(approval.id, PAYLOAD, self.action)
        self._tamper(3, event="approved")
        chain = self.ledger.verify_chain()
        self.assertFalse(chain["ok"])
        self.assertEqual(chain["broken_seq"], 3)
        self.assertIn("edited", chain["reason"])

    def test_editing_a_later_row_still_names_the_first_break(self):
        for _ in range(3):
            approval = self.store.request(PAYLOAD)
            self.store.approve(approval.id)
        self._tamper(2, event="executed")
        chain = self.ledger.verify_chain()
        self.assertFalse(chain["ok"])
        self.assertEqual(chain["broken_seq"], 2)

    def test_removing_a_row_is_detected(self):
        approval = self.store.request(PAYLOAD)
        self.store.approve(approval.id)
        self.store.request(PAYLOAD)
        rows = self.ledger.rows()
        self.path.write_text(json.dumps(rows[0], sort_keys=True) + "\n" +
                             json.dumps(rows[2], sort_keys=True) + "\n", encoding="utf-8")
        chain = self.ledger.verify_chain()
        self.assertFalse(chain["ok"])
        self.assertEqual(chain["broken_seq"], 2)

    def test_the_chain_links_each_row_to_the_previous_hash(self):
        self.store.request(PAYLOAD)
        self.store.request(PAYLOAD)
        rows = self.ledger.rows()
        self.assertEqual(rows[0]["prev"], "0" * 64)
        self.assertEqual(rows[1]["prev"], rows[0]["hash"])

    def test_append_only_never_rewrites_earlier_bytes(self):
        self.store.request(PAYLOAD)
        before = self.path.read_bytes()
        self.store.request(PAYLOAD)
        after = self.path.read_bytes()
        self.assertTrue(after.startswith(before))
        self.assertGreater(len(after), len(before))


class ClientTests(unittest.TestCase):
    def test_the_noop_client_records_what_it_showed(self):
        client = NoopClient()
        ledger = Ledger(Path(tempfile.mkdtemp()) / "l.jsonl")
        approval = Approvals(ledger).request(PAYLOAD)
        message = client.notify(approval, PAYLOAD)
        self.assertEqual(message["approval_id"], approval.id)
        self.assertEqual(client.shown, [message])
        self.assertTrue(client.confirm(approval.id))

    def test_the_phone_client_needs_the_exact_reply(self):
        printed = []
        client = PhoneClient(out=printed.append)
        ledger = Ledger(Path(tempfile.mkdtemp()) / "l.jsonl")
        approval = Approvals(ledger).request(PAYLOAD)
        client.notify(approval, PAYLOAD)
        self.assertTrue(any(approval.id in line for line in printed))
        self.assertTrue(client.confirm(approval.id, f"YES {approval.id}"))
        self.assertFalse(client.confirm(approval.id, "YES ap_someoneelse"))
        self.assertFalse(client.confirm(approval.id, None))


class DemoTests(unittest.TestCase):
    def test_the_demo_exits_zero_only_when_every_refusal_happened(self):
        completed = subprocess.run(
            [sys.executable, "demo.py"], cwd=ROOT, capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("demo PASSED", completed.stdout)
        for reason in ("AlreadyDecided", "PayloadMismatch", "TokenSpent", "Expired"):
            self.assertIn(reason, completed.stdout)
        self.assertIn("writes that actually happened: 1", completed.stdout)
        self.assertIn("ok True", completed.stdout)


if __name__ == "__main__":
    unittest.main()
