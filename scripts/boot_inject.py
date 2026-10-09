#!/usr/bin/env python3
"""boot_inject.py — type a boot prompt into an agent pane and VERIFY it submitted.

DEC-1791347425871474 (CONSENSUS_REACHED: orchestra-builder, gm, gemini-gm; gm's build answers
msg_59988b71). Every boot path calls this: spawn-agent.sh inject_prompt, rotate_agent (readback
drive + step-9 promotion notice), agent-reincarnator.sh, qa-instance-up.sh, gm-startup.sh.

THE INCIDENT (2026-10-07): Claude Code ingests a paste asynchronously and an early Enter is
swallowed, so the boot prompt SITS in the composer, often as a "[Pasted text #1 +16 lines]" chip.
Three BG green spawns stranded that way in one night (SeamTimeout x3, seat disarmed), and the
legacy rotation's own boot stranded too, until gm pressed ONE bare Enter ~2 min later and it
submitted in 10 s. The old typers were blind in opposite directions: spawn-agent.sh called
"text visible" success (a strand IS visible), rotate_agent called "text gone" success (a chip
hides it). This module asks the one question that matters, through composer_verify: did the
content leave the composer AND appear in the scrollback?

ALGORITHM (gm Q3): copy-mode exit -> codex busy-hold -> refuse to paste onto a stranded chip ->
paste + receipt (2 tries) -> 3 Enters 2 s apart, each verified -> then poll every 10 s for up to
90 s, pressing ONE bare Enter whenever text or a chip is still in the composer (at most 4 more)
-> loud "stuck". It NEVER re-pastes: a second paste is how chips stack and wedge a composer.

TARGET GUARD (gm Q2): no fence_check (a boot target is normally a non-canonical green). Instead it
REFUSES to type into the canonical tmux session of a LIVE seat, which is a working agent whose
composer may hold the operator's draft. Pass allow_canonical (CLI --allow-canonical) for a deliberate
notice such as rotate_agent's promotion message. "Live" = the canonical row's status is online AND
the tmux session is older than BOOT_GRACE_S. A session spawn-agent.sh just created for that seat
is its own boot, not a working seat, so ordinary spawns are not refused.

Every tmux target is exact ('=NAME:'). A bare name prefix-matches: 'gm' hits 'gm-g96'.

CLI: boot_inject.py <session> [--runtime claude] (--text T | --text-file F) [--allow-canonical]
exit 0 submitted / 2 stuck / 3 failed or refused; the last stdout line names the outcome + stage.
"""
import argparse
import os
import sqlite3
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from composer_verify import (  # noqa: E402
    codex_pane_is_busy,
    composer_has_stranded_chip,
    count_paste_chips,
    paste_receipt_ok,
    split_capture,
    submit_ok,
)
from runtime_signatures import DEFAULT_RUNTIME, PROMPT_SIGNATURES  # noqa: E402

ORCHESTRA_DIR = os.environ.get("ORCHESTRA_DIR", os.path.dirname(_HERE))
BOOT_GRACE_S = 300          # a canonical session younger than this is being booted, not working
FIRST_ENTERS = 3            # router-identical stage 2: Enter, 2 s, verify
FIRST_ENTER_GAP_S = 2.0
POLL_EVERY_S = 10.0         # gm Q3: then poll every 10 s ...
POLL_FOR_S = 90.0           # ... for up to 90 s ...
LATE_ENTERS_MAX = 4         # ... with at most 4 more bare Enters while content remains
BUFFER = "boot-inject"


def _target(session):
    return f"={session}:"


def _real_tmux(*args, timeout=10):
    r = subprocess.run(["tmux", *args], capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout


def _registry_db():
    """The identity DB to judge "canonical" by: $ORCHESTRA_DIR's when it has one (a QA instance
    or worktree may point elsewhere), else the tree this script lives in. None when neither has
    one: an install without the identity store has no canonical seats to protect."""
    for root in (ORCHESTRA_DIR, os.path.dirname(_HERE)):
        db = os.path.join(root, "state", "orchestra-registry.db")
        if os.path.exists(db):
            return db
    return None


def _canonical_live(session, *, tmux_fn=_real_tmux, now=None, db_path=None):
    """True iff `session` is the canonical tmux session of an ONLINE seat AND that tmux session
    is older than BOOT_GRACE_S. Unknowable (DB or tmux unreadable) -> True: refuse (fail-closed),
    since a boot prompt typed into a working seat is the worse failure (gm Q2). NO identity DB at
    all -> False: a fresh public install never creates one (orchestra init does not), so there is
    no canonical seat, and refusing there would refuse every spawn the install ever makes."""
    db = db_path or _registry_db()
    if db is None:
        return False
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            row = con.execute("SELECT status FROM canonical WHERE tmux_session=?",
                              (session,)).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return True
    if row is None or row[0] != "online":
        return False
    rc, out = tmux_fn("display-message", "-p", "-t", _target(session), "#{session_created}")
    if rc != 0:
        return True
    try:
        created = float(out.strip())
    except ValueError:
        return True
    return ((now if now is not None else time.time()) - created) >= BOOT_GRACE_S


def boot_inject(session, text, runtime=DEFAULT_RUNTIME, *, allow_canonical=False,
                tmux_fn=_real_tmux, sleep_fn=time.sleep, log_fn=None, canonical_live_fn=None):
    """Type `text` into `session` and verify by effect. Returns (outcome, stage):
    outcome in {'submitted', 'stuck', 'failed'}; stage names where it ended."""
    log = log_fn or (lambda m: print(f"[boot_inject] {session}: {m}", file=sys.stderr, flush=True))
    sig = PROMPT_SIGNATURES.get(runtime, PROMPT_SIGNATURES[DEFAULT_RUNTIME])
    prompt_char = sig["prompt_char"]
    probe = text[:60]

    def split():
        rc, out = tmux_fn("capture-pane", "-p", "-t", _target(session), "-S", "-50")
        if rc != 0:
            return [], "", False
        return split_capture(out, prompt_char, runtime)

    def enter():
        tmux_fn("send-keys", "-t", _target(session), "Enter")

    # GUARD: never type into a live working canonical seat unless the caller means to.
    if not allow_canonical and (canonical_live_fn or _canonical_live)(session, tmux_fn=tmux_fn):
        log("REFUSED: canonical session of a LIVE seat (pass --allow-canonical for a deliberate "
            "notice); nothing was typed")
        return "failed", "refused-canonical"

    # Stage 0: copy mode swallows the paste and the Enter.
    rc, mode = tmux_fn("display-message", "-p", "-t", _target(session), "#{pane_in_mode}")
    if rc == 0 and mode.strip() == "1":
        tmux_fn("send-keys", "-t", _target(session), "q")
        sleep_fn(0.3)

    if runtime == "codex":
        rc, pane = tmux_fn("capture-pane", "-p", "-t", _target(session))
        if rc == 0 and codex_pane_is_busy(pane):
            log("codex pane is WORKING: busy-hold, nothing typed")
            return "stuck", "codex-busy"

    _, line0, ok0 = split()
    if ok0 and composer_has_stranded_chip(line0):
        log("a paste chip is ALREADY in the composer: not pasting onto it, not pressing Enter on it")
        return "stuck", "stranded-chip"

    if not (ok0 and probe[:30] in line0):
        landed = False
        for attempt in (1, 2):
            _, before, okb = split()
            chips_before = count_paste_chips(before if okb else "")
            tmux_fn("set-buffer", "-b", BUFFER, text)
            tmux_fn("paste-buffer", "-b", BUFFER, "-t", _target(session), "-d")
            sleep_fn(0.7)
            _, line, ok = split()
            if ok and paste_receipt_ok(line, probe, chips_before):
                landed = True
                break
            log(f"paste attempt {attempt}: not in the composer")
            sleep_fn(1.0)
        if not landed:
            log("FAILED: the paste never reached the composer")
            return "failed", "no-receipt"
    else:
        log("the prompt is already in the composer (earlier attempt): submitting it, not re-pasting")

    for attempt in range(1, FIRST_ENTERS + 1):
        enter()
        sleep_fn(FIRST_ENTER_GAP_S)
        scroll, line, ok = split()
        if ok and submit_ok(scroll, line, probe):
            return "submitted", f"enter-{attempt}"

    # gm Q3: a fresh TUI can take minutes to finish ingesting; keep checking, Enter sparingly.
    late = 0
    waited = 0.0
    while waited < POLL_FOR_S:
        sleep_fn(POLL_EVERY_S)
        waited += POLL_EVERY_S
        scroll, line, ok = split()
        if ok and submit_ok(scroll, line, probe):
            return "submitted", f"late-poll-{int(waited)}s"
        if ok and late < LATE_ENTERS_MAX and (probe[:30] in line or count_paste_chips(line) > 0):
            late += 1
            enter()
    log(f"STUCK: still in the composer after {FIRST_ENTERS}+{late} Enters and {int(waited)} s; "
        f"left as-is (never cleared, never re-pasted)")
    return "stuck", "stuck"


_EXIT = {"submitted": 0, "stuck": 2, "failed": 3}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("session")
    ap.add_argument("--runtime", default=DEFAULT_RUNTIME)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--text")
    g.add_argument("--text-file")
    ap.add_argument("--allow-canonical", action="store_true")
    a = ap.parse_args(argv)
    text = a.text if a.text is not None else open(a.text_file, encoding="utf-8").read()
    outcome, stage = boot_inject(a.session, text, a.runtime, allow_canonical=a.allow_canonical)
    print(f"boot_inject {a.session}: {outcome} ({stage})")
    return _EXIT[outcome]


if __name__ == "__main__":
    sys.exit(main())
