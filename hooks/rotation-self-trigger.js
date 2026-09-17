#!/usr/bin/env node
// rotation-self-trigger.js — PostToolUse hook: the PRIMARY, self-accomplished
// rotation trigger.
//
// Companion to gsd-context-monitor.js (which warns at 35%/25% remaining — left
// intact). This fires DEEPER, at the ROTATION band (~10% remaining = ~90% used,
// the operator's handoff-at-90 rule), injecting a ONE-TIME self-rotate instruction so the
// agent authors its successor handoff + spawns + self-retires WITHOUT a central
// beat/coordinator watching. It ALSO writes a durable seam MARK so the fleet beat
// can tell 'self-triggered (skip)' from 'silent-past-threshold = wedged (nudge)'.
//
// Shared contract (shape + thresholds): scripts/focus_registry/rotation_signal.py
// (build_mark / rotation_level). The mark shape here MUST match build_mark().
//
// HARD CONTRACT (mirrors gsd-context-monitor + state-event-hook.py): ALWAYS exit 0,
// never wedge a turn. Config-driven thresholds. Opt-out for services/T0 via env.
//
// Installed by `orchestra init` (live-hook =
// moved surface). Registering it is the arming step.

const fs = require('fs');
const os = require('os');
const path = require('path');

// --- config (file overrides defaults; env opt-out) -----------------------------
const HOME = os.homedir();
const CONFIG_PATH = path.join(HOME, '.claude', 'rotation-self-trigger.json');
const DEFAULTS = {
  enabled: true,
  author_remaining: 12,     // start authoring the successor handoff
  rotation_remaining: 10,   // rotate now (~90% used)
  immediate_remaining: 8,   // rotate immediately before auto-compact
  stale_seconds: 60,
  // 'full' = may inject the autonomous spawn+retire instruction (T2/sub-agents).
  // 'warn_only' = inject author/rotate WARNINGS but never the autonomous-execute
  //   instruction (recommended for T0/T1 gm/coordinator seats — their rotation is
  //   supervised/one-tap per the WS3 T0/T1-kill-human-gated rule).
  mode: 'warn_only',
  // Marks live in the data dir so the fleet beat can read them (not /tmp).
  mark_dir: path.join(process.env.ORCHESTRA_DIR || process.env.ORCH_DIR || path.join(HOME, 'orchestra'), 'state', 'rotation-self-triggers'),
};

function loadConfig() {
  let cfg = Object.assign({}, DEFAULTS);
  try {
    if (fs.existsSync(CONFIG_PATH)) {
      Object.assign(cfg, JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8')));
    }
  } catch (e) { /* defaults */ }
  // Env control (gm-baked single var, set per-agent at spawn):
  //   ORCH_SELF_ROTATE=off        -> disabled (services)
  //   ORCH_SELF_ROTATE=warn_only  -> author-warning only, NO autonomous spawn+retire (T0/T1)
  //   ORCH_SELF_ROTATE=on|full    -> full autonomous self-rotate (T2)
  const ov = (process.env.ORCH_SELF_ROTATE || '').toLowerCase();
  if (ov === 'off' || ov === '0' || ov === 'false' || ov === 'no') cfg.enabled = false;
  else if (ov === 'warn_only') cfg.mode = 'warn_only';
  else if (ov === 'on' || ov === 'full' || ov === 'true' || ov === 'yes') cfg.mode = 'full';
  // Explicit mode override still honored (takes precedence).
  const mo = (process.env.ORCH_SELF_ROTATE_MODE || '').toLowerCase();
  if (mo === 'warn_only' || mo === 'full') cfg.mode = mo;
  return cfg;
}

// --- level ladder (mirrors rotation_signal.rotation_level) ----------------------
function rotationLevel(remaining, cfg) {
  if (remaining == null) return null;
  if (remaining <= cfg.immediate_remaining) return 'immediate';
  if (remaining <= cfg.rotation_remaining) return 'rotate';
  if (remaining <= cfg.author_remaining) return 'author';
  return null;
}
const SEVERITY = { author: 1, rotate: 2, immediate: 3 };
function shouldFire(level, lastFired) {
  if (!level) return false;
  return (SEVERITY[level] || 0) > (SEVERITY[lastFired] || 0);
}

// --- the injected instruction (escalating; warn_only strips the autonomous act) --
function buildMessage(level, remaining, usedPct, mode) {
  const head = `ROTATION SELF-TRIGGER [${level}]: remaining ${remaining}% (used ${usedPct}%). `;
  const autonomous =
    'SELF-ROTATE NOW (primary trigger, no beat is watching): ' +
    '1) author your successor handoff to the fixed schema (handoff_schema.Handoff / ' +
    'author_gate.build_author_trigger): current_goal, phase_state{plan_ref,phase,next_gate}, ' +
    'decisions[{text,rationale}], open_loops, hazards, next_3_actions[0].first_effect, ' +
    'canary_questions (deep). 2) COMMIT it. 3) spawn your successor + verify its model BY EFFECT ' +
    '(tmux has-session / it reads the handoff). 4) have the successor cite-back to gm ' +
    '(online_callback.py with read-back + canary answers). 5) self-retire (park-idle, reversible). ' +
    'This is the same flow pocket-agent ran (graded A-).';
  if (level === 'author') {
    return head + (mode === 'warn_only'
      ? 'Begin AUTHORING your successor handoff now (do not auto-spawn/retire — your rotation is supervised).'
      : 'Begin AUTHORING your successor handoff now; you will rotate shortly.');
  }
  if (mode === 'warn_only') {
    return head + 'You are at the rotation band. Finish authoring + COMMIT your handoff and ' +
      'signal READY-TO-ROTATE to gm — your actual rotate/retire is supervised (T0/T1), do NOT auto-retire.';
  }
  if (level === 'immediate') {
    return head + 'ROTATE IMMEDIATELY before auto-compact. ' + autonomous;
  }
  return head + autonomous;
}

// --- main -----------------------------------------------------------------------
let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', c => input += c);
process.stdin.on('end', () => {
  try {
    const cfg = loadConfig();
    if (!cfg.enabled) process.exit(0);

    const data = JSON.parse(input || '{}');
    const sessionId = data.session_id;
    if (!sessionId) process.exit(0);

    const tmp = os.tmpdir();
    const metricsPath = path.join(tmp, `claude-ctx-${sessionId}.json`);
    if (!fs.existsSync(metricsPath)) process.exit(0);  // subagent / fresh -> silent

    const metrics = JSON.parse(fs.readFileSync(metricsPath, 'utf8'));
    const now = Math.floor(Date.now() / 1000);
    if (metrics.timestamp && (now - metrics.timestamp) > cfg.stale_seconds) process.exit(0);

    const remaining = metrics.remaining_percentage;
    const usedPct = metrics.used_pct;
    const level = rotationLevel(remaining, cfg);
    if (!level) process.exit(0);  // above the rotation band

    // once-per-level sentinel (escalation re-fires); mirrors gsd-context-monitor's warned.json
    const sentPath = path.join(tmp, `claude-rotation-${sessionId}.json`);
    let sent = { lastFired: null };
    try { if (fs.existsSync(sentPath)) sent = JSON.parse(fs.readFileSync(sentPath, 'utf8')); } catch (e) {}
    if (!shouldFire(level, sent.lastFired)) process.exit(0);

    // fire: update sentinel, write the durable seam MARK (build_mark shape), inject.
    sent.lastFired = level;
    try { fs.writeFileSync(sentPath, JSON.stringify(sent)); } catch (e) {}

    try {
      fs.mkdirSync(cfg.mark_dir, { recursive: true });
      const mark = {
        kind: 'rotation_self_trigger',
        session_id: sessionId,
        pane: data.tmux_pane || process.env.TMUX_PANE || null,
        cwd: data.cwd || null,
        remaining: remaining,
        level: level,
        triggered_at: Date.now() / 1000,
      };
      const markPath = path.join(cfg.mark_dir, sessionId + '.json');
      const t = markPath + '.tmp';
      fs.writeFileSync(t, JSON.stringify(mark));
      fs.renameSync(t, markPath);
    } catch (e) { /* mark best-effort; never wedge the turn */ }

    const output = {
      hookSpecificOutput: {
        hookEventName: 'PostToolUse',
        additionalContext: buildMessage(level, remaining, usedPct, cfg.mode),
      },
    };
    process.stdout.write(JSON.stringify(output));
  } catch (e) {
    // silent fail — never block a tool call
  }
  process.exit(0);
});
