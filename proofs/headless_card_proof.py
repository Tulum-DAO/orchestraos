#!/usr/bin/env python3
"""READ-ONLY proof that a headless seat's card answer reached the agent EXACTLY ONCE (step 2.4).

Run it after the card was answered from the dashboard (or the watch):

    .venv/bin/python proofs/headless_card_proof.py <seat> <card id>

It reads, and changes nothing:
  1. the card in the ledger (state/tasks.db, opened read-only): kind menu, origin headless, status resumed;
  2. the seat's supervisor log (state/headless/<seat>/events.jsonl): the card's request, and how many control
     responses were written to the CLI for it (must be 1), and that the request was resolved from this card;
  3. the seat's inbox (the same tasks.db, messages table): any message to the seat that names the card
     (must be 0: no approval_resolved digest, so the Stop hook's queue digest has nothing to show);
  4. the seat's transcript through the API (GET only): how many tool results answer AskUserQuestion with the
     card's answer after the card was filed (must be 1).
Exit 0 when every check passes, 1 otherwise; each check prints PASS/FAIL with what it saw.
"""
import json
import os
import sqlite3
import sys
import urllib.request
from pathlib import Path

DATA = Path(os.environ.get("ORCHESTRA_DIR") or os.path.expanduser("~/.orchestra"))
API = os.environ.get("ORCH_API_URL", "http://127.0.0.1:8888")


def ro(db):
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)


def main(seat, card_id):
    ok = True

    def check(cond, what):
        nonlocal ok
        ok = ok and bool(cond)
        print(("PASS " if cond else "FAIL ") + what)

    db = DATA / "state" / "tasks.db"
    c = ro(db)
    c.row_factory = sqlite3.Row
    row = c.execute("SELECT * FROM approval_requests WHERE id=?", [card_id]).fetchone()
    check(row is not None, f"card {card_id} is in the ledger")
    if row is None:
        return 1
    row = dict(row)
    menu = json.loads(row.get("menu") or "{}")
    bridge = menu.get("bridge") or {}
    check(row.get("kind") == "menu" and row.get("origin") == "headless" and bridge.get("kind") == "headless",
          f"it is a headless card (kind={row.get('kind')}, origin={row.get('origin')})")
    check(row.get("status") == "resumed", f"its status is resumed (is {row.get('status')})")
    request_id = bridge.get("request_id")
    print(f"     request {request_id}, answer={row.get('answer')!r} option_n={row.get('option_n')!r} "
          f"answer_text={row.get('answer_text')!r}")

    events = DATA / "state" / "headless" / seat / "events.jsonl"
    writes, resolved, card_lines = 0, [], []
    for line in events.read_text(errors="replace").splitlines():
        if request_id not in line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("kind") == "in" and (r.get("obj") or {}).get("type") == "control_response" \
                and (r["obj"].get("response") or {}).get("request_id") == request_id:
            writes += 1
            print(f"     control response at {r.get('wall')}: "
                  f"{json.dumps((r['obj']['response'].get('response') or {}))[:300]}")
        if r.get("kind") == "sup" and r.get("what") == "resolved":
            resolved.append(r.get("card_id"))
        if r.get("kind") == "sup" and r.get("what") == "card":
            card_lines.append(r.get("card_id"))
    check(card_lines == [card_id], f"the supervisor filed this card for the request (saw {card_lines})")
    check(writes == 1, f"exactly one control response written to the CLI (saw {writes})")
    check(resolved == [card_id], f"resolved from this card, once (saw {resolved})")

    inbox = c.execute("SELECT id, type, subject, status FROM messages WHERE to_agent=? AND "
                      "(body LIKE ? OR subject LIKE ? OR metadata LIKE ?)",
                      [seat, f"%{card_id}%", f"%{card_id}%", f"%{card_id}%"]).fetchall()
    check(len(inbox) == 0, f"no inbox message to {seat} names the card (saw {[dict(m) for m in inbox]})")
    c.close()

    try:
        with urllib.request.urlopen(f"{API}/api/agents/{seat}/transcript?limit=400", timeout=15) as r:
            tx = json.loads(r.read().decode())
    except Exception as e:  # noqa: BLE001
        check(False, f"transcript readable from the API ({e})")
        return 1
    items = tx.get("items") or []
    asks = {it.get("id") for it in items if it.get("kind") == "tool_use" and it.get("tool") == "AskUserQuestion"}
    results = [it for it in items if it.get("kind") == "tool_result" and it.get("tool_use_id") in asks
               and (it.get("ts") or "") >= (row.get("created_at") or "")]
    for it in results:
        print(f"     tool result {it.get('ts')}: {str(it.get('text'))[:200]!r}")
    check(len(results) == 1, f"one AskUserQuestion result in the transcript after the card (saw {len(results)})")
    dup = [it for it in items if it.get("kind") == "text" and it.get("role") == "user"
           and card_id in str(it.get("text"))]
    check(not dup, f"no user line in the transcript names the card (saw {len(dup)})")
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2]))
