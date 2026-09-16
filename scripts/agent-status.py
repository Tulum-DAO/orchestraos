#!/usr/bin/env python3
"""
agent-status.py — Detect Claude Code agent state from tmux, hardened.

v2 (agent-state-truth, 2026-08-09). Tiered truth, per docs/agent-state-truth-audit.md:

  Tier 0  HOOK EVENTS   state/agent-events/panes/<pane>.json — push truth written
                        by scripts/state-event-hook.py (Claude Code hooks). Immune
                        to every screen lie; adopted per-session as the fleet
                        restarts (hooks snapshot at session start).
  Tier 1  PROCESS       claude process on the pane tty (kernel truth).
  Tier 2  SCREEN        chrome-ANCHORED parsing: markers only count in the TUI
                        chrome region (spinner zone / composer box / status bar),
                        never anywhere on screen (F1/F9 fix). Spinner detection
                        does not require the (time · tokens) suffix (F2 fix).
  Tier 3  PERSISTED     state/agent-state/<session>.json — state ages, stalled
                        detection, stranded-input first-seen tracking.

States: working | thinking | idle | stopped | waiting_permission |
        stranded_input | stalled | unknown
Additive output keys: confidence ("hook"|"screen"|"screen+hook"|"process"),
        state_age_s, stranded {text, age_s}, hook {event, tool, age_s}.

The public shape of get_agent_status(session) is unchanged for existing keys —
watch_gateway.py (/agents, verified_inject, /agent-screen) consumes it as-is.

Usage:
    python3 agent-status.py <session-name>           # JSON output
    python3 agent-status.py <session-name> --oneline # one-line summary
    python3 agent-status.py --all [--oneline]        # all sessions
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import providers  # noqa: E402  — provider-aware process truth (Tier 1)

ORCH_DIR = os.environ.get("ORCH_DIR", os.path.expanduser("~/scripts/agent-orchestra"))
EVENTS_DIR = os.environ.get("ORCH_EVENTS_DIR", os.path.join(ORCH_DIR, "state", "agent-events", "panes"))
STATE_DIR = os.environ.get("ORCH_STATE_DIR", os.path.join(ORCH_DIR, "state", "agent-state"))

HOOK_WORKING_TTL_S = 300     # hook "working" trusted this long without a newer event
HOOK_WAITING_TTL_S = 900     # hook "waiting_permission" trusted this long
STALL_S = 600                # working + unchanged screen + no hook events -> stalled

# --- Telemetry-v2 deriver cutover (congruence DEC-1788465233) ---------------
# When the always-on B1 deriver daemon (telemetryd) is live, its status.json is
# the AUTHORITATIVE status source for a LIVE proc, RETIRING the 300/900/600 TTL
# heuristics above (they are demoted to a rollback-only fallback, NOT deleted —
# leg (a) atomicity + leg (e) rollback). A fresh snapshot => deriver is the sole
# oracle (the TTL path is not consulted); a stale/absent snapshot => fall through
# to the intact TTL path. Rollback = `systemctl disable` the unit; status.json
# ages out within DERIVER_FRESH_S and the pre-wiring TTL path resumes.
DERIVER_FRESH_S = 12.0        # deriver status trusted this long (status.json rewrites ~1s)
# Map the deriver's vocabulary onto the pre-wiring state vocabulary consumers key
# on (watch_gateway / arturo). offline_crashed->stopped (secondary #2).
_DERIVER_TO_STATE = {
    "streaming": "working", "computing": "working",
    "waiting_permission": "waiting_permission", "stalled": "stalled",
    "idle": "idle", "offline_crashed": "stopped",
}


def _read_deriver_snapshot():
    """Read the B1 deriver's status.json (the live oracle) via the canonical
    reader. Lazy import so agent-status stays light/import-safe when the deriver
    is absent (the common pre-activation case). None on any failure => fallback."""
    try:
        from lineage_daemon.realtime.snapshot import read_status_snapshot
        return read_status_snapshot(os.environ.get("ORCHESTRA_REALTIME_DIR") or None)
    except Exception:
        return None


def _deriver_lookup(session, now):
    """Return (mapped_state, deriver_state, age_s) if a FRESH deriver status
    exists for this session, else None (=> TTL fallback)."""
    snap = _read_deriver_snapshot()
    if not snap:
        return None
    seat = (snap.get("seats") or {}).get(session)
    if not seat:
        return None
    ts = seat.get("ts")
    if ts is None:
        return None
    age = now - float(ts)
    if age > DERIVER_FRESH_S:
        return None
    mapped = _DERIVER_TO_STATE.get(seat.get("status"))
    if mapped is None:                       # unknown deriver state -> fail safe to TTL
        return None
    return mapped, seat.get("status"), int(max(0.0, age))

# The composer placeholder on fresh sessions ('❯ Try "fix lint errors"') is
# ANSI-indistinguishable from typed text (verified 2026-08-09); whitelist it.
PLACEHOLDER_RE = re.compile(r'^Try "[^"]{0,60}"$')

# CLI v2.1.259+ stamps the session title into the TOP rule line
# ('────...──── GM ─' — verified by effect on the live gm pane, line 88), so a
# rule may carry an OPTIONAL trailing ' <label> ─' suffix. The label is Claude
# Code's session title (may contain spaces + be long, e.g. a 40+ char seat
# summary), so it is UNCAPPED — a too-narrow cap would leave long-named seats
# grey. The strong leading run '[─━]{8,}' is what keeps prose out: an ordinary
# line (even one quoting a labeled rule, or ending ' word ─') never begins with
# 8+ box-drawing chars, so it still fails to match.
_RULE_RE = re.compile(r'^[─━]{8,}( .+ [─━]+)?\s*$')
_GERUND_RE = re.compile(r'([A-Z][a-zà-ÿ]+ing)\b')
_DONE_RE = re.compile(r'([A-Z][a-zà-ÿ]+ed)\s+for\s+(\d+[hms][\s\dhms]*)')
# spinner glyphs observed across Claude Code and Antigravity builds
_SPINNER_GLYPHS = '✻✽✶✳✢✺✹✸✷⚹·*+⣾⣽⣻⢿⡿⣟⣯⣷'
_SPINNER_LINE_RE = re.compile(
    r'^[' + re.escape(_SPINNER_GLYPHS) + r']\s*[A-Z][a-zà-ÿ]+ing\S*(…|\.\.\.)')
_SUFFIX_RE = re.compile(
    r'\((\d+[hms][\s\dhms]*)(?:[^)]*?[·,]\s*[↓⬇]?\s*([\d.]+k?)\s*tokens)?[^)]*\)')
_SPINNER_COLOR_RE = re.compile(r'\033\[38;5;(?:174|180|216)m')


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return re.sub(r'\033\[[0-9;]*m', '', text or '')


def get_tmux_output(session: str, lines: int = 0) -> str | None:
    """Capture tmux pane output with ANSI escape codes preserved.

    lines=0 (default) captures the VISIBLE SCREEN ONLY. Do NOT include
    scrollback for state detection: old spinner frames persist in scrollback
    after a turn finishes (2026-08-09 incident). Scrollback is only for
    content reads.
    """
    try:
        args = ['tmux', 'capture-pane', '-t', session, '-p', '-e']
        if lines > 0:
            args += ['-S', f'-{lines}']
        result = subprocess.run(args, capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return None
        return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def get_pane_tty(session: str) -> str | None:
    """Get the TTY device for a tmux session's active pane."""
    try:
        result = subprocess.run(
            ['tmux', 'display-message', '-t', session, '-p', '#{pane_tty}'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def get_pane_id(session: str) -> str | None:
    """tmux pane id (%N) — the join key for hook event files ($TMUX_PANE)."""
    try:
        result = subprocess.run(
            ['tmux', 'display-message', '-t', session, '-p', '#{pane_id}'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def check_claude_process(session: str) -> dict:
    """Check if a claude process runs on the session's pane tty.

    v2: matches the claude row by the FIRST token of the command (basename),
    not the last whitespace token (which false-matched anything ending in
    'claude' and depended on --forest column art). Children counted via ppid.
    """
    empty = {'running': False, 'runtime': None, 'pid': None, 'cpu': 0.0,
             'rss_mb': 0, 'elapsed': '', 'children': 0}
    tty = get_pane_tty(session)
    if not tty:
        return empty
    try:
        result = subprocess.run(
            # -ww: width-unlimited args. ps honours the caller's COLUMNS (pytest, some cron/hook
            # envs export a narrow one) and truncated '/home/.../bin/claude' to '.../bi', so a
            # BUSY seat read as no-claude -> 'service' (gm msg_bfb82953 fold-in, 2026-09-16).
            ['ps', '-ww', '-t', tty.replace('/dev/', ''), '-o', 'pid=,ppid=,pcpu=,rss=,etime=,args='],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return empty
        rows = []
        for line in result.stdout.strip().split('\n'):
            parts = line.split(None, 5)
            if len(parts) >= 6:
                rows.append(parts)
        claude = None
        runtime = None
        for p in rows:
            first_arg = p[5].split()[0] if p[5].split() else ''
            matched = providers.runtime_for_command(first_arg)
            if matched:
                claude = p
                runtime = matched
                break
        if not claude:
            return empty
        pid = int(claude[0])
        children = sum(1 for p in rows if p[1].isdigit() and int(p[1]) == pid)
        return {
            'running': True,
            'runtime': runtime,
            'pid': pid,
            'cpu': float(claude[2]),   # NB: ps %cpu = lifetime average, weak signal
            'rss_mb': round(int(claude[3]) / 1024),
            'elapsed': claude[4],
            'children': children,
        }
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        return empty


# ---------------------------------------------------------------------------
# Tier 2 — chrome-anchored screen parsing
# ---------------------------------------------------------------------------

_SGR_RE = re.compile(r'\033\[([0-9;]*)m')
_CSI_RE = re.compile(r'\033\[[0-9;?]*[A-Za-z]')


def _typed_chars(raw: str) -> str:
    """Walk a raw ANSI string and keep only DEFAULT-styled characters.

    Discriminator (verified live 2026-08-09): ghost prompt-suggestions and the
    placeholder render DIM (SGR 2 / 0;2) with the cursor (SGR 7) parked over
    position 0; real typed text renders in the default style (cursor after the
    text when focused, no styling at all when unfocused). Dim and
    reverse-video characters are therefore NOT typed input.
    """
    out = []
    dim = rev = False
    i = 0
    while i < len(raw):
        m = _SGR_RE.match(raw, i)
        if m:
            params = m.group(1).split(';') if m.group(1) else ['0']
            for p in params:
                if p in ('', '0'):
                    dim = rev = False
                elif p == '2':
                    dim = True
                elif p == '22':
                    dim = False
                elif p == '7':
                    rev = True
                elif p == '27':
                    rev = False
            i = m.end()
            continue
        if raw[i] == '\033':
            m2 = _CSI_RE.match(raw, i)
            i += m2.end() - m2.start() if m2 else 1
            continue
        if not dim and not rev:
            out.append(raw[i])
        i += 1
    return ''.join(out).replace('\xa0', ' ')


def composer_typed_text(raw_line: str) -> str:
    """Typed (non-ghost) composer content of a raw ANSI '❯' or '>' line, '' if none."""
    idx = raw_line.find('❯')
    plen = len('❯')
    if idx == -1:
        idx = raw_line.find('>')
        plen = len('>')
    if idx == -1:
        return ''
    text = _typed_chars(raw_line[idx + plen:]).strip()
    return '' if PLACEHOLDER_RE.match(text) else text


def _find_chrome(stripped: list[str], raw_lines: list[str] | None = None) -> dict | None:
    """Locate the live input box: bottom-most '❯' or '>' line framed by rule lines.

    Screen anatomy (bottom-up): status bar / rule / ❯ or > composer / rule / spinner
    zone / output. Submitted history also renders '❯ text' or '> text' but is never the
    bottom-most boxed one. Composer glyph is '❯' + NBSP or '> '.
    Returns {composer_idx, box_top, box_bottom, composer_text (typed only),
    composer_ghost} or None.
    """
    n = len(stripped)
    if raw_lines is None:
        raw_lines = stripped
    for i in range(n - 1, -1, -1):
        t = stripped[i].replace('\xa0', ' ').strip()
        if not (t.startswith('❯') or (t.startswith('>') and not t.startswith('>>'))):
            continue
        # look for framing rules within 4 lines either side
        top = next((j for j in range(i - 1, max(-1, i - 5), -1)
                    if _RULE_RE.match(stripped[j].strip())), None)
        bottom = next((j for j in range(i + 1, min(n, i + 5))
                       if _RULE_RE.match(stripped[j].strip())), None)
        if top is None or bottom is None:
            continue
        # display text (ghost + typed), may wrap across lines i..bottom-1
        disp = [stripped[i].replace('\xa0', ' ').strip().lstrip('❯>').strip()]
        typed = [composer_typed_text(raw_lines[i])]
        for k in range(i + 1, bottom):
            disp.append(stripped[k].strip())
            typed.append(_typed_chars(raw_lines[k]).strip())
        display = ' '.join(l for l in disp if l).strip()
        typed_text = ' '.join(l for l in typed if l).strip()
        if PLACEHOLDER_RE.match(typed_text):
            typed_text = ''
        ghost = display if (display and not typed_text) else ''
        if PLACEHOLDER_RE.match(ghost):
            ghost = ''
        return {'composer_idx': i, 'box_top': top, 'box_bottom': bottom,
                'composer_text': typed_text, 'composer_ghost': ghost}
    return None


def _spinner_zone(stripped: list[str], raw_lines: list[str], box_top: int):
    """Lines directly above the input box where spinner/turn markers are LEGAL.
    Anything above this zone is agent output and must never drive state (F1)."""
    lo = max(0, box_top - 4)
    return stripped[lo:box_top], raw_lines[lo:box_top]


def _parse_active(zone_stripped: list[str], zone_raw: list[str]) -> dict | None:
    """Detect an active turn from the spinner zone ONLY.

    Signals (any suffices):
      - 'esc to interrupt' / 'esc to cancel' hint on a zone line
      - spinner glyph + Gerund… (suffix NOT required — F2: early/tool phases
        render bare '* Moseying…')
      - braille spinner glyphs (⣾⣽⣻⢿⡿⣟⣯⣷)
      - spinner-colored (174/180/216) Gerund line
      - '(Xm Ys · ↓ N tokens)' suffix
    """
    for st, rw in zip(zone_stripped, zone_raw):
        s = st.strip()
        # Real spinner lines START with a spinner glyph; tool output in the
        # zone starts with ⎿/●/│ etc. Requiring the glyph anchor keeps quoted
        # markers in output inert even when they sit just above the box (F1).
        if not (s[:1] in _SPINNER_GLYPHS or any(c in s for c in '⣾⣽⣻⢿⡿⣟⣯⣷')):
            continue
        hit = ('esc to interrupt' in s
               or 'esc to cancel' in s
               or _SPINNER_LINE_RE.match(s)
               or any(c in s for c in '⣾⣽⣻⢿⡿⣟⣯⣷')
               or (_SPINNER_COLOR_RE.search(rw) and _GERUND_RE.search(s))
               or (_SUFFIX_RE.search(s) and ('tokens' in s or 'esc' in s)))
        if not hit:
            continue
        g = _GERUND_RE.search(s)
        suf = _SUFFIX_RE.search(s)
        braille_act = re.sub(r'^[⣾⣽⣻⢿⡿⣟⣯⣷\s]+', '', s).strip() if any(c in s for c in '⣾⣽⣻⢿⡿⣟⣯⣷') else None
        return {
            'activity': (braille_act or (g.group(1) if g else 'Active turn')).rstrip('…').rstrip('.'),
            'elapsed': (suf.group(1).strip() if suf else ''),
            'tokens': ((suf.group(2) or '').strip() if suf else ''),
        }
    return None


# ---------------------------------------------------------------------------
# pending_menu (spec 2026-08-10-interleaving-and-decision-options §2): emit a
# STRUCTURED decision menu ONLY from false-positive-free TUI chrome — never
# prose heuristics (no trailing-? class). The menu REPLACES the composer box;
# the ❯-prefixed numbered option line is the selector and does not occur in
# agent prose or in the composer (which is ❯ + text/ghost, never ❯ + "N.").
# ---------------------------------------------------------------------------

_MENU_OPT_RE = re.compile(r'^\s*([❯>])?\s*(\d{1,2})\.\s+(\S.*?)\s*$')
_MENU_TOOL_NAMES = ('Bash', 'Read', 'Write', 'Edit', 'MultiEdit', 'Grep', 'Glob',
                    'Agent', 'Task', 'WebFetch', 'WebSearch', 'NotebookEdit')
_FOOTER_HINTS = ('shift+tab', 'ctrl-', 'ctrl+', 'esc to', 'press ', '↑↓', '↑/↓', '/plan',
                 'to edit in vim', 'to navigate', 'esc skip', 'enter to')
# Free-text ("write-in") option label classes across agent runtimes — Claude's
# "Type something" AND agy/Gemini's "Write-in..." (+ common variants). MUST stay in
# sync with watch_gateway._FREE_TEXT_CLASSES. Used to set part.has_free_text so the
# surface renders a write-in box regardless of runtime (F6-render, 2026-08-24: the
# gemini card's write-in row was dropped because has_free_text only matched
# "type something").
_FREE_TEXT_LABELS = ('type something', 'write-in', 'write in', 'custom answer', 'other')
# A leading checkbox token on an option label => multi-select (toggle) menu.
# group(1) is the fill: ' ' = unchecked, ✔/✓/x/X/* = checked.
_CHECKBOX_RE = re.compile(r'^\[\s*([ xX✔✓*])\s*\]\s*')
# Multi-tab AskUserQuestion header: "←  ☒ <tab>  ✔ Submit  →". The ←/→ are
# tab-nav arrows; committing = navigate to the Submit tab then Enter (NOT
# Enter-in-place, which only toggles a checkbox). Detect a line carrying both
# nav arrows.
_TAB_ARROW_L, _TAB_ARROW_R = '\u2190', '\u2192'          # ← →
_TAB_MARKERS = '\u2610\u2611\u2612\u2714\u2713\u2717\u25cf\u2022 '  # ☐☑☒✔✓✗●•(space)

# §1.3 active-tab FOCUS: the FOCUSED tab is rendered with an ANSI background
# highlight (real capture: "\x1b[38;5;16m\x1b[48;5;153m ☐ <tab> \x1b[39m\x1b[49m").
# This is LOST when ANSI is stripped, so the passive parser (stripped-only)
# cannot tell which part is focused and defaulted to tab ORDER — wrong for any
# menu parked on part>=1. Detect the highlighted span from the RAW tab line.
# ON = a background-set SGR (256-color / truecolor / standard / bright / reverse);
# OFF = a background/full/reverse reset. Foreground-only SGR (38;5;… / 39) is
# NOT a highlight and is deliberately excluded.
_TAB_HL_ON = re.compile(
    r'\033\[(?:48;5;\d+|48;2;\d+;\d+;\d+|4[0-7]|10[0-7]|7)m')
_TAB_HL_OFF = re.compile(r'\033\[(?:49|0|27)m')


def _active_tab_index(raw_lines, tabs):
    """§1.3: index (into `tabs`) of the FOCUSED tab, derived from the ANSI
    background-highlight on the RAW tab-header line, or None if no raw line /
    no highlight / no confident label match (caller then falls back to order).

    Pure over raw_lines (un-stripped) + the already-parsed `tabs` labels. Finds
    the tab-header line (both ←/→ nav arrows), extracts the text inside the first
    highlighted span, strips ANSI + tab markers, and matches it to a tab label."""
    if not raw_lines or not tabs:
        return None
    for ln in raw_lines:
        if _TAB_ARROW_L not in ln or _TAB_ARROW_R not in ln:
            continue
        m_on = _TAB_HL_ON.search(ln)
        if not m_on:
            return None                       # header present but no focus glyph
        rest = ln[m_on.end():]
        m_off = _TAB_HL_OFF.search(rest)
        focus_raw = rest[:m_off.start()] if m_off else rest
        focus = strip_ansi(focus_raw).strip(_TAB_MARKERS).strip()
        if not focus:
            return None
        for i, t in enumerate(tabs):          # exact first, then containment
            if t == focus:
                return i
        for i, t in enumerate(tabs):
            if focus in t or t in focus:
                return i
        return None
    return None


def _parse_tab_header(lines):
    """Find a multi-tab AskUserQuestion header (a line with both ←/→ nav arrows)
    and return the ordered tab labels, else None. Each tab token is separated by
    2+ spaces and may carry a leading state marker (☐/☑/☒/✔...) which is stripped."""
    for ln in lines:
        s = ln.strip()
        if _TAB_ARROW_L not in s or _TAB_ARROW_R not in s:
            continue
        inner = s.strip(_TAB_ARROW_L + _TAB_ARROW_R + ' ')
        tabs = []
        for tok in re.split(r'\s{2,}', inner):
            label = tok.strip(_TAB_MARKERS).strip()
            if label:
                tabs.append(label)
        if tabs:
            return tabs
    return None


_TAB_CHECKED_MARKS = '\u2611\u2612\u2714\u2713'   # ☑ ☒ ✔ ✓  (answered)
_TAB_UNCHECKED_MARKS = '\u2610'                   # ☐         (unanswered)


def _parse_tab_answered_state(lines):
    """Per-question-tab answered state from the multi-tab header, or None.

    The real AUQ tab bar renders a state marker on EACH tab:
      "←  ☒ Where  ☒ Priority  ☐ Notes  ✔ Submit  →"
    ☑/☒/✔/✓ = answered, ☐ = unanswered. Returns an ordered list of
    {"label": str, "answered": bool|None} for the NON-Submit tabs (Submit is
    dropped). `answered` is None when a tab carries NO recognizable leading
    marker — the confirm-time assertion FAILS OPEN on that ambiguity (never
    false-blocks). Pure over the stripped header line. (§1.1b Stage-2 bar-3:
    the race-free positive pane-truth check at the confirm screen.)"""
    for ln in lines:
        s = ln.strip()
        if _TAB_ARROW_L not in s or _TAB_ARROW_R not in s:
            continue
        inner = s.strip(_TAB_ARROW_L + _TAB_ARROW_R + ' ')
        out = []
        for tok in re.split(r'\s{2,}', inner):
            tok = tok.strip()
            if not tok:
                continue
            # leading marker (if any) is the FIRST char when it's a known mark.
            mark = tok[0] if tok and tok[0] in (_TAB_CHECKED_MARKS + _TAB_UNCHECKED_MARKS) else None
            label = tok.strip(_TAB_MARKERS).strip()
            if not label or label.lower() == 'submit':
                continue
            if mark is None:
                answered = None                       # ambiguous -> fail-open upstream
            elif mark in _TAB_CHECKED_MARKS:
                answered = True
            else:
                answered = False
            out.append({"label": label, "answered": answered})
        return out or None
    return None


def _is_standalone_submit(lines):
    """P3: a genuinely-rendered standalone "Submit" section-line — a line whose
    WHOLE content is just "Submit" (optionally a leading ☐/✔/... state marker),
    NOT a ←/→ tab header. This is a parser-confirmed commit path even without a
    tab bar (e.g. the "Submit" row under "5. Type something"). Deliberately tight:
    "submit" inside prose or an option label ("Submit answers") does NOT match, so
    a tabless checkbox menu with no rendered Submit never fabricates has_submit."""
    for ln in lines:
        s = ln.strip()
        if _TAB_ARROW_L in s or _TAB_ARROW_R in s:
            continue                                  # tab header handled above
        if s.strip(_TAB_MARKERS).strip().lower() == 'submit':
            return True
    return False
# Max line-gap between two CONSECUTIVE options that still counts as one menu
# block (option + its detail sublines). Beyond this, a matched "N." line is an
# unrelated prose numbered-list, not the next option (block-walk boundary).
_MENU_OPT_MAX_GAP = 8

# Native permission-prompt question verbs. HARVESTED from real Claude Code
# v2.1.87 captures (2026-08-15 empirical validation, D1 gate): Edit -> "make"
# ("Do you want to make this edit to X?"), Write -> "create" ("Do you want to
# create X?"), Bash -> "proceed" ("Do you want to proceed?"), WebFetch ->
# "allow"/"fetch" ("Do you want to allow Claude to fetch this content?"). 'run'
# retained defensively (was in the original pattern; not observed but plausible
# for some Bash prompts). Do NOT widen speculatively — extend only from new REAL
# captures. Real permission prompts in this version are all proceed_numbered.
_PERM_PAT = r'do you want to (proceed|allow|run|make|create|fetch)'
# Real permission prompts' option-2 signature — chrome-independent, and never
# present in an authored AskUserQuestion's custom option labels. Every real
# capture carried one: Edit "allow all edits during this session", Bash "don't
# ask again for: <cmd>", Edit-dir "always allow access to <dir>".
_PERM_OPT_PAT = r"don.t ask again|allow all|always allow"

# Affirmative lead tokens for the perm_shaped hint (L13, ADDENDUM 2). perm_shaped
# is a STYLE hint telling the surface to render option 1 Approve-styled. It must
# only fire when option 1 is genuinely an affirmative ("Yes, deploy" / "Approve"
# / "Proceed") — NOT when the menu is a two-way FORK whose option 1 is a specific
# action ("Build the drill first, then fire for real"). Keying the hint on the
# question pattern ALONE mislabeled a fork's option 1 as "Approve" on the card
# (semantically inverted). Fail-safe direction: when unsure, do NOT set the hint
# — the card then shows the verbatim label (correct content), just without the
# Approve styling.
_AFFIRM_LEAD = re.compile(
    r'^(yes|approve|proceed|allow|confirm|accept|ok(ay)?|sure|go ahead|do it|'
    r'continue|deploy|run it|permit|grant)\b', re.IGNORECASE)


def _is_affirmative_option_label(label: str) -> bool:
    """True if an option label reads as a generic affirmative (the option a
    permission-shaped AUQ styles as 'Approve'), not a specific fork action."""
    return bool(_AFFIRM_LEAD.match((label or '').strip().lstrip('☐☑☒✔✓ ').strip()))


def _truncate_on_word_boundary(text: str, limit: int = 200) -> str:
    """Truncate to <= limit chars WITHOUT cutting mid-word, appending an ellipsis
    (L14, ADDENDUM 2). A bridged card header falls back to the question when the
    summary is empty; the old (text)[:200] left readers with fragments like
    '...next build). Armi'. Short text passes through unchanged. The ellipsis is
    the identity-tolerant U+2026 that watch_gateway._q_norm already drops, so the
    same-menu match seam is unaffected."""
    text = text or ''
    if len(text) <= limit:
        return text
    cut = text[:limit].rstrip()
    sp = cut.rfind(' ')
    if sp > 0:                      # break on the last whole word when there is one
        cut = cut[:sp].rstrip()
    return cut + '…'


def _menu_question(lines: list[str], first_opt_idx: int | None) -> str:
    """The CONTIGUOUS prose block above the options, top-down (task #8 fix,
    E2E-found apr_11a45610): the old nearest-'?'-line capture dropped the HEAD
    of a wrapped multi-line question — the [G1-E2E test] prefix never reached
    the operator's card. Walk up gathering consecutive prose lines until a blank /
    rule / option line bounds the block; join in reading order. Head-priority
    cap (the head carries prefixes/labels): 600 chars with a visible '…'.
    NOTE: question text feeds menu_op_key — menus parked ACROSS a deploy of
    this change may re-card once (dedup key shifts); accepted, one-time."""
    if first_opt_idx is None:
        first_opt_idx = len(lines)
    block: list[str] = []
    rule_label = ""                       # labeled top-rule "─── Q ───" fallback question
    for i in range(first_opt_idx - 1, max(-1, first_opt_idx - 8), -1):
        s = lines[i].strip()
        rm = _RULE_RE.match(s)
        if rm or (s and set(s) <= set('╌─━ ')):
            # A ─━ rule is the widget FRAME/border. STRUCTURAL INVARIANT (no real
            # 2.1.260 reproducer; guards a future question-line-dropped shape): the
            # walk must never cross the frame INTO SCROLLBACK PROSE and leak it as a
            # the operator decision-card question. The question+title always live BELOW the
            # top frame — anything above it is scrollback, never the menu question —
            # so STOP at the rule. If nothing was gathered below it, remember a
            # labeled rule's text ("─── Q ───") as the fallback question.
            # (Real captures stop at the blank above the question before reaching
            # the frame, so they are unaffected.)
            if not block and rm and rm.group(1):
                rule_label = rm.group(1).strip(' ─━')
            break
        if _MENU_OPT_RE.match(s):
            continue
        if not s:
            if block:
                break                     # blank ABOVE gathered prose bounds the block
            continue                      # blanks between options and prose: keep walking
        # 2.1.260 renders a long/bordered question as "│ <question>" and a short
        # ☐-title summary; strip a leading box-border (│) or ☐-state marker so
        # neither leaks into the operator's card summary.
        # (RED: fixtures/chrome-2.1.260/decision_menu_longtitle.pane.txt)
        block.insert(0, re.sub(r'^[│┃╎╏┆┇┊┋|☐☑☒✔✓✗]\s*', '', s))
    q = ' '.join(block) or rule_label
    q = re.sub(r'^(?:Question\s+\d+/\d+:\s*|\?\s*)', '', q)
    if len(q) > 600:
        q = q[:600] + '…'
    return q


def _menu_tool(lines: list[str]) -> str:
    """Tool name from a permission prompt, e.g. 'Bash' from 'Bash(echo hi)' or
    'Do you want to run …'. Empty if not parseable."""
    blob = '\n'.join(lines[-20:])
    for t in _MENU_TOOL_NAMES:
        if re.search(rf'\b{t}\s*\(', blob) or re.search(rf'\bDo you want to .*\b{t}\b', blob):
            return t
    return ''


# F6b (2026-08-24, PROVEN BY EFFECT — f6b-agy-description-EFFECT-PROOF.md): agy/
# Gemini renders an option's description INLINE on the option's own line, NOT as
# Claude's indented subline, and the tmux pane carries NO tool-structural
# delimiter — agy flattens {label, description} into one string. The only
# separator is author-written: agy's DEFAULT is ": " ("Redis: Persistent, …");
# an author may also use " — "/" – "/" - ". Split the label on the FIRST such
# separator into label + detail so the surface can render agy descriptions at
# parity with Claude. Best-effort + agy-gated: on no separator keep the whole
# label (description stays visible), never drop text.
_AGY_DESC_SEPS = (': ', ' — ', ' – ', ' - ')

def _split_agy_inline_detail(label: str) -> tuple[str, str | None]:
    """(label, detail|None) for one agy option. Splits on the EARLIEST author
    separator with non-empty text on both sides; else returns the label as-is.

    False-split guard (gm flag, e.g. 'GPT-4: fast' is a whole label, not
    label:desc): the description tail must be MULTI-WORD — a real description is
    a phrase, whereas a colon-bearing label is usually a single token. A
    single-word tail is ambiguous, so keep the whole label (text stays visible).

    Separator PRIORITY, not earliest-index (gemini-dev COUNTER, DEC-1787614864):
    ': ' is agy's structural default, so try separators in _AGY_DESC_SEPS order
    and return the first that yields a valid split. This stops an incidental
    earlier hyphen from beating the real colon — 'Phase 1 - Planning: Create the
    doc' splits at ': ' (label 'Phase 1 - Planning'), not at the ' - '."""
    for sep in _AGY_DESC_SEPS:
        idx = label.find(sep)
        if idx > 0:
            head, tail = label[:idx].strip(), label[idx + len(sep):].strip()
            if head and ' ' in tail:                # tail must be a phrase, not a token
                return head, tail
    return label, None


def parse_pending_menu(stripped: list[str], now: float | None = None,
                       has_composer_box: bool = False,
                       raw_lines: list[str] | None = None) -> dict | None:
    """Structured interactive-menu extraction. Returns the pending_menu dict or
    None. Pure function of the visible (stripped) screen lines.

    A menu REPLACES the composer input box (verified across real captures), so
    when a composer box is present this is an idle/stranded/ghost screen and any
    'deny'/'confirm'/'select' is PROSE, not chrome (F1-class marker-in-content —
    an agent narrating "approve/deny/defer loop" tripped a false allow_deny).

    raw_lines (§1.3): the UN-stripped screen lines, same length/order as
    `stripped`. Optional — supplied by the live caller (parse_status) so the
    multi-part FOCUS detector can read the ANSI tab-highlight (which tells us
    WHICH part is on screen). Stripped-only callers keep the pre-fix behavior
    (active part = first non-Submit tab). Everything else is stripped-only.
    """
    if has_composer_box:
        return None
    now = time.time() if now is None else now
    lines = [s.replace('\xa0', ' ') for s in stripped]
    # tmux pads the capture to pane height with blank lines — trim them first.
    end = len(lines)
    while end > 0 and not lines[end - 1].strip():
        end -= 1

    # BLOCK-ANCHORED WINDOW (VQ-7 fix 2026-08-12): a fixed line window truncates
    # tall menus — a 5-option AskUserQuestion with per-option detail sublines
    # exceeds 20 lines, so options fell outside and Arturo went blind to the
    # bottom options in a live Voice Deliberation call. Span the FULL contiguous
    # option block instead: find the bottom-most option near the live chrome,
    # then walk UP through connector lines (blank / rule / more-indented subline)
    # to include every option, however tall. A generous ceiling only bounds the
    # search; it never truncates the block.
    scan_lo = max(0, end - 120)
    opt_idxs = [k for k in range(scan_lo, end) if _MENU_OPT_RE.match(lines[k])]
    if opt_idxs:
        block_bottom = opt_idxs[-1]
        block_top = block_bottom
        for k in reversed(opt_idxs[:-1]):
            gap_ok = True
            # k and block_top are CONSECUTIVE option matches. Two guards decide
            # whether they belong to the SAME menu block:
            #  (1) DISTANCE: real options (with their detail sublines) sit within a
            #      few lines of each other; a large gap means k is an unrelated
            #      prose numbered-list far above the real menu, not the next option
            #      (D1 regression: "1. Use the Edit tool / 2. ..." narration 20+
            #      lines above the menu must NOT be absorbed).
            #  (2) FOOTER: menu chrome between them ends the block.
            # NB: an earlier indent test (subline must be MORE indented than the
            # marker) wrongly split AskUserQuestion menus whose detail aligns at
            # the SAME column as the "N." marker (gm stuck-menu 2026-08-16 dropped
            # options 1-2); distance replaces it without that false split.
            if block_top - k > _MENU_OPT_MAX_GAP:
                break
            for g in lines[k + 1:block_top]:
                s = g.strip()
                if not s or _RULE_RE.match(s) or set(s) <= set('╌─━ '):
                    continue
                if any(s.lower().startswith(h) for h in _FOOTER_HINTS):
                    gap_ok = False
                    break
            if not gap_ok:
                break
            block_top = k
        # window: from a few lines above the top option (to reach the question)
        # down to just past the block bottom (to catch its trailing sublines +
        # the footer, which sits within a few lines below the last option).
        start = max(0, block_top - 4)
        win_end = min(end, block_bottom + 6)
    else:
        # No option lines (degraded footer-only case): keep a small bottom window.
        start = max(0, end - 6)
        win_end = end
    tail = lines[start:win_end]
    # Footer/Allow-Deny signals count only in the live chrome region — at or
    # below the last option (not a fixed offset from screen end, which a tall
    # menu or trailing content would push the footer out of). This keeps
    # scrollback prose above the menu from supplying a false footer signal.
    foot = ('\n'.join(lines[block_bottom:win_end]).lower() if opt_idxs
            else '\n'.join(lines[max(0, end - 5):end]).lower())

    opts, selected, first_idx = [], None, None
    i = 0
    while i < len(tail):
        ln = tail[i]
        m = _MENU_OPT_RE.match(ln)
        if not m:
            i += 1
            continue
        label = m.group(3).strip()
        if any(label.lower().startswith(h) for h in _FOOTER_HINTS):
            i += 1
            continue
        if first_idx is None:
            first_idx = start + i
        # Detail sublines (C1.1): AskUserQuestion renders an indented summary
        # under each option. A detail line follows the option, is at least as
        # indented as the option NUMBER, and is not another option / footer /
        # rule / blank. Best-effort — on narrow panes a wrapped label
        # continuation is indistinguishable from a subline; appending it is
        # acceptable (spec).
        # F-NB4 (2026-08-24, effect-proven on a real AUQ): the baseline MUST be
        # the option-NUMBER column, not the raw line indent. The CURSOR row
        # renders "❯ 1." with the selector glyph at col 0, so raw indent was 0
        # and its equally-indented subtitle (col 2) survived; every NON-cursor
        # row renders "  2." (number at col 2) with its subtitle ALSO at col 2,
        # so the old `<= raw_indent` (2 <= 2) dropped it -> only option 1's
        # subtitle showed on phone+watch. `m.start(2)` is the number column and
        # is a consistent 2 across cursor/non-cursor rows; keep sublines at that
        # column or deeper, break only on a genuine dedent.
        num_col = m.start(2)
        detail_parts = []
        j = i + 1
        while j < len(tail):
            nxt = tail[j]
            s = nxt.strip()
            if not s:                              # blank ends the option block
                break
            if _MENU_OPT_RE.match(nxt):            # next option
                break
            if _RULE_RE.match(s) or set(s) <= set('╌─━ '):   # hard separator
                break
            if any(s.lower().startswith(h) for h in _FOOTER_HINTS):
                break
            if (len(nxt) - len(nxt.lstrip(' '))) < num_col:  # dedented past number
                break
            detail_parts.append(s)
            j += 1
        # Checkbox/multi-select state (gm stuck-menu 2026-08-16): AskUserQuestion
        # multi-select options render "[ ]" / "[✔]". Expose per-option `checked`
        # so the surface can distinguish a toggle (digit toggles, needs Submit)
        # from a single-select commit (digit fires). GLYPH-STRIP (punch-list §1,
        # DEC-1786866488): the checkbox is stripped from the emitted label; the
        # client draws its own mark from `checked`. Non-checkbox options -> no
        # `checked` key + label unchanged.
        cb = _CHECKBOX_RE.match(label)
        checked = None
        if cb:
            checked = cb.group(1).strip() != ''
            label = label[cb.end():].strip()      # strip "[ ]"/"[✔]" from label
        opt = {'n': m.group(2), 'label': label[:600]}
        if checked is not None:
            opt['checked'] = checked
        if detail_parts:
            opt['detail'] = ' '.join(detail_parts)[:300]
        opts.append(opt)
        if m.group(1):                      # ❯ selector cursor
            selected = m.group(2)
        if len(opts) >= 10:
            break
        i = j

    footer_select = ('enter to select' in foot or 'to navigate' in foot or
                     'enter select' in foot or 'navigate ·' in foot or
                     '↑/↓ navigate' in foot or '↑↓ navigate' in foot)
    footer_confirm = 'enter to confirm' in foot
    # Allow/Deny only as OPTION-LABEL chrome ("(Deny)", "allow once/always") —
    # bottom-anchored, never a bare prose "deny" (F1-class marker-in-content).
    opt_blob = ' '.join(o['label'] for o in opts).lower()
    opt_allow_deny = bool(re.search(r'\(deny\)|\ballow (once|always)\b', opt_blob))
    # FP-GATE permission signal stays FOOTER-ONLY. A permission phrase in the
    # footer region is real chrome; the SAME phrase in an agent's PROSE narration
    # (which _menu_question happily picks up as the 'question' line) must NOT be
    # allowed to fabricate a menu out of a plain numbered prose list. So the gate
    # never opens on the question line. (gm gap #2, empirically proven: the real
    # capture prose_perm_narration -> None must survive this fix.)
    is_perm_foot = bool(re.search(_PERM_PAT, foot))
    has_selector = selected is not None

    # FALSE-POSITIVE GATE: at least one hard chrome/permission signal. Prose
    # numbered lists have none; ghost/idle composers are excluded above.
    if not (has_selector or footer_select or footer_confirm or opt_allow_deny or is_perm_foot):
        return None

    # KIND-only permission signals — consulted ONLY after the gate above has
    # already opened via real chrome, so prose (which fails the gate) never
    # reaches them:
    #  - q_perm: the perm phrase on the QUESTION line (above the options), but
    #    NEVER for enter_select — that footer is the authored-AskUserQuestion
    #    signature; a real permission dialog is proceed_numbered/enter_confirm.
    #    This is what catches Edit/Write/Bash/WebFetch (phrase is the question
    #    line, not the footer).  (chrome-gate corrects the naive question-line OR.)
    #  - opt_perm: the real permission option-2 signature (see _PERM_OPT_PAT) —
    #    chrome-independent, so it also covers any perm prompt whose verb we have
    #    not harvested yet. This is gm's suggested option-shape discriminator.
    q_line = (_menu_question(lines, first_idx) or '').lower()
    q_perm = bool(re.search(_PERM_PAT, q_line)) and not footer_select
    opt_perm = bool(re.search(_PERM_OPT_PAT, opt_blob))

    # chrome = the matched TUI signature (priority: named footer > option chrome)
    if footer_select:
        chrome = 'enter_select'
    elif footer_confirm:
        chrome = 'enter_confirm'
    elif opt_allow_deny:
        chrome = 'allow_deny'
    else:
        chrome = 'proceed_numbered'

    # kind = what the menu is asking for
    labels = [o['label'].lower() for o in opts]
    yes_no_shaped = bool(opts) and len(opts) <= 3 and all(l.startswith(('yes', 'no')) for l in labels)
    if is_perm_foot or q_perm or opt_perm or opt_allow_deny:
        kind = 'permission'
    elif footer_select:
        kind = 'options'
    elif footer_confirm:
        kind = 'options' if (len(opts) > 2 and not yes_no_shaped) else 'yes_no'
    else:
        kind = 'yes_no' if yes_no_shaped else 'options'

    question = _menu_question(lines, first_idx)
    if kind == 'permission':
        tool = _menu_tool(lines)
        if tool and (not question or tool.lower() not in question.lower()):
            question = f'{question} [{tool}]' if question else f'Permission: {tool}'

    result = {'kind': kind, 'question': _truncate_on_word_boundary(question or ''), 'options': opts,
              'selected_n': selected, 'chrome': chrome, 'captured_at': round(now, 3)}
    # menu_family (F6, 2026-08-24): the ANSWER contract differs by agent runtime.
    # agy/Gemini's native multi-select uses SPACE to toggle + ENTER to submit (no
    # Submit tab / confirm chain) and a "Write-in..." free-text row that opens on
    # ENTER; Claude's AskUserQuestion toggles by option-number + reaches a Submit
    # tab. The surface answer-injection must drive the RIGHT contract, so expose a
    # family hint from the footer chrome (the authoritative, runtime-specific
    # signal). Absent-by-default 'claude'; only flips to 'agy' on agy's footer.
    _agy_foot = ('space toggle' in foot or 'space select' in foot
                 or ('↑/↓ navigate' in foot and 'submit' in foot and 'tab' not in foot))
    if kind == 'options':
        result['menu_family'] = 'agy' if _agy_foot else 'claude'
    # F6b: agy folds each option's description INLINE into the label (no subline);
    # split it back out into 'detail' so the surface renders it at parity with
    # Claude. Gated menu_family=='agy' — Claude's subline-captured 'detail' path
    # (above) is byte-unchanged. Mutates opts in place; parts[] shares the list.
    if result.get('menu_family') == 'agy':
        for o in opts:
            if 'detail' in o:
                continue                       # never seen for agy, but be safe
            if o['label'].strip().rstrip('.').lower() in _FREE_TEXT_LABELS:
                continue                       # the write-in row has no description
            head, det = _split_agy_inline_detail(o['label'])
            if det is not None:
                o['label'] = head[:600]
                o['detail'] = det[:300]
    # A-slice (perm-card lane, DEC-perm-card v2 msg_c22f70d4): an enter_select
    # AskUserQuestion whose QUESTION matches the permission pattern is
    # semantically a permission ("Do you want to proceed?" + Enter-to-select).
    # Carry a perm_shaped HINT so the surface can render Approve-styled verbs.
    # NOT a reclassification: kind STAYS 'options' (downstream contracts depend on
    # it); this is an additive, absent-by-default flag only on the options class.
    if (kind == 'options' and footer_select and bool(re.search(_PERM_PAT, q_line))
            and opts and _is_affirmative_option_label(opts[0].get('label', ''))):
        # L13 (ADDENDUM 2): also require option 1 to be a genuine affirmative, so
        # a two-way FORK matching the question pattern is NOT mislabeled 'Approve'.
        result['perm_shaped'] = True
    # Menu-level multi-select flag: any option carrying a checkbox => this is a
    # toggle-then-Submit menu (the surface must send a terminal Enter/Submit, not
    # rely on digit-commit). Additive; absent on single-select/permission menus.
    if any('checked' in o for o in opts):
        result['checkbox'] = True
    # Multi-tab structure (DEC-1786849794 COND-1): expose the Submit tab so the
    # gateway commits by navigating to it (Right-arrow) then Enter — NOT an
    # Enter-in-place, which only toggles the highlighted checkbox.
    tabs = _parse_tab_header(lines)
    active_tab = None
    active_idx = None
    if tabs:
        submit_idx = next((i for i, t in enumerate(tabs)
                           if t.strip().lower() == 'submit'), None)
        if submit_idx is not None:
            result['tabs'] = tabs
            result['has_submit'] = True
            result['submit_tab_index'] = submit_idx
            # §1.1b Stage-2 bar-3: per-tab answered-state (☒/☐) for the race-free
            # confirm-time all-answered assertion. Additive; None-safe downstream.
            ts = _parse_tab_answered_state(lines)
            if ts is not None:
                result['tab_state'] = ts
        # §1.3 active-tab FOCUS: prefer the ANSI-highlighted tab (which part is
        # actually on screen), from the raw line; fall back to tab ORDER (first
        # non-Submit) only when raw is absent / no highlight. Never resolve to the
        # Submit tab.
        focus_idx = _active_tab_index(raw_lines, tabs)
        if focus_idx is not None and focus_idx != submit_idx:
            active_idx = focus_idx
        else:
            active_idx = next((i for i in range(len(tabs)) if i != submit_idx), None)
        active_tab = tabs[active_idx] if active_idx is not None else None
    # P3: a standalone "Submit" section-line (no ←/→ tab bar) is also a real
    # commit path -> set has_submit (never tabs — those are tab-header-specific).
    if not result.get('has_submit') and _is_standalone_submit(lines):
        result['has_submit'] = True

    # Multi-part payload (DEC-1786866488 §A/§B): shape the CURRENT part and a
    # parts[] list. The passive detector only ever sees the current part; more
    # parts exist -> multipart + walk_complete False (the active Right-walk, a
    # SEPARATE gateway entrypoint, hydrates the rest). Legacy flat fields already
    # mirror part-1, so old clients degrade gracefully.
    #
    # part_count: the REAL AskUserQuestion tab bar renders QUESTION-NAME tabs
    # (e.g. "☐ Surfaces  ☐ Priority  ✔ Submit"), NOT "k of M" (live E2E finding
    # 2026-08-16 — my synthetic golden fixture wrongly assumed "Test k of M").
    # So count the NON-Submit tabs. Keep the "k of M" regex only as a fallback
    # for any variant that does render it.
    part_index, part_count = 0, 1
    mp = re.search(r'(\d+)\s+of\s+(\d+)', active_tab or '')
    if mp:
        part_index = int(mp.group(1)) - 1
        part_count = int(mp.group(2))
    elif tabs and result.get('has_submit'):
        # non-Submit tabs = the question parts.
        part_count = sum(1 for i, t in enumerate(tabs)
                         if i != result.get('submit_tab_index'))
        # §1.3: part_index = the FOCUSED tab's position among the non-Submit tabs
        # (0 if focus unknown). Named tabs carry no "k of M", so the on-screen
        # part index is the focused tab's ordinal, not always 0.
        if active_idx is not None:
            part_index = sum(1 for i in range(active_idx)
                             if i != result.get('submit_tab_index'))
    multipart = part_count > 1
    select = 'multi' if any('checked' in o for o in opts) else 'single'
    # tolerate a trailing period ("Type something." vs "Type something") — the
    # real AskUserQuestion renders both; matches stamp_input_kinds' rstrip('.').
    # Match ALL free-text label classes (F6-render): Claude's "type something" AND
    # agy's "write-in..." — else the agy write-in row is dropped from the surface.
    has_free_text = any(o['label'].strip().rstrip('.').lower() in _FREE_TEXT_LABELS
                        for o in opts)
    part = {'index': part_index, 'tab_label': active_tab,
            'question': result['question'], 'select': select,
            'options': opts, 'has_free_text': has_free_text}
    result['parts'] = [part]
    result['part_index'] = part_index
    result['part_count'] = part_count
    if multipart:
        result['multipart'] = True
    result['walk_complete'] = not multipart      # single-part = fully captured
    return result


def _codex_context_pct(session_name: str) -> str:
    """C-R1 codex context_pct via codex_context (rollout token events). '' on
    any failure — never guessed, never borrowed (dossier fail-closed rule)."""
    try:
        import codex_context
        res = codex_context.get_codex_session_context(session_name)
        return f"{res[0]:.0f}%" if res else ""
    except Exception:
        return ""


def parse_status(raw: str) -> dict:
    """Parse ANSI tmux output (visible screen) into a screen-tier state.

    Returns dict with state/activity/elapsed/tokens/tool/model/project/
    context_pct/is_noise (+ composer_text). Pure function of the screen —
    persistence and hook merging live in get_agent_status().
    """
    result = {
        'state': 'unknown', 'activity': '', 'elapsed': '', 'tokens': '',
        'tool': '', 'model': '', 'project': '', 'context_pct': '',
        'composer_text': '', 'composer_ghost': '', 'pending_menu': None,
        'is_noise': []
    }

    # --- CODEX screen shapes (C-R1, SPEC_codex-parity-phase2) -----------------
    # Codex CLI (0.148) markers are self-identifying and never rendered by
    # claude/gemini panes, so this branch is additive and collision-free:
    #   working: '◦ Working (Ns • esc to interrupt)'
    #   idle composer: '› Ask Codex to do anything' (dim placeholder)
    #   footer: '<model> <effort> <speed> · <cwd>'
    # context_pct is NOT read from the screen — the codex meter is a footer that
    # can disappear mid-turn; get_agent_status() fills it from the process-bound
    # rollout token events (codex_context). Unknown codex states stay 'unknown'
    # (fail-closed; no keystrokes on guessed grammar — dossier §4).
    if ('\u203a Ask Codex to do anything' in strip_ansi(raw)
            or '\u25e6 Working (' in strip_ansi(raw)):
        s = strip_ansi(raw)
        result['runtime'] = 'codex'
        fm = re.search(r'^\s*((?:gpt|o\d)[\w.-]*)\s+\w+\s+\w+\s+\u00b7\s',
                       s, re.MULTILINE)
        if fm:
            result['model'] = fm.group(1)
        wm = re.search(r'\u25e6 Working \((\d+[hms][^)]*)\u2022?[^)]*\)', s)
        if wm or '\u25e6 Working (' in s:
            result['state'] = 'working'
            result['activity'] = 'Working'
            m2 = re.search(r'\u25e6 Working \((\d+)s', s)
            if m2:
                result['elapsed'] = m2.group(1) + 's'
            return result
        if 'tab to queue message' in s:
            result['state'] = 'working'
            result['activity'] = 'Working (message queued)'
            return result
        # idle: the dim placeholder composer is present and no working marker
        result['state'] = 'idle'
        return result

    # --- noise inventory (informational only) ---
    for pat, tag in (('hook error', 'hook_errors'), ('Tip:', 'tips')):
        found = raw.count(pat)
        if found:
            result['is_noise'].append(f'{tag}:{found}')
    if 'Auto-update failed' in raw:
        result['is_noise'].append('auto_update_failed')
    if 'How is Claude doing' in raw or 'rate this' in raw.lower() or "How's the CLI experience so far" in raw:
        result['is_noise'].append('rating_prompt')

    # --- status bar fields (bottom chrome; regexes tolerant of truncation) ---
    m = re.search(r'\033\[2m(?:\033\[[0-9;]*m)*([\w. ()\[\]-]+context\))', raw)
    if m:
        result['model'] = strip_ansi(m.group(1)).strip()
    else:
        gm_match = re.search(r'(Gemini\s+[\d\.]+(?:\s+(?:Flash|Pro|Thinking))?)', raw, re.I)
        if gm_match:
            result['model'] = gm_match.group(1).strip()
    pm = re.search(r'\033\[2m(\w[\w-]*)\033\[0m\033\[38;5;246m \033\[32m', raw)
    if pm:
        result['project'] = pm.group(1)
    raw_lines = raw.split('\n')
    stripped = [strip_ansi(l) for l in raw_lines]

    # context %: COLOR-AGNOSTIC + BOTTOM-CHROME-SCOPED. The meter's % renders in
    # a DIFFERENT SGR color by fill tier — green(\033[32m) low (gm, ob-v2),
    # yellow(\033[33m) mid (app-dev-v5), orange(\033[38;5;208m) high
    # (orchestra-builder, jpb-v4), red near-full — and the ctx-exhausted skull
    # line renders gray(\033[38;5;246m) as 'N% until auto-compact'. The old
    # anchor keyed on green(32) ONLY, so it dropped every high-ctx + skull agent
    # -> the app showed null for them (WS1 app-bug 2026-08-12).
    #
    # Read the % off the STRIPPED status-bar meter line (the one carrying the
    # █/░ glyphs), independent of color. Scan from the BOTTOM (reversed): the
    # live status bar is the bottom-most meter line; a stale/FOREIGN meter line
    # can sit in SCROLLBACK above it (e.g. app-dev-v5 had a foreign 80% bar in
    # history above its real fable-5 65% bar). Requiring a '%' on the line skips
    # decorative glyph lines. 'N% until auto-compact' is REMAINING headroom, so
    # used% = 100 - N (0% remaining => 100% used); a plain '████ NN%' is used%.
    for s in reversed(stripped):
        if ('█' in s or '░' in s) and '%' in s:
            ac = re.search(r'(\d+)\s*%\s*until\s+auto-?compact', s, re.I)
            if ac:
                result['context_pct'] = str(100 - int(ac.group(1))) + '%'
            else:
                mm = re.findall(r'(\d+)\s*%', s)
                if mm:
                    result['context_pct'] = mm[-1] + '%'
            break

    chrome = _find_chrome(stripped, raw_lines)
    tail15 = [s.strip() for s in stripped[-15:] if s.strip()]

    # Structured decision menu (additive; rides every return path, never
    # changes `state`). None unless a false-positive-free menu signature hits.
    # A composer box present => not a menu screen (menus replace the box).
    result['pending_menu'] = parse_pending_menu(
        stripped, has_composer_box=bool(chrome), raw_lines=raw_lines)

    # --- permission / dialog waiting (dialog replaces or overlays the box) ---
    dialog = (
        any('Enter to confirm' in s for s in tail15)
        or any(re.match(r'^[❯>]\s*\d+\.\s', s.replace('\xa0', ' ')) for s in tail15)
        or (any('Do you want' in s for s in tail15)
            and any(re.match(r'^\d+\.\s', s) or 'Yes' == s[:3] for s in tail15))
        or (result.get('pending_menu') is not None)
    )

    if chrome:
        zone_s, zone_r = _spinner_zone(stripped, raw_lines, chrome['box_top'])
        active = _parse_active(zone_s, zone_r)
        if active:
            result.update(active)
            tool_dot = next((re.search(r'●\s*(\w+)\(', s) for s in zone_s if '●' in s), None)
            if tool_dot:
                result['state'] = 'working'
                result['tool'] = tool_dot.group(1)
                result['activity'] = f'Running {result["tool"]}'
            else:
                result['state'] = 'thinking'
            return result
        # tool execution: grey "Running…" in the zone
        if any('\033[38;5;246mRunning' in r or re.match(r'^Running…', s.strip())
               for s, r in zip(zone_s, zone_r)):
            tools = re.findall(
                r'\033\[1m(?:\033\[39m)?(Bash|Read|Write|Edit|Grep|Glob|Agent|Skill|'
                r'ToolSearch|WebFetch|WebSearch)\033\[0m', raw)
            result['state'] = 'working'
            result['tool'] = tools[-1] if tools else 'unknown'
            result['activity'] = f'Running {result["tool"]}' if tools else 'Running tools'
            return result
        if dialog:
            result['state'] = 'waiting_permission'
            result['activity'] = 'Waiting for confirmation'
            return result
        # composer content (placeholder already whitelisted)
        if chrome['composer_text']:
            result['state'] = 'stranded_input'
            result['composer_text'] = chrome['composer_text']
            result['activity'] = 'Composer holds unsubmitted text'
            return result
        # idle — recognize any completion gerund ("Baked for 1m 32s"), not just Saut…
        result['state'] = 'idle'
        # Ghost-suggestion (F2): propagate the CLI-suggested ghost ONLY on the
        # idle branch. The thinking/working/permission branches return early
        # above and never carry it; the stranded_input branch has non-empty
        # composer_text so composer_ghost is '' by construction (_find_chrome
        # L252). => a ghost can never surface mid-turn.
        result['composer_ghost'] = chrome['composer_ghost']
        done = next((_DONE_RE.search(s) for s in zone_s if _DONE_RE.search(s)), None)
        if done:
            result['activity'] = f'Completed (ran for {done.group(2).strip()})'
            result['elapsed'] = done.group(2).strip()
        elif any('new task?' in s for s in tail15) or ('/clear' in raw and 'to save' in raw):
            result['activity'] = 'Idle, suggesting new task'
        else:
            result['activity'] = 'Waiting for input'
        return result

    # --- no composer box on screen ---
    if dialog:
        result['state'] = 'waiting_permission'
        result['activity'] = 'Waiting for confirmation'
        return result
    # mid-redraw / dialog-less spinner: glyph-anchored hint near the bottom only.
    # Same signal vocabulary as _parse_active — the old 'esc to interrupt'-only
    # anchor went stale when fable-5 chrome replaced that hint with
    # '(<timer> · ↓ N tokens · thought for Ns)', so working agents flashed
    # grey/unknown during chromeless redraw phases (the operator field-catch 2026-08-18,
    # status-mismatch-investigator round 2).
    for s in (t.strip() for t in tail15[-6:]):
        if s[:1] not in _SPINNER_GLYPHS:
            continue
        if ('esc to interrupt' in s
                or _SPINNER_LINE_RE.match(s)
                or (_SUFFIX_RE.search(s) and ('tokens' in s or 'esc' in s))):
            result['state'] = 'thinking'
            result['activity'] = 'Active turn'
            return result
    # rating / feedback prompt (Claude "How is Claude doing" or Antigravity "How's the CLI experience so far")
    if any("How's the CLI experience so far" in s or "[0] Skip" in s or "How is Claude doing" in s for s in tail15):
        result['state'] = 'idle'
        result['activity'] = 'Feedback prompt (ready for input)'
        return result
    last = next((s.strip() for s in reversed(stripped)
                 if s.strip() and not all(c in '━─ ' for c in s.strip())), '')
    result['state'] = 'unknown'
    result['activity'] = f'Last visible: {last[:60]}' if last else 'Empty screen'
    return result


# ---------------------------------------------------------------------------
# Tier 0 — hook events; Tier 3 — persisted state machine
# ---------------------------------------------------------------------------

def _read_hook_event(session: str) -> dict | None:
    pane = get_pane_id(session)
    if not pane:
        return None
    path = os.path.join(EVENTS_DIR, pane.lstrip('%') + '.json')
    try:
        with open(path) as f:
            ev = json.load(f)
        return ev if isinstance(ev, dict) and 'state' in ev else None
    except (OSError, ValueError):
        return None


def mark_turn_interrupted(session: str, now: float | None = None) -> bool:
    """Write an authoritative Stop/idle pane event after a USER INTERRUPT.

    A user interrupt (app stop button / terminal Esc) never fires the CLI Stop
    hook, so the pane's last hook event stays 'working' and the F2 hook-override
    keeps reporting 'Active turn' for up to HOOK_WORKING_TTL_S (300s) — holding
    all queued/held mail (the operator field report 2026-08-18). The interrupt SOURCE
    knows the turn ended; this records it. Safe by construction: the hook
    override only ever applies when the screen reads idle, so a false idle
    event can never mask a genuinely live turn (screen-working always wins).
    Fail-silent: returns False on any error (an interrupt must never break)."""
    import time as _t
    try:
        pane = get_pane_id(session)
        if not pane:
            return False
        os.makedirs(EVENTS_DIR, exist_ok=True)
        path = os.path.join(EVENTS_DIR, pane.lstrip('%') + '.json')
        tmp = path + '.tmp'
        with open(tmp, 'w') as f:
            json.dump({'event': 'Stop', 'state': 'idle',
                       'ts': now if now is not None else _t.time(),
                       'tool': '', 'source': 'agent-interrupt'}, f)
        os.replace(tmp, path)
        return True
    except Exception:  # noqa: BLE001 — never break the interrupt path
        return False


def _state_path(session: str) -> str:
    safe = re.sub(r'[^A-Za-z0-9._-]', '_', session)
    return os.path.join(STATE_DIR, safe + '.json')


def _load_prev(session: str) -> dict:
    try:
        with open(_state_path(session)) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _persist(session: str, data: dict) -> None:
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp = _state_path(session) + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(data, f)
        os.replace(tmp, _state_path(session))
    except OSError:
        pass


def _fmt_age(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f'{s}s'
    if s < 3600:
        return f'{s // 60}m'
    return f'{s // 3600}h {(s % 3600) // 60}m'


def get_agent_status(session: str) -> dict:
    """Full agent status: hooks (tier 0) + process (1) + screen (2) + persisted
    state machine (3). Same shape as v1 plus additive keys."""
    now = time.time()
    proc = check_claude_process(session)

    if not proc['running']:
        out = {
            'session': session, 'state': 'stopped',
            'activity': 'No claude process running',
            'process': proc, 'screen': {}, 'is_noise': [],
            'confidence': 'process', 'state_age_s': None,
        }
        _persist(session, {'state': 'stopped', 'since': now,
                           'screen_hash': '', 'hash_since': now})
        return out

    # --- Telemetry-v2 deriver cutover (DEC-1788465233) ---------------------
    # For a LIVE proc, prefer the B1 deriver's fresh status.json — the single
    # authoritative oracle that RETIRES the 300/900/600 TTL heuristics below.
    # Returns BEFORE the screen scrape => the TTL path is not consulted while the
    # deriver is fresh (leg a). A stale/absent snapshot falls through unchanged
    # (leg e rollback). Sits AFTER the proc 'stopped' short-circuit so kernel
    # truth still wins for a dead proc (secondary #2).
    dv = _deriver_lookup(session, now)
    if dv is not None:
        mapped, dstate, age_s = dv
        # BUNDLE PRECONDITION: the deriver owns STATUS, but the menu-bridge,
        # watch_gateway (has_pending_menu), /agent-screen and permission rows all
        # depend on pending_menu. Carry it through by running ONLY menu detection
        # (parse_pending_menu — cli-chrome-pin-dev owns the parser), NEVER the
        # chrome status-classification the deriver retires. This is the read path
        # (get_agent_status calls from bridge/watch/api), NOT the daemon tick, so
        # it does not touch the daemon CPU budget; the capture was already paid on
        # the pre-deriver path. Fresh live capture => menus detected in real time.
        pending_menu = None
        raw = get_tmux_output(session)
        if raw is not None:
            raw_lines = raw.split('\n')
            stripped = [strip_ansi(l) for l in raw_lines]
            try:
                chrome = _find_chrome(stripped, raw_lines)
                pending_menu = parse_pending_menu(
                    stripped, has_composer_box=bool(chrome), raw_lines=raw_lines)
            except Exception:
                pending_menu = None
        _persist(session, {'state': mapped, 'since': now,
                           'screen_hash': '', 'hash_since': now})
        out = {
            'session': session, 'state': mapped,
            'activity': f'Live status: {dstate}',
            'process': proc,
            'screen': {'pending_menu': pending_menu} if pending_menu else {},
            'is_noise': [], 'confidence': 'deriver', 'state_age_s': age_s,
            'deriver_state': dstate,
        }
        if pending_menu:
            out['pending_menu'] = pending_menu       # additive; consumers read top-level
        return out

    raw = get_tmux_output(session)
    if raw is None:
        return {
            'session': session, 'state': 'unknown',
            'activity': 'Could not capture tmux output',
            'process': proc, 'screen': {}, 'is_noise': [],
            'confidence': 'process', 'state_age_s': None,
        }

    screen = parse_status(raw)
    hook = _read_hook_event(session)
    prev = _load_prev(session)

    state = screen['state']
    activity = screen['activity']
    confidence = 'screen'
    hook_info = None

    if hook:
        hook_age = max(0.0, now - float(hook.get('ts', 0)))
        hook_info = {'event': hook.get('event', ''), 'tool': hook.get('tool', ''),
                     'age_s': int(hook_age)}
        hstate = hook.get('state')
        if state in ('thinking', 'working', 'waiting_permission'):
            if hstate == 'working' and state in ('thinking', 'working'):
                confidence = 'screen+hook'
        elif hstate == 'working' and hook_age < HOOK_WORKING_TTL_S:
            # Screen missed an active turn (suffix-less spinner, redraw race,
            # theme drift…) but hooks saw prompt-submit/tool-use with no Stop
            # since -> the turn is live. This closes F2 for hooked sessions.
            state = 'working'
            tool = hook.get('tool') or ''
            activity = f'Active turn ({tool})' if tool else 'Active turn'
            confidence = 'hook'
        elif hstate == 'waiting_permission' and hook_age < HOOK_WAITING_TTL_S \
                and state == 'idle':
            state = 'waiting_permission'
            activity = 'Waiting for permission approval'
            confidence = 'hook'

    # CPU override: ONLY rescues 'unknown' (ps %cpu is a lifetime average and
    # must never promote a parsed idle screen — F6).
    if state == 'unknown' and proc['cpu'] > 5.0:
        state = 'working'
        activity = f'Active (CPU: {proc["cpu"]}%)'
        confidence = 'process'

    # --- tier 3: stalled + stranded ages ---
    shash = hashlib.md5(strip_ansi(raw).encode()).hexdigest()
    hash_since = prev.get('hash_since', now) if prev.get('screen_hash') == shash else now
    stranded = None

    if state in ('working', 'thinking'):
        hook_quiet = hook_info is None or hook_info['age_s'] > STALL_S
        if prev.get('screen_hash') == shash and (now - hash_since) >= STALL_S and hook_quiet \
                and prev.get('state') in ('working', 'thinking', 'stalled'):
            state = 'stalled'
            activity = f'No visible progress for {_fmt_age(now - hash_since)}'
            confidence = 'screen'

    if state == 'stranded_input':
        text = screen.get('composer_text', '')
        since = prev.get('stranded_since', now) if prev.get('stranded_text') == text else now
        stranded = {'text': text[:200], 'age_s': int(now - since)}
        activity = f"Unsubmitted input ({_fmt_age(now - since)}): {text[:80]!r}"

    since = prev.get('since', now) if prev.get('state') == state else now
    _persist(session, {
        'state': state, 'since': since,
        'screen_hash': shash, 'hash_since': hash_since,
        'stranded_text': (stranded or {}).get('text', ''),
        'stranded_since': (now - stranded['age_s']) if stranded else None,
    })

    out = {
        'session': session,
        'state': state,
        'activity': activity,
        'elapsed': screen.get('elapsed', ''),
        'tokens': screen.get('tokens', ''),
        'tool': screen.get('tool', '') or (hook_info or {}).get('tool', ''),
        'model': screen.get('model', ''),
        'project': screen.get('project', ''),
        # C-R1: codex context comes from the process-bound rollout token
        # events (codex screens have no persistent meter line); fail-closed ''.
        'context_pct': (screen.get('context_pct', '')
                        or (_codex_context_pct(session)
                            if screen.get("runtime") == "codex" else "")),
        'process': {
            # .get: proc dicts are constructed by callers and tests too, not
            # only by check_claude_process — a hard index here turns an
            # additive key into a breaking change (10 existing tests, caught
            # before this left the worktree).
            'pid': proc['pid'], 'cpu': proc['cpu'],
            'runtime': proc.get('runtime'),
            'rss_mb': proc['rss_mb'], 'uptime': proc['elapsed'],
        },
        'is_noise': screen['is_noise'],
        'confidence': confidence,
        'state_age_s': int(now - since),
    }
    if stranded:
        out['stranded'] = stranded
    if hook_info:
        out['hook'] = hook_info
    if screen.get('pending_menu'):
        out['pending_menu'] = screen['pending_menu']   # additive, nullable
    # Ghost-suggestion chip (F2): surface ONLY when the FINAL resolved state is
    # idle — so a hook-promoted mid-turn (idle screen -> working/waiting_permission
    # above) can never leak a chip. Fail-closed: absent unless a real idle ghost.
    if state == 'idle' and screen.get('composer_ghost'):
        out['composer_ghost'] = screen['composer_ghost']   # additive, nullable
    return out


def list_claude_sessions() -> list[str]:
    """List all tmux sessions that have a claude process."""
    try:
        result = subprocess.run(
            ['tmux', 'list-sessions', '-F', '#{session_name}'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return []
        return [s for s in result.stdout.strip().split('\n')
                if s and check_claude_process(s)['running']]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def format_oneline(status: dict) -> str:
    """Format status as a compact one-line string."""
    state_icons = {
        'working': '🔧', 'thinking': '🧠', 'idle': '💤', 'stopped': '⛔',
        'waiting_permission': '🔐', 'stranded_input': '📝', 'stalled': '⚠️',
        'unknown': '❓',
    }
    icon = state_icons.get(status['state'], '❓')
    parts = [f"{icon} {status['session']:20s} {status['state']:14s}"]
    if status.get('activity'):
        parts.append(status['activity'])
    if status.get('elapsed'):
        parts.append(f"[{status['elapsed']}]")
    if status.get('tool'):
        parts.append(f"({status['tool']})")
    if status.get('context_pct'):
        parts.append(f"ctx:{status['context_pct']}")
    if status.get('process', {}).get('cpu', 0) > 0.5:
        parts.append(f"cpu:{status['process']['cpu']}%")
    if status.get('confidence') == 'hook':
        parts.append('[hook]')
    return ' '.join(parts)


def main():
    args = sys.argv[1:]
    oneline = '--oneline' in args
    show_all = '--all' in args
    args = [a for a in args if a not in ('--oneline', '--all')]

    if show_all:
        sessions = list_claude_sessions()
        if not sessions:
            print('No active Claude Code sessions found.')
            sys.exit(1)
    elif args:
        sessions = [args[0]]
    else:
        print('Usage: agent-status.py <session> [--oneline]')
        print('       agent-status.py --all [--oneline]')
        sys.exit(1)

    results = [get_agent_status(s) for s in sessions]
    if oneline:
        for r in results:
            print(format_oneline(r))
    elif len(results) == 1:
        print(json.dumps(results[0], indent=2))
    else:
        print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
