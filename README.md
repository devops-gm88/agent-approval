# agent-approval

An approval is not a permission. It is a single-use, expiring, payload-bound token, and the ledger
proves what happened.

[![CI](https://github.com/devops-gm88/agent-approval/actions/workflows/ci.yml/badge.svg)](https://github.com/devops-gm88/agent-approval/actions/workflows/ci.yml)
**25 unittest tests**, one demo, Python 3.11 / 3.12 / 3.13, standard library only. The demo ends with
one write performed, four refusals recorded, and a verified 10-row hash chain.

```bash
python3 demo.py
```

That is the whole install: no dependencies, no venv, no network, no credentials.

## What it does

An agent wants to take a real action: append a row to a sheet, send an invoice, mark a record paid.
The action is bound to a payload, the payload is bound to a hash, the hash is bound to an approval
that a human gives once, and the approval dies at a fixed time. A retry with the same token is a
replay and is refused. Each attempt, including every refusal, is appended to a JSONL ledger whose
rows are chained by SHA-256, so a hand-edited row is detectable and named by line.

## The eight behaviours, each with its test

| # | Behaviour | Test |
|---|---|---|
| 1 | `request(payload)` stores a pending approval with a stable id and payload hash | `RequestTests.test_request_stores_a_pending_approval_with_a_stable_id_and_payload_hash` |
| 2 | `approve(id)` marks it approved once | `ApproveTests.test_approve_marks_it_approved_once` |
| 3 | `approve(id)` again raises `AlreadyDecided` | `ApproveTests.test_approve_again_raises_already_decided` |
| 4 | `execute(id, payload)` succeeds once, runs the action, records it, marks the token used | `ExecuteTests.test_execute_succeeds_once_and_marks_the_token_used` |
| 5 | A replay after success raises `TokenSpent` and the refusal is in the ledger | `ReplayTests.test_replay_after_success_raises_token_spent_and_the_ledger_records_it` |
| 6 | A payload whose hash differs raises `PayloadMismatch` and is refused | `MismatchTests.test_a_payload_whose_hash_differs_raises_payload_mismatch` |
| 7 | An approval whose `expires_at` is past raises `Expired` | `ExpiryTests.test_an_approval_past_its_expiry_raises_expired_on_approve` |
| 8 | `verify_chain()` detects a hand-edited row and names the first broken row | `ChainTests.test_verify_chain_detects_a_hand_edited_row_and_names_it` |

Also covered: executing before approval (`NotApproved`), an unknown id (`UnknownApproval`), a
mismatch that must not spend the token, a row that was deleted (detected by the sequence gap), and
append-only growth (earlier bytes are never rewritten).

## The demo

```text
step 1  request an approval for a write the agent cannot perform alone
step 2  the owner approves it once
step 3  a second approval of the same id must be refused       -> AlreadyDecided
step 4  a payload that is not the approved payload must be refused -> PayloadMismatch
step 5  the approved payload executes, and the write happens exactly once
step 6  the same token is offered again; a replay must be refused -> TokenSpent
step 7  an approval that expired before use must be refused    -> Expired
step 8  writes that actually happened: 1
step 9  ledger rows 10  ok True
```

Exit code is 0 only when all four refusals happened, the chain verifies, and exactly one write was
performed. A gate that refuses the duplicate but still writes twice is not fixed.

## The ledger

One JSON object per line, never updated and never deleted:

```json
{"seq": 3, "ts": "...", "event": "refused", "approval_id": "ap_...",
 "payload_hash": "0d4c62c2...", "detail": {"reason": "payload_mismatch"},
 "prev": "bdeab401...", "hash": "..."}
```

`hash` is SHA-256 of the row without its hash field; `prev` is the previous row's hash, and the
first row chains onto 64 zeros. `verify_chain()` walks the file and returns the first row that does
not hold: edited content, broken link, or a missing line. Refusals are recorded *before* the
exception is raised, so the log shows the attempts that were turned away, not only the successes.

## API

```python
from approval import Approvals, Ledger

store = Approvals(Ledger("approvals.jsonl"), ttl=timedelta(minutes=15))
approval = store.request({"tool": "sheets.append_row", "row": {...}})  # pending, payload_hash
store.approve(approval.id)                                            # once
store.execute(approval.id, payload, write_row)                        # once; the action is the only
                                                                      # thing a spent token cannot repeat
```

`client.py` holds the two prompt surfaces: `NoopClient` for tests, `PhoneClient` for the demo. A
client can only *ask*; it has no method that approves anything, so a broken notification channel
cannot become a grant.

## What this does not do

It does not authenticate the human who approves, and it does not defend against someone who can
rewrite the whole ledger from row 1 (a rebuilt chain verifies). It assumes the process that holds
the ledger also holds the clock. It is a reference implementation of the pattern, not an access
control system for a hostile host.

MIT.
