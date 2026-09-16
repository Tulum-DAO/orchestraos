"""Central config for the approval loop. Import these; never hard-code twice."""
import os
from pathlib import Path

ORCHESTRA_DIR = Path(os.environ.get("ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra")))
DB_PATH = ORCHESTRA_DIR / "state" / "tasks.db"

NTFY_BASE = os.environ.get("NTFY_BASE", "http://localhost:9080")
NTFY_APPROVALS_TOPIC = os.environ.get("NTFY_APPROVALS_TOPIC", "approvals")
NTFY_ANSWERS_TOPIC = os.environ.get("NTFY_ANSWERS_TOPIC", "approval-answers")
NTFY_TOKEN_FILE = Path(os.environ.get("NTFY_TOKEN_FILE", os.path.expanduser("~/.config/jarvis/ntfy-token")))

DEFAULT_OPTIONS = ["approve", "deny", "hold"]
EXPIRY_HOURS = 24
WATCHDOG_MINUTES = 5          # re-fire an un-acked resume after this
WATCHDOG_MAX_ATTEMPTS = 3     # then escalate
# SLA spec 2026-08-14 (DEC-1786690995): inject within 1 min of the target going
# idle. Cheap refusals (busy/no-live-head) retry every cron beat; only REAL
# outcomes count toward WATCHDOG_MAX_ATTEMPTS. WATCHDOG_MINUTES above is the
# ack window for landed-but-unacked injects (re-inject spam guard).
WATCHDOG_RETRY_BEAT_SECONDS = 55   # cheap-retry throttle (~one cron beat)
ESCALATE_STUCK_MINUTES = 15        # wall-clock backstop (dead emitter / forever-busy)
ESCALATE_REPEAT_MINUTES = 30       # re-escalate no more often than this
CURSOR_FILE = ORCHESTRA_DIR / "state" / ".approval-listener-cursor"

# DEC-1786664626 Q4 (the operator-gated, card apr_10c0c829): whether a PENDING decision
# still expires after EXPIRY_HOURS. Answered/acted decisions NEVER expire (F2) —
# that already holds (expire_due only touches status='pending'). This flag only
# governs the PENDING side, behind a config toggle so the operator's answer flips ONE
# setting, not a rebuild. Default = current behavior (pending expires at 24h).
# Set EXPIRE_PENDING=0 (env) to make pending decisions never expire either.
# SHAW ANSWERED Q4 on 2026-08-13 23:51Z (apr_10c0c829, option 1): "Nothing expires — pending
# too". That card ended resume_failed so the ruling never reached this default (it stayed
# "1" while expire_due had no caller, so nothing expired by accident). Default now records
# the ruling: pending decisions do NOT expire unless EXPIRE_PENDING=1 is set explicitly
# (gm msg_86bcc168 item 4 by effect: 27/28 live pending rows were past expires_at).
EXPIRE_PENDING = os.environ.get("EXPIRE_PENDING", "0") not in ("0", "false", "False", "")

# Off-tailnet escalation (gm commission msg_c5757395, apr_321ad47b gap): ntfy is
# served tailnet-only, so a push to a phone that is OFF Tailscale dies silently.
# approval_notify checks the phone's tailscale peer and escalates to a FULL
# Telegram card when it has been off longer than the threshold. Host is matched
# against the peer's DNSName prefix / HostName. Unknown => treated as ON.
PHONE_TAILNET_HOST = os.environ.get("PHONE_TAILNET_HOST", "iphone172")
PHONE_OFFTAILNET_THRESHOLD_S = int(os.environ.get("PHONE_OFFTAILNET_THRESHOLD_S", "600"))
TAILSCALE_STATUS_TIMEOUT_S = int(os.environ.get("TAILSCALE_STATUS_TIMEOUT_S", "5"))
# Digest mode (gm msg_083b5273): when MORE than this many rows qualify for the
# full-card escalation on one beat, send ONE digest instead of a pile-on.
ESCALATION_DIGEST_MAX_SINGLES = int(os.environ.get("ESCALATION_DIGEST_MAX_SINGLES", "3"))

def ntfy_token() -> str:
    try:
        return NTFY_TOKEN_FILE.read_text().strip()
    except OSError:
        return ""
