#!/usr/bin/env python3
"""
OrchestraOS Message Bus — Reliable inter-agent messaging with reply routing.

Every message has a sender, receiver, conversation_id, and reply_to field.
When agent B finishes work requested by agent A, the response routes back
to A automatically. Jarvis can read any conversation thread.

Storage: JSONL per agent (state/messages/{agent_id}.jsonl)
Conversations: JSONL per thread (state/conversations/{conversation_id}.jsonl)

Usage:
    # Send a message
    python3 message_bus.py send --from gm --to your-agent-id --subject "Fix the bug" --body "..."

    # Reply to a message
    python3 message_bus.py reply --message-id msg_xxx --body "Done, here's what I did..."

    # Read an agent's inbox (unread messages)
    python3 message_bus.py inbox --agent gm

    # Read a conversation thread
    python3 message_bus.py thread --conversation-id conv_xxx

    # Acknowledge receipt (marks as delivered)
    python3 message_bus.py ack --message-id msg_xxx

    # Router daemon (watches for new messages, delivers to agents)
    python3 message_bus.py router
"""

import json
import os
import sys
import time
import hashlib
import argparse
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

ORCHESTRA_DIR = Path(os.environ.get("ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra")))
MESSAGES_DIR = ORCHESTRA_DIR / "state" / "messages"
CONVERSATIONS_DIR = ORCHESTRA_DIR / "state" / "conversations"
ROUTER_LOG = ORCHESTRA_DIR / "state" / "message-router.jsonl"

MESSAGES_DIR.mkdir(parents=True, exist_ok=True)
CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)


def gen_id(prefix: str = "msg") -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    h = hashlib.md5(f"{ts}{time.time_ns()}".encode()).hexdigest()[:6]
    return f"{prefix}_{ts}_{h}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- Message Operations ---

def send_message(
    from_agent: str,
    to_agent: str,
    subject: str,
    body: str = "",
    priority: str = "medium",
    msg_type: str = "task",
    conversation_id: Optional[str] = None,
    reply_to: Optional[str] = None,
    context_files: Optional[list] = None,
) -> dict:
    """Send a message from one agent to another. Returns the message dict."""
    msg_id = gen_id("msg")
    conv_id = conversation_id or gen_id("conv")

    msg = {
        "id": msg_id,
        "conversation_id": conv_id,
        "type": msg_type,
        "from": from_agent,
        "to": to_agent,
        "priority": priority,
        "subject": subject,
        "body": body,
        "reply_to": reply_to,
        "context_files": context_files or [],
        "created": now_iso(),
        "status": "pending",      # pending -> delivered -> read -> replied
        "delivered_at": None,
        "read_at": None,
    }

    # Write to receiver's message log
    receiver_log = MESSAGES_DIR / f"{to_agent}.jsonl"
    with open(receiver_log, "a") as f:
        f.write(json.dumps(msg) + "\n")

    # Write to conversation thread
    conv_log = CONVERSATIONS_DIR / f"{conv_id}.jsonl"
    with open(conv_log, "a") as f:
        f.write(json.dumps(msg) + "\n")

    # Write to sender's outbox (so they can track what they sent)
    sender_log = MESSAGES_DIR / f"{from_agent}.jsonl"
    outbound = {**msg, "direction": "sent"}
    with open(sender_log, "a") as f:
        f.write(json.dumps(outbound) + "\n")

    return msg


def reply_to_message(message_id: str, from_agent: str, body: str, status: str = "completed") -> dict:
    """Reply to a specific message. Automatically routes back to the original sender."""
    # Find the original message
    original = find_message(message_id)
    if not original:
        raise ValueError(f"Message {message_id} not found")

    return send_message(
        from_agent=from_agent,
        to_agent=original["from"],
        subject=f"RE: {original['subject']}",
        body=body,
        priority=original.get("priority", "medium"),
        msg_type="reply",
        conversation_id=original["conversation_id"],
        reply_to=message_id,
    )


def find_message(message_id: str) -> Optional[dict]:
    """Find a message by ID across all agent logs."""
    for log_file in MESSAGES_DIR.glob("*.jsonl"):
        for line in log_file.read_text().strip().split("\n"):
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
                if msg.get("id") == message_id:
                    return msg
            except json.JSONDecodeError:
                continue
    return None


def get_inbox(agent_id: str, unread_only: bool = True) -> list[dict]:
    """Get messages for an agent. Default: unread only."""
    log_file = MESSAGES_DIR / f"{agent_id}.jsonl"
    if not log_file.exists():
        return []

    messages = []
    for line in log_file.read_text().strip().split("\n"):
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
            # Skip outbound messages
            if msg.get("direction") == "sent":
                continue
            if unread_only and msg.get("status") not in ("pending", "delivered"):
                continue
            messages.append(msg)
        except json.JSONDecodeError:
            continue

    return messages


def acknowledge(message_id: str, agent_id: str) -> bool:
    """Mark a message as delivered/read."""
    log_file = MESSAGES_DIR / f"{agent_id}.jsonl"
    if not log_file.exists():
        return False

    lines = log_file.read_text().strip().split("\n")
    updated = False
    new_lines = []
    for line in lines:
        if not line.strip():
            new_lines.append(line)
            continue
        try:
            msg = json.loads(line)
            if msg.get("id") == message_id:
                msg["status"] = "read"
                msg["read_at"] = now_iso()
                updated = True
            new_lines.append(json.dumps(msg))
        except json.JSONDecodeError:
            new_lines.append(line)

    if updated:
        log_file.write_text("\n".join(new_lines) + "\n")
    return updated


def get_conversation(conversation_id: str) -> list[dict]:
    """Get all messages in a conversation thread, ordered by time."""
    conv_file = CONVERSATIONS_DIR / f"{conversation_id}.jsonl"
    if not conv_file.exists():
        return []

    messages = []
    for line in conv_file.read_text().strip().split("\n"):
        if not line.strip():
            continue
        try:
            messages.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    return sorted(messages, key=lambda m: m.get("created", ""))


def get_agent_conversations(agent_id: str) -> list[dict]:
    """Get summary of all conversations an agent is part of."""
    log_file = MESSAGES_DIR / f"{agent_id}.jsonl"
    if not log_file.exists():
        return []

    convs: dict[str, dict] = {}
    for line in log_file.read_text().strip().split("\n"):
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
            cid = msg.get("conversation_id", "unknown")
            if cid not in convs:
                convs[cid] = {
                    "conversation_id": cid,
                    "subject": msg.get("subject", ""),
                    "with": msg.get("from") if msg.get("from") != agent_id else msg.get("to"),
                    "message_count": 0,
                    "unread": 0,
                    "last_message": msg.get("created"),
                    "last_from": msg.get("from"),
                }
            convs[cid]["message_count"] += 1
            convs[cid]["last_message"] = msg.get("created")
            convs[cid]["last_from"] = msg.get("from")
            if msg.get("direction") != "sent" and msg.get("status") in ("pending", "delivered"):
                convs[cid]["unread"] += 1
        except json.JSONDecodeError:
            continue

    return sorted(convs.values(), key=lambda c: c.get("last_message", ""), reverse=True)


# --- Router ---

def deliver_to_agent(agent_id: str, msg: dict) -> bool:
    """Deliver a message to an agent by injecting a compact notification into their tmux."""
    import subprocess

    # Build a compact notification (NOT the full message body)
    sender = msg.get("from", "unknown")
    subject = msg.get("subject", "")[:100]
    msg_id = msg.get("id", "")
    notification = f"[MSG from {sender}] {subject}\nReply: python3 message_bus.py reply --message-id {msg_id} --body \"your response\""

    # Check if agent has a tmux session
    try:
        result = subprocess.run(
            ["tmux", "has-session", "-t", agent_id],
            capture_output=True, timeout=3
        )
        if result.returncode != 0:
            return False  # Agent not running — message stays in inbox
    except Exception:
        return False

    # DON'T inject into tmux automatically — that's the old pattern.
    # Instead, update the message status to "delivered" so the agent
    # can pull it when ready (batch update pattern).
    acknowledge(msg_id, agent_id)

    # Log delivery
    entry = {
        "ts": now_iso(),
        "action": "delivered",
        "message_id": msg_id,
        "from": sender,
        "to": agent_id,
    }
    with open(ROUTER_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")

    return True


def run_router(once: bool = False):
    """Watch for pending messages and deliver them. Run as daemon or --once."""
    import subprocess

    while True:
        for log_file in MESSAGES_DIR.glob("*.jsonl"):
            agent_id = log_file.stem
            pending = get_inbox(agent_id, unread_only=True)
            pending = [m for m in pending if m.get("status") == "pending"]

            if not pending:
                continue

            # Check if agent is running
            try:
                result = subprocess.run(
                    ["tmux", "has-session", "-t", agent_id],
                    capture_output=True, timeout=3
                )
                agent_running = result.returncode == 0
            except Exception:
                agent_running = False

            if agent_running:
                for msg in pending:
                    deliver_to_agent(agent_id, msg)

        if once:
            break
        time.sleep(5)


# --- API Helpers (called by Express routes) ---

def get_inbox_summary(agent_id: str) -> dict:
    """Compact inbox summary for dashboard/Jarvis."""
    msgs = get_inbox(agent_id, unread_only=True)
    return {
        "agent_id": agent_id,
        "unread": len(msgs),
        "messages": [
            {
                "id": m["id"],
                "from": m["from"],
                "subject": m["subject"][:80],
                "priority": m.get("priority", "medium"),
                "created": m["created"],
                "conversation_id": m["conversation_id"],
            }
            for m in msgs[:10]  # Last 10 unread
        ],
    }


# --- CLI ---

def main():
    parser = argparse.ArgumentParser(description="OrchestraOS Message Bus")
    sub = parser.add_subparsers(dest="command")

    # send
    send_p = sub.add_parser("send")
    send_p.add_argument("--from", dest="from_agent", required=True)
    send_p.add_argument("--to", required=True)
    send_p.add_argument("--subject", required=True)
    send_p.add_argument("--body", default="")
    send_p.add_argument("--priority", default="medium")
    send_p.add_argument("--type", default="task")
    send_p.add_argument("--conversation-id", default=None)

    # reply
    reply_p = sub.add_parser("reply")
    reply_p.add_argument("--message-id", required=True)
    reply_p.add_argument("--from", dest="from_agent", default=None)
    reply_p.add_argument("--body", required=True)

    # inbox
    inbox_p = sub.add_parser("inbox")
    inbox_p.add_argument("--agent", required=True)
    inbox_p.add_argument("--all", action="store_true")

    # thread
    thread_p = sub.add_parser("thread")
    thread_p.add_argument("--conversation-id", required=True)

    # ack
    ack_p = sub.add_parser("ack")
    ack_p.add_argument("--message-id", required=True)
    ack_p.add_argument("--agent", required=True)

    # conversations
    convs_p = sub.add_parser("conversations")
    convs_p.add_argument("--agent", required=True)

    # router
    router_p = sub.add_parser("router")
    router_p.add_argument("--once", action="store_true")

    args = parser.parse_args()

    if args.command == "send":
        msg = send_message(
            from_agent=args.from_agent,
            to_agent=args.to,
            subject=args.subject,
            body=args.body,
            priority=args.priority,
            msg_type=args.type,
            conversation_id=args.conversation_id,
        )
        print(json.dumps(msg, indent=2))

    elif args.command == "reply":
        original = find_message(args.message_id)
        if not original:
            print(f"Message {args.message_id} not found", file=sys.stderr)
            sys.exit(1)
        from_agent = args.from_agent or original["to"]
        msg = reply_to_message(args.message_id, from_agent, args.body)
        print(json.dumps(msg, indent=2))

    elif args.command == "inbox":
        msgs = get_inbox(args.agent, unread_only=not args.all)
        for m in msgs:
            status = m.get("status", "?")
            print(f"  [{status}] {m['id']} from {m['from']}: {m['subject'][:60]}")
        if not msgs:
            print("  (empty)")

    elif args.command == "thread":
        msgs = get_conversation(args.conversation_id)
        for m in msgs:
            direction = "→" if m.get("direction") == "sent" else "←"
            print(f"  {m['created'][:19]} {m['from']} {direction} {m['to']}: {m.get('body', m.get('subject', ''))[:80]}")

    elif args.command == "ack":
        ok = acknowledge(args.message_id, args.agent)
        print("Acknowledged" if ok else "Not found")

    elif args.command == "conversations":
        convs = get_agent_conversations(args.agent)
        for c in convs:
            unread = f" ({c['unread']} unread)" if c["unread"] else ""
            print(f"  {c['conversation_id'][:20]} with {c['with']} — {c['subject'][:50]}{unread}")

    elif args.command == "router":
        print("Message router starting...")
        run_router(once=args.once)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
