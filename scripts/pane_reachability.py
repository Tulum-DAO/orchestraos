"""Layer B (Bug 2): live-but-unreachable pane classifier — EXTENDS the
delivery/idle-gate (gm ruling 3: one place judges liveness; NOT a parallel
watchdog).

A tmux pane can be ALIVE yet UNREACHABLE for mail injection; the agent
silently stops receiving work. Four confirmed classes
(.workspace/proposals/live-but-unreachable-pane-states-design.md):

  1. survey            — CLI feedback survey (gemini/agy; claude rating)
                          -> dismiss_then_inject (benign)
  2. permission_prompt — harness permission prompt (claude)
                          -> escalate_hold. Auto-answer = HARD NEVER
                          (ratified fleet IRON RULE). Escalate only.
  3. stuck_composer    — stranded paste chip in the INPUT line
                          -> clear_and_redeliver (never drop pending mail)
  4. auq_menu          — AUQ/plan-mode/decision menu occupying the pane
                          -> leave (the card/menu lane owns it)

SHADOW-FIRST (mirrors blocker_surface_watchdog): this module only ever LOGS
what it WOULD do to state/pane-reachability/would-clear-*.jsonl. ZERO live
auto-clears until a separate the operator/gm arm; execute_action() is double-gated
(armed flag AND ARM sentinel) and refuses the permission class ALWAYS.
Kill-switch sentinel: ~/runtime/PANE_REACHABILITY_DISABLED (instant no-op).

Consumer: scripts/message-router.py reachability_probe() at the idle-gate's
"not-idle" hold — the branch where an unreachable pane's mail parks forever.
"""
import datetime
import json
import os
import re

ORCHESTRA_DIR = os.environ.get(
    "ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra"))
LOG_DIR = os.path.join(ORCHESTRA_DIR, "state", "pane-reachability")

# Kill-switch + arm sentinels (sync-immune operator e-brake; same discipline
# as blocker_surface_watchdog / fleet-beat).
RUNTIME_DIR = os.environ.get("ORCH_RUNTIME_DIR", os.path.expanduser("~/runtime"))
DISABLED_FILE = os.path.join(RUNTIME_DIR, "PANE_REACHABILITY_DISABLED")
ARM_FILE = os.path.join(RUNTIME_DIR, "PANE_REACHABILITY_ARMED")

CLASS_SURVEY = "survey"
CLASS_PERMISSION = "permission_prompt"
CLASS_STUCK_COMPOSER = "stuck_composer"
CLASS_MENU = "auq_menu"

ACTION_DISMISS_THEN_INJECT = "dismiss_then_inject"
ACTION_ESCALATE_HOLD = "escalate_hold"
ACTION_CLEAR_AND_REDELIVER = "clear_and_redeliver"
ACTION_LEAVE = "leave"

ACTIONS = {
    CLASS_SURVEY: ACTION_DISMISS_THEN_INJECT,
    CLASS_PERMISSION: ACTION_ESCALATE_HOLD,
    CLASS_STUCK_COMPOSER: ACTION_CLEAR_AND_REDELIVER,
    CLASS_MENU: ACTION_LEAVE,
}

# --- Banner signatures (grounded on scripts/fixtures/menus/* captures) -------
# Permission prompt: harness question ("Do you want to …?") + the
# permission-ONLY footer "Tab to amend" + a live selected option ("❯ 1.").
# Prose that merely QUOTES such a question (prose_perm_narration fixture) has
# neither the footer nor a selected option line, so it must NOT classify —
# the same discipline as agent-status's q_perm/footer gate.
_PERM_Q_RE = re.compile(
    r"do you want to (proceed|allow|run|make|create|fetch|edit|read|write|"
    r"delete|update)", re.IGNORECASE)
_PERM_FOOTER_RE = re.compile(r"Tab to amend|This command requires approval")
# A SELECTED numbered option line ('❯ 1.' / '> 1.') — live menu chrome, never
# rendered by plain prose numbered lists.
_SELECTED_OPT_RE = re.compile(r"^\s*[\u276f>]\s*\d{1,2}\.\s+\S", re.MULTILINE)
# Survey banners: Antigravity/gemini CLI experience survey + claude rating.
_SURVEY_RE = re.compile(
    r"How's the CLI experience so far|How is Claude doing|"
    r"\[1\] Good\s+\[2\] Fine\s+\[3\] Bad")
# Composer paste chip (same shape message-router's E7 chip-awareness matches).
_PASTE_CHIP_RE = re.compile(r"\[Pasted (?:text|Content)[^\]]*\]")


def disabled() -> bool:
    """True iff the kill-switch sentinel exists (instant fleet-wide no-op)."""
    return os.path.exists(DISABLED_FILE)


def classify_pane(plain_capture: str, input_line: str, runtime: str):
    """Classify a pane capture into one of the 4 live-but-unreachable classes.

    Pure (no IO). Returns {"cls", "action", "evidence"} or None (no class —
    the pane is plainly busy/idle; existing idle-gate behavior stands).

    Precedence is SAFETY order: permission (never touch, escalate) > survey >
    menu (leave) > stuck composer. `input_line` is the composer line from
    pane_split — a chip there is a stranded paste; a chip merely echoed in
    scrollback is a completed submission (E7), not a stranded composer.
    """
    text = plain_capture or ""
    if (_PERM_Q_RE.search(text) and _PERM_FOOTER_RE.search(text)
            and _SELECTED_OPT_RE.search(text)):
        return {"cls": CLASS_PERMISSION, "action": ACTIONS[CLASS_PERMISSION],
                "evidence": _PERM_Q_RE.search(text).group(0)}
    m = _SURVEY_RE.search(text)
    if m:
        return {"cls": CLASS_SURVEY, "action": ACTIONS[CLASS_SURVEY],
                "evidence": m.group(0)}
    if _SELECTED_OPT_RE.search(text):
        return {"cls": CLASS_MENU, "action": ACTIONS[CLASS_MENU],
                "evidence": _SELECTED_OPT_RE.search(text).group(0).strip()}
    chip = _PASTE_CHIP_RE.search(input_line or "")
    if chip:
        return {"cls": CLASS_STUCK_COMPOSER,
                "action": ACTIONS[CLASS_STUCK_COMPOSER],
                "evidence": chip.group(0)}
    return None


# --- Per-episode de-dup (gm ruling msg_d4237aac, ARM-BLOCKING) ---------------
# Act ONCE per stuck-state episode; re-arm only after the pane VISIBLY
# RECOVERS. Keyed on STATE-TRANSITION, not time: an episode is (session, cls)
# active-until-recovery — recovery = a probe beat that classifies None for the
# session, a DIFFERENT class appearing, or the delivery path's note_recovered
# (idle-gate passed => pane visibly recovered). NOT a cooldown timer.
# Persisted on disk (episodes.json beside the would-clear log) because the
# router is a fresh cron process every minute. Rationale (shadow 08-31): 225
# would-clears were ~2 real incidents re-observed every beat; an ARMED clear
# would have fired up to 171x on ONE stuck composer = a redelivery storm.

def _episodes_path(log_dir: str) -> str:
    return os.path.join(log_dir, "episodes.json")


def _load_episodes(log_dir: str) -> dict:
    try:
        with open(_episodes_path(log_dir)) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_episodes(episodes: dict, log_dir: str) -> None:
    os.makedirs(log_dir, exist_ok=True)
    tmp = _episodes_path(log_dir) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(episodes, f, indent=2)
    os.replace(tmp, _episodes_path(log_dir))


def note_recovered(session: str, log_dir: "str | None" = None) -> None:
    """Delivery-path recovery edge: the idle-gate passed for this session, so
    the pane VISIBLY recovered — end any active episode (re-arms one future
    would-clear if it re-sticks). Cheap no-op when no episode file exists."""
    log_dir = log_dir or LOG_DIR
    if not os.path.exists(_episodes_path(log_dir)):
        return
    episodes = _load_episodes(log_dir)
    if session in episodes:
        del episodes[session]
        _save_episodes(episodes, log_dir)


def _log_would_clear(record: dict, log_dir: str) -> None:
    os.makedirs(log_dir, exist_ok=True)
    day = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d")
    path = os.path.join(log_dir, f"would-clear-{day}.jsonl")
    with open(path, "a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def observe_hold(*, session: str, runtime: str, agent: str, msg_id: str,
                 plain_capture: str, input_line: str,
                 log_dir: "str | None" = None):
    """SHADOW observation at the idle-gate's not-idle hold: classify WHY the
    pane is unreachable and log the would-action. Emits NOTHING live — the
    held message stays pending (so a future clear re-delivers by construction;
    pending mail is never dropped). Returns the record or None."""
    if disabled():
        return None
    log_dir = log_dir or LOG_DIR
    hit = classify_pane(plain_capture, input_line, runtime)
    episodes = _load_episodes(log_dir)
    if hit is None:
        # Visible recovery: the unreachable state is gone — end the episode
        # (state-transition edge; a later re-stuck opens a NEW episode).
        if session in episodes:
            del episodes[session]
            _save_episodes(episodes, log_dir)
        return None
    active = episodes.get(session)
    if active and active.get("cls") == hit["cls"]:
        # Same episode re-observed on a later beat (or a same-tick sibling
        # msg) — already logged once; suppress until recovery re-arms.
        return None
    episodes[session] = {
        "cls": hit["cls"],
        "evidence": hit["evidence"][:120],
        "since": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    _save_episodes(episodes, log_dir)
    record = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "session": session,
        "agent": agent,
        "msg_id": msg_id,
        "runtime": runtime,
        "cls": hit["cls"],
        "action": hit["action"],
        "evidence": hit["evidence"][:120],
        "redeliver": hit["cls"] == CLASS_STUCK_COMPOSER,
        "mode": "shadow",
        "armed": False,
    }
    _log_would_clear(record, log_dir)
    return record


def execute_action(cls: str, session: str, *, armed: bool = False):
    """The (future) live path — NOT armed in this build. Double-gated like
    blocker_surface_watchdog.emit_real_card: requires armed=True AND the ARM
    sentinel. The permission class refuses UNCONDITIONALLY."""
    if cls == CLASS_PERMISSION:
        raise RuntimeError(
            "execute_action REFUSED: permission-prompt auto-answer is the "
            "ratified fleet IRON RULE 'NEVER' — escalate to gm/the operator only.")
    if not armed or not os.path.exists(ARM_FILE):
        raise RuntimeError(
            "execute_action REFUSED: pane-reachability is shadow-first. A "
            f"live clear requires armed=True AND the ARM sentinel ({ARM_FILE}) "
            "— the separate the operator/gm arm-go.")
    raise NotImplementedError(
        "live auto-clear lands with the arm build after shadow review — "
        "this build ships would-clear shadow only.")
