# B1 corpus — REAL Claude Code 2.1.260 pane chrome

*Harvested by cli-chrome-pin-dev, 2026-09-04, read-only `tmux capture-pane -p` from
live fleet seats (NEVER pipe-pane — AttachSweep is fleet-wide). Real captures, not
synthetic — RED-first fixtures for the menu-bridge + status re-skin. gm GO msg_3b619fab.*

## Parser ownership (per gm boundary ruling + telemetry-wiring-dev msg_171e7e9e)

- **MINE (menu/chrome DETECTION — `parse_pending_menu` path):** must classify these as
  NOT-a-decision-menu so the menu-bridge never mis-bridges them to the operator's approvals watch.
- **telemetry-wiring-dev's (STATUS classification — `_find_chrome`/`parse_status`):** the
  footer/banner/separator framing. Harvested here as a shared corpus for their re-skin;
  I do not edit their status functions.

## Fixtures

| file | element | owner | notes |
|---|---|---|---|
| `feedback_survey_quality.pane.txt` | `● How is Claude doing this session? (optional)` + `1: Bad 2: Fine 3: Good 0: Dismiss` | **MINE** | Looked like a mis-bridge risk (numbered options). **VALIDATED SAFE 2026-09-04** against the current parser — see below. |

### Validation: feedback survey is ALREADY correctly rejected (no parser change needed)

Ran the real `feedback_survey_quality.pane.txt` through `agent-status.parse_pending_menu`.
Result: `None` (not a menu) both with and without the composer guard. Double protection:
1. `_MENU_OPT_RE` = `^\s*([❯>])?\s*(\d{1,2})\.\s+(\S.*?)\s*$` requires the `N.` (digit-DOT)
   per-line option form. The survey uses `1: Bad   2: Fine ...` (colon, multiple per line),
   which does NOT match — so no option block is found.
2. The survey renders ABOVE an intact composer box, so `has_composer_box=True` → the parser
   short-circuits to `None` (a real menu REPLACES the composer box).
So this element is NOT a mis-bridge risk in the current bridge; it needs no recognizer. The
real B2 work remains the POSITIVE case (casualty #3: real 2.1.260 decision menus must be
RECOGNIZED) — that needs gm's real menu capture, still owed.
| `idle_footer.pane.txt` | composer box (`❯` empty) + status line + `⏵⏵ bypass permissions` | status (theirs) | idle seat; classify as CHROME/idle, never 'working'. |
| `completion_spinner_footer.pane.txt` | `✻ Brewed for 40s · done 1:13 AM` + composer + status w/ `/rc` chip | status (theirs) | `✻ <verb> for <time> [· done <clock>]`. Verb varies: Brewed/Cooked/Baked/Sautéed/Worked. |
| `status_rc_chip_and_model_variant.txt` | status line: `Opus 4.8 (1M context)` model form + `/rc` chip | status (theirs) | NOTE two model-display forms coexist in 2.1.260: `Opus 4.8 (1M context)` (seen on gm) vs `claude-opus-4-8[1m]` / `claude-fable-5[1m]` elsewhere. The detector's model regex must handle BOTH. |

## New 2.1.260 chrome confirmed present fleet-wide (from the harvest)

- **`✗ Auto-update failed · Try claude doctor or npm i -g @anthropic-ai/claude-code`** — a
  right-aligned banner in the status line on nearly EVERY seat (note glyph is `✗`, not `✘`).
  It is the live evidence of the auto-updater churn the root pin (Workstream A) stops. Must
  classify as CHROME (banner), never content/frame-boundary. (status — theirs)
- **`/rc` chip** — far-right status chip (seen on gm). (status — theirs)
- Two model-display formats (above).

## Still OWED for B2 (the priority)

The real 2.1.260 **agent-decision-menu** capture — gm owns it (msg_3b619fab), holding it
until the live telemetry bring-up settles. B2 (menu-bridge) goes RED-first the moment it
lands; no synthetic menu fixture will be manufactured.

## NOT harvested (out of scope)

- Codex chrome (`› Ask Codex to do anything`) — different runtime, not the claude 2.1.260 surface.
- The labeled top-separator regression (`_RULE_RE`) — already FIXED @323b20f99 and lives in
  telemetry-wiring-dev's `_find_chrome`; their fixture, not re-harvested here.
