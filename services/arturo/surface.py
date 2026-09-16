# surface.py — the operator's active-surface (screen) awareness for Arturo (voice-surface-context spec §2).
# The app (via the gateway POST /surface) maintains state/arturo/active-surface.json; Arturo reads
# it. Mode 1 = a per-turn readout line; Mode 2 (read_screen_context tool) resolves the route live.
import json
import os
import time
import urllib.request
import urllib.error
from pathlib import Path

from services.arturo.call_journal import _atomic_write

STALE_S = 120
GATEWAY = "http://127.0.0.1:9091"


def _default_focus_path(surface_path):
    """focus.json lives beside active-surface.json (state/arturo/)."""
    return Path(surface_path).parent / "focus.json"


def assert_focus(entity, source, now=None, focus_path=None):
    """Voice-assertable focus (spec §3.2). Writes state/arturo/focus.json: a first-class focus
    switch. STICKY until the next assertion, most-recent-assertion-wins, NO decay. `source` ∈
    {"voice","screen"}. Returns the focus record written."""
    if not focus_path:
        raise ValueError("assert_focus needs a focus_path")
    rec = {"entity": entity, "asserted_at": float(now if now is not None else time.time()),
           "source": source, "sticky": True}
    _atomic_write(Path(focus_path), rec)
    return rec


def current_focus(focus_path, now=None):
    """Read the voice-asserted focus record, or None if never asserted / unreadable. Sticky: never
    expires (a mid-deliberation focus timeout is maddening — spec §3.2). `now` is accepted for a
    symmetric signature but intentionally unused (no decay)."""
    try:
        rec = json.loads(Path(focus_path).read_text())
    except Exception:
        return None
    return rec if isinstance(rec, dict) and rec.get("entity") else None


def _gw_token():
    p = Path(os.environ.get("WATCH_GATEWAY_TOKEN_FILE",
                            os.path.expanduser("~/.config/jarvis/watch-gateway-token")))
    try:
        return p.read_text().strip()
    except OSError:
        return ""


def _gw_get(path, timeout=10):
    # GET the local watch gateway (bearer). Returns parsed JSON, raw text, or None on failure.
    req = urllib.request.Request(GATEWAY + path,
                                 headers={"Authorization": f"Bearer {_gw_token()}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
        try:
            return json.loads(body)
        except Exception:
            return body
    except Exception:
        return None


def readout_line(surface_path, now=None):
    """Mode 1: one honest line for the live-context block, or None to OMIT (spec: never inject
    garbage). `surface_path`: Path to active-surface.json. Returns a string or None."""
    now = now or time.time()
    try:
        d = json.loads(surface_path.read_text())
    except Exception:
        return None                      # missing / malformed → omit entirely
    cur = d.get("current") or {}
    route = cur.get("route")
    if not route:
        return "SHAW'S SCREEN: the operator is not looking at the app."
    hint = (cur.get("hint") or "").strip()
    device = cur.get("device") or "?"
    updated = cur.get("updated_at") or 0
    age = int(max(0, now - updated))
    if age > STALE_S:
        human = f"{age // 60}m" if age >= 60 else f"{age}s"
        return f"SHAW'S SCREEN: last seen {route} {human} ago (app backgrounded)."
    hint_part = f' — "{hint}"' if hint else ""
    return f"SHAW'S SCREEN: {route}{hint_part} ({age}s ago, {device})."


def call_surface(surface_path, now=None):
    """P1b fold-in (second-brain-dev msg_6ee2c708, the operator voice ask): the surface the operator is on at
    CALL START, stamped onto the vc_ journal at creation so the brain can attribute
    phone/watch/web live. Returns {"device","route","age_s"} or None (missing/malformed/empty
    → omit — a journal never carries a garbage stamp)."""
    now = now or time.time()
    try:
        cur = (json.loads(Path(surface_path).read_text()).get("current") or {})
    except Exception:
        return None
    device = cur.get("device")
    if not device:
        return None
    # Canonical vocabulary (ios-watch-dev msg_7549a899): the /surface path emits device
    # 'ios'/'web' (watch mirrors with 'watch'); the brain attributes phone/watch/web. Normalize
    # HERE so consumers never see a split vocabulary; keep the raw value for forensics.
    canon = {"ios": "phone", "iphone": "phone", "watch": "watch", "web": "web"}
    device_c = canon.get(str(device).lower(), device)
    # Watch guard (second-brain-dev-g5 msg_ea2b04bd): watch presence can be the FRESHEST
    # surface (live devices.watch slots since ios-watch-dev msg_9e1a4d2c), but NO voice path
    # exists on the watch — stamping a voice journal device=watch would be a false attribution.
    # Watch at call start => omit the field entirely (absence = unknown, the reader contract).
    if device_c == "watch":
        return None
    return {"device": device_c, "device_raw": device,
            "route": cur.get("route"),
            "age_s": int(max(0, now - (cur.get("updated_at") or now)))}


def route_kind(route):
    """Classify a route for the read_screen_context tool. Returns (kind, ident) or (None, None)."""
    if not route or not route.startswith("/"):
        return (None, None)
    parts = route.strip("/").split("/")
    head = parts[0] if parts else ""
    ident = parts[1] if len(parts) > 1 else None
    if head == "agents" and ident:
        return ("agent", ident)
    if head in ("approvals", "history") and ident:
        return ("approval", ident)
    if head == "projects" and ident:
        return ("project", ident)
    if head == "pipeline":
        return ("pipeline", None)
    if head == "arturo":
        return ("arturo", None)
    if head == "voice" and ident:
        return ("voice", ident)
    return (None, None)


def _current_route(surface_path):
    try:
        return (json.loads(surface_path.read_text()).get("current") or {}).get("route")
    except Exception:
        return None


def _entity_from_route(route):
    kind, ident = route_kind(route)
    return {"kind": kind, "id": ident} if kind else {"kind": None, "id": None}


def parse_stack(doc):
    """Return the z-ordered layer list from a surface doc's `current`.
    Back-compat: a legacy flat {route, hint} wraps to a single base layer."""
    cur = (doc or {}).get("current") or {}
    stack = cur.get("stack")
    if isinstance(stack, list) and stack:
        return sorted(stack, key=lambda l: l.get("z", 0))
    route = cur.get("route")
    if not route:
        return []
    return [{"z": 0, "role": "base", "route": route,
             "entity": _entity_from_route(route), "hint": cur.get("hint")}]


_NON_FOCUS_KINDS = {"voice", "arturo", "approval_count"}


def pick_focus(layers):
    """Top-down: first layer whose entity.kind is resolvable and not a
    non-focus kind (voice/arturo/badge-count). Returns the layer or None."""
    for layer in sorted(layers, key=lambda l: l.get("z", 0), reverse=True):
        ent = layer.get("entity") or {}
        kind = ent.get("kind")
        if kind and kind not in _NON_FOCUS_KINDS:
            return layer
    return None


def ambient_line(gw_get):
    """Cheap fleet awareness from the warm /agents cache ONLY. Never triggers a
    cold scan. Reports running count + busiest projects by WORKING-agent count.
    Honest: reports where effort IS, never goal-relative 'on track'. None on miss."""
    agents = gw_get("/agents")
    if not isinstance(agents, dict) or not isinstance(agents.get("agents"), list):
        return None
    rows = agents["agents"]
    working = [a for a in rows if a.get("state") == "working"]
    from collections import Counter
    by_project = Counter(a.get("project") for a in working if a.get("project"))
    top = ", ".join(f"{proj} ({n})" for proj, n in by_project.most_common(3))
    base = f"{len(working)} agents working of {len(rows)} running"
    return f"{base} · busiest: {top}" if top else base


def _focus_block(focus, gw_get, voice_calls_dir=None):
    """Fan out on the focused layer's entity. Returns a text block (heaviest tier)."""
    ent = (focus or {}).get("entity") or {}
    kind, ident = ent.get("kind"), ent.get("id")
    if kind == "agent" and ident:
        parts = [f"Agent '{ident}'"]
        agents = gw_get("/agents")
        if isinstance(agents, dict):
            for a in agents.get("agents", []):
                if a.get("session") == ident or a.get("id") == ident:
                    parts[0] += f" — state: {a.get('state','?')}, {a.get('activity','')}".rstrip()
                    if a.get("project"):
                        parts.append(f"Connected: project={a['project']}")
                    break
        pane = gw_get(f"/agent-screen?session={ident}&lines=60")
        pane_txt = pane.get("pane") if isinstance(pane, dict) else None
        if pane_txt:
            parts.append("Current activity:\n" + pane_txt[-1500:])
        tx = gw_get(f"/transcript?agent={ident}&limit=10")
        if isinstance(tx, dict) and tx.get("turns"):
            parts.append("Recent transcript:\n" + "\n".join(
                f"{t.get('role','?')}: {str(t.get('content', t.get('text','')))[:120]}"
                for t in tx["turns"][-6:]))
        return "\n".join(parts)
    if kind == "approval" and ident:
        row = gw_get(f"/approvals/{ident}")
        return f"Approval {ident}:\n{json.dumps(row, indent=1)[:1200]}" if row else f"Approval {ident} (unloadable)"
    if kind == "project" and ident:
        projects = gw_get("/projects")
        if isinstance(projects, dict):
            for pr in (projects.get("projects") or projects.get("clients") or []):
                if pr.get("slug") == ident or pr.get("id") == ident:
                    return f"Project '{ident}':\n{json.dumps(pr, indent=1)[:1200]}"
        return f"Project '{ident}' (card not found)"
    if kind == "pipeline":
        pl = gw_get("/pipeline")
        return f"Pipeline:\n{json.dumps(pl, indent=1)[:1200]}" if pl else "Pipeline (unloadable)"
    return None


def _recent_block(recent_events, limit=3):
    """Labels only (route/entity + nothing heavy). Newest first."""
    if not recent_events:
        return None
    seen, lines = set(), []
    for ev in reversed(recent_events):
        ent = ev.get("focused") or {}
        key = (ent.get("kind"), ent.get("id"))
        if key in seen or not ent.get("kind"):
            continue
        seen.add(key)
        lines.append(f"{ent.get('kind')} {ent.get('id') or ''}".strip())
        if len(lines) >= limit:
            break
    return "\n".join(lines) if lines else None


def resolve_stack(surface_path, gw_get=None, recent_events=None, voice_calls_dir=None):
    """Composite SEE: focus fan-out (heaviest) + recent labels + ambient. Tier-labeled
    so Gemini weights 'in front of you' first. Every tier best-effort; omit on failure."""
    gw_get = gw_get or _gw_get
    try:
        doc = json.loads(Path(surface_path).read_text())
    except Exception:
        return "the operator isn't looking at anything resolvable right now."
    layers = parse_stack(doc)
    focus = pick_focus(layers)
    sections = []
    fb = _focus_block(focus, gw_get, voice_calls_dir) if focus else None
    if fb:
        sections.append("IN FRONT OF YOU (focused — heaviest):\n" + fb)
    rb = _recent_block(recent_events or [])
    if rb:
        sections.append("AROUND THE CORNER (recent this call):\n" + rb)
    amb = ambient_line(gw_get)
    if amb:
        sections.append("AMBIENT (warm cache; where effort IS, not goal-relative):\n" + amb)
    return "\n\n".join(sections) if sections else "the operator is in the app; no resolvable focus."


def _voice_focus_block(entity, gw_get):
    """Hydrate a voice-asserted focus into a top-of-mind block: an approval pulls its
    question/from_agent/feature so "what's that about / who's it from / what's it working on" all
    answer without another the operator prompt (spec §3.2)."""
    kind = entity.get("kind")
    label = (entity.get("label") or entity.get("id") or "").strip()
    lines = [f"{kind}: {label}".strip(": ")]
    frm, feat = entity.get("from_agent"), entity.get("feature")
    if kind == "approval":
        rid = str(entity.get("id", "")).split(":", 1)[-1]
        row = gw_get(f"/approvals/{rid}") if gw_get else None
        if isinstance(row, dict):
            frm = row.get("from_agent") or frm
            feat = row.get("feature") or feat
            q = row.get("question") or row.get("summary")
            if q:
                lines.append(f"question: {q}")
    if frm:
        lines.append(f"from agent: {frm}")
    if feat:
        lines.append(f"feature: {feat}")
    return "\n".join(lines)


def build_turn_context(surface_path, gw_get=None, recent_events=None, voice_calls_dir=None,
                       focus_path=None):
    """Per-turn SEE helper for the proxy (Lane A2/v4 wires the call-site). Returns
    (blob, focused): the tier-weighted context string AND the effective focused layer (its
    `entity` dict is the DEFAULT target for inject/read tools when the operator doesn't name one).

    Effective focus (spec §3.2) = the MOST-RECENTLY-ASSERTED of {screen route, voice assertion}.
    A voice assertion is a first-class switch — screen no longer unconditionally wins. On the watch
    there's no screen → pure voice; on the phone tap and voice are peers, last-one-wins. Backward
    compatible: no focus.json → identical to the pre-focus screen-only behavior."""
    gw_get = gw_get or _gw_get
    blob = resolve_stack(surface_path, gw_get=gw_get, recent_events=recent_events,
                         voice_calls_dir=voice_calls_dir)
    try:
        doc = json.loads(Path(surface_path).read_text())
        screen_focus = pick_focus(parse_stack(doc))
        screen_ts = float((doc.get("current") or {}).get("updated_at") or 0.0)
    except Exception:
        screen_focus, screen_ts = None, 0.0

    voice = current_focus(focus_path or _default_focus_path(surface_path))
    if voice and float(voice.get("asserted_at") or 0.0) >= screen_ts:
        ent = voice.get("entity") or {}
        fb = _voice_focus_block(ent, gw_get)
        if fb:
            blob = "IN FRONT OF YOU (voice-asserted focus — heaviest):\n" + fb + "\n\n" + blob
        focused = {"z": None, "role": "voice-focus", "entity": ent, "source": "voice"}
        return blob, focused

    return blob, screen_focus


def resolve_screen(surface_path, gw_get=None, voice_calls_dir=None, focus_path=None):
    """Mode 2 (read_screen_context tool): resolve what the operator is looking at RIGHT NOW into a
    text blob Gemini can analyze. `gw_get`/`voice_calls_dir` are injectable for tests.
    Returns a human/agent-readable string. Never guesses on a dead/unknown route."""
    gw_get = gw_get or _gw_get
    route = _current_route(surface_path)
    kind, ident = route_kind(route)
    if kind is None and route:
        # gm-mine-menu-card seam 3: route_kind() only resolves an id-bearing
        # route (/approvals/{id}); a bare LIST route (e.g. /approvals, the operator
        # scrolling — no tap) falls through with no ident, even when
        # focus_entity() already named the exact card by voice and wrote it
        # to focus.json (assert_focus). build_turn_context() already consults
        # that file; this tool call did not, so a voice-located card silently
        # failed to resolve here. Route-first precedence is UNCHANGED — this
        # fallback only fires when route_kind() found nothing, and only for a
        # bare (no-id) route; an id-bearing route always wins on its own.
        parts = route.strip("/").split("/")
        if len(parts) == 1:
            voice = current_focus(focus_path or _default_focus_path(surface_path))
            ent = (voice or {}).get("entity") or {}
            if ent.get("kind") and ent.get("id"):
                kind, ident = ent["kind"], ent["id"]
    if kind is None:
        if not route:
            return "the operator isn't looking at anything in the app right now."
        return f"the operator is on {route}, but I can't resolve that screen."

    if kind == "agent":
        pane = gw_get(f"/agent-screen?session={ident}&lines=60")
        agents = gw_get("/agents")
        tx = gw_get(f"/transcript?agent={ident}&limit=40")
        row = None
        if isinstance(agents, dict):
            for a in (agents.get("agents") or []):
                if a.get("session") == ident or a.get("id") == ident:
                    row = a
                    break
        parts = [f"the operator is watching agent '{ident}'."]
        if row:
            parts.append(f"Status: {row.get('state', row.get('status', '?'))}"
                         f" — {row.get('activity', row.get('hint', ''))}".rstrip(" —"))
        # DELIB-BUG-2 / VQ-7 (the operator live): if the agent is PARKED on a decision menu, surface the
        # STRUCTURED pending_menu with ALL its options — not just the truncated pane text. Before
        # this, read_screen_context returned only "status: waiting" and Arturo was blind to options
        # 4/5 ("Type something"/"Chat about this") until the operator read them aloud.
        _menu = pane.get("pending_menu") if isinstance(pane, dict) else None
        if isinstance(_menu, dict):
            from services.arturo.deliberation import menu_blob_line
            _ml = menu_blob_line(ident, _menu)
            if _ml:
                parts.append(_ml)
                parts.append("(You can answer this: select an option with answer_menu. If the operator wants "
                             "to give a custom answer, select the 'Type something' / free-text option "
                             "with answer_menu, then send his exact words via inject_message.)")
        pane_txt = pane.get("pane") if isinstance(pane, dict) else (pane if isinstance(pane, str) else None)
        if pane_txt:
            parts.append("Recent screen output:\n" + pane_txt[-2000:])
        if isinstance(tx, dict) and tx.get("turns"):
            last = tx["turns"][-10:]
            parts.append("Recent transcript:\n" + "\n".join(
                f"{t.get('role','?')}: {str(t.get('content', t.get('text','')))[:120]}" for t in last))
        return "\n".join(parts)

    if kind == "approval":
        row = gw_get(f"/approvals/{ident}")
        if not row:
            return f"the operator is on approval {ident}, but I couldn't load it."
        return f"the operator is looking at approval {ident}:\n{json.dumps(row, indent=1)[:1500]}"

    if kind == "project":
        projects = gw_get("/projects")
        if isinstance(projects, dict):
            for p in (projects.get("projects") or projects.get("clients") or []):
                if p.get("slug") == ident or p.get("id") == ident:
                    return f"the operator is on project '{ident}':\n{json.dumps(p, indent=1)[:1500]}"
        return f"the operator is on project '{ident}', but I couldn't find that card."

    if kind == "pipeline":
        pl = gw_get("/pipeline")
        return f"the operator is on the pipeline:\n{json.dumps(pl, indent=1)[:1500]}" if pl else \
            "the operator is on the pipeline, but I couldn't load the stage totals."

    if kind in ("arturo", "voice"):
        parts = ["the operator is on the Arturo/voice screen."]
        if kind == "voice" and ident and voice_calls_dir:
            try:
                jf = Path(voice_calls_dir) / f"{ident}.json"
                parts.append(f"Call {ident}:\n{json.dumps(json.loads(jf.read_text()), indent=1)[:1200]}")
            except Exception:
                pass
        return "\n".join(parts)

    return f"the operator is on {route}, but I can't resolve that screen."
