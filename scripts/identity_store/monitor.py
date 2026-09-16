"""Projector-liveness monitor arm path (U12).

The projector becomes a single point of failure for ~130 legacy readers the
moment the first reader depends on a projection. So its liveness monitor must be
ARMABLE BEFORE the cutover switch — armed independently of and ahead of the flag
flip — so a stale or missing projection PAGES from the very first projection.

Arm ordering at cutover: arm the monitor FIRST, then arm cutover. A disarmed
monitor never pages (no false alarms before arm); an armed monitor delegates to
``projector.check_liveness`` (pages on stale age OR missing projection).
"""
import logging
import os
import subprocess

from scripts.identity_store import projector

_ARM_REL = os.path.join("state", "identity-store-monitor.flag")
_LOG = logging.getLogger("identity_store.monitor")

# U12 page channels (gm-ruled msg_80df6e59): Telegram-to-the operator (primary) + a gm
# msg_store message. Both are best-effort at the edge; the senders are injectable
# so the page contract is provable without hitting the real channels.
_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def _default_telegram(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        _LOG.error("U12 page: TELEGRAM_BOT_TOKEN/CHAT_ID unset; not sent to the operator: %s", text)
        return
    subprocess.run(
        ["curl", "-s", "-X", "POST", _TELEGRAM_API.format(token=token),
         "-d", f"chat_id={chat}", "--data-urlencode", f"text={text}"],
        timeout=10, check=False)


def _default_gm_msg(text):
    od = os.environ.get("ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra"))
    subprocess.run(
        ["python3", os.path.join(od, "msg_store.py"), "send",
         "--from", "identity-store-monitor", "--to", "gm", "--type", "task",
         "--subject", "U12 projector-liveness PAGE", "--body", text],
        timeout=10, check=False)


def page(detail, *, telegram_send=None, gm_send=None):
    """The concrete U12 alarm — page BOTH channels (Telegram to the operator + gm
    msg_store). Injectable senders for the RED proof."""
    text = f"[identity-store U12] projector liveness FAILED: {detail}"
    (telegram_send or _default_telegram)(text)
    (gm_send or _default_gm_msg)(text)


def _orchestra_dir(orchestra_dir=None):
    return orchestra_dir or os.environ.get(
        "ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra"))


def arm_path(orchestra_dir=None):
    return os.path.join(_orchestra_dir(orchestra_dir), _ARM_REL)


def is_armed(orchestra_dir=None):
    return os.path.exists(arm_path(orchestra_dir))


def arm(orchestra_dir=None):
    """Arm the liveness monitor — do this BEFORE flipping the cutover switch."""
    p = arm_path(orchestra_dir)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as fh:
        fh.write("armed\n")
    return p


def disarm(orchestra_dir=None):
    try:
        os.remove(arm_path(orchestra_dir))
    except FileNotFoundError:
        pass


def check(orchestra_dir, projections_dir, now, max_age_s=120.0, alarm=None):
    """One liveness tick. When disarmed, do NOT page (avoid false alarms before
    arm) and report checked=False. When armed, delegate to
    ``projector.check_faithful_liveness`` — the staleness bound read from the
    faithful projector's ``.projection-meta.json`` SIDECAR (the cutover projector
    writes HEADER-FREE artifacts, so the strangler ``check_liveness`` — which reads
    an embedded header — would spuriously page every tick under the faithful model).
    Pages (via ``alarm``) on a stale or missing projection. Returns a status dict."""
    if not is_armed(orchestra_dir):
        return {"armed": False, "checked": False, "healthy": None}
    alarm = alarm or page   # default: page BOTH channels (Telegram + gm msg_store)
    healthy = projector.check_faithful_liveness(projections_dir, now, max_age_s, alarm)
    return {"armed": True, "checked": True, "healthy": healthy}
