"""Outbound long-poll of the answers topic. Auth is enforced by ntfy ACL (token);
this process additionally id-binds every tap to a still-pending request, then fires resume.
Owns a durable ?since= cursor so a tap during restart is replayed, not dropped."""
import json, os, sys, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from approval_config import NTFY_BASE, NTFY_ANSWERS_TOPIC, ntfy_token, CURSOR_FILE
from approval_schema import ApprovalStore
from approval_resume import fire_resume

def _read_cursor():
    try:
        return CURSOR_FILE.read_text().strip()
    except OSError:
        return "all"   # first run: ntfy 'since=all' replays retained messages

def _write_cursor(ts):
    try:
        CURSOR_FILE.write_text(str(ts))
    except OSError as e:
        print(f"[approval_listener] cursor write failed: {e}", file=sys.stderr)

def handle_event(event, store=None):
    store = store or ApprovalStore()
    if event.get("event") != "message":
        return
    try:
        payload = json.loads(event.get("message", "{}"))
    except json.JSONDecodeError:
        return
    rid, answer = payload.get("id"), payload.get("answer")
    text = payload.get("text")
    if not rid or not answer:
        return
    if store.record_answer(rid, answer, text):   # id-bound + answer-once; False if unknown/not-pending
        fire_resume(store.get(rid), store)
    if event.get("time"):
        _write_cursor(event["time"])

def run():
    store = ApprovalStore()
    while True:
        url = f"{NTFY_BASE}/{NTFY_ANSWERS_TOPIC}/json?since={_read_cursor()}"
        headers = {"Authorization": f"Bearer {ntfy_token()}"}   # FIX 2: re-read token each reconnect
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=300) as resp:
                for raw in resp:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line:
                        continue
                    try:
                        handle_event(json.loads(line), store=store)
                    except Exception as e:  # noqa: BLE001
                        print(f"[approval_listener] bad line: {e}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"[approval_listener] reconnect after: {e}", file=sys.stderr)
        time.sleep(3)   # FIX 1: pause before EVERY reconnect — clean end OR error — no hot spin

if __name__ == "__main__":
    run()
