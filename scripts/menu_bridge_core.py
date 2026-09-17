"""menu_bridge_core — schema-independent, PURE core of the decision-menu bridge.

Turns a detector `parse_pending_menu` capture into a stored menu payload that
the watch / iOS surfaces consume, plus the pure gating and reconciliation
helpers around it.

STRICT PURITY: no tmux, no subprocess, no sqlite, no ApprovalStore, no notify,
no cron, no network. Every function here is side-effect-free. The live
ApprovalStore wiring is a separate later step handled by the parent operator.
"""

import hashlib

# pending_menu PRESENCE is the authoritative signal — the detector only emits it
# when real native-menu chrome is on screen. The coarse `state` label is NOT
# reliable: a real parked AskUserQuestion reports state='working' (hook-confirmed
# "Active turn (AskUserQuestion)") — verified LIVE on a real agent, 2026-08-12.
# It is NOT 'idle' (first guess) and NOT 'waiting_permission' (synthetic-pane
# guess; that only appears when there's no hook event). So we gate on menu
# presence + debounce and do NOT filter on state, except to exclude states where
# a live composer means the human is mid-typing (belt-and-suspenders; the detector
# won't emit a menu there anyway).
_NON_BRIDGE_STATES = frozenset({"stranded_input"})


def menu_op_key(source_session: str, question: str) -> str:
    """Deterministic dedup key for a menu, derived from session + question."""
    digest = hashlib.sha256(
        (source_session + "|" + question).encode()
    ).hexdigest()[:16]
    return f"menu:{source_session}:{digest}"


def build_menu_payload(capture: dict, source_session: str) -> dict:
    """Build a NEW stored menu payload from a detector capture.

    Does not mutate `capture`. Options carry {n, label, detail?(only when
    truthy)}. input_kind is NOT stamped here: it is the single responsibility
    of watch_gateway.stamp_input_kinds (the one classifier), delegated to in the
    bridge layer (menu_bridge.bridge_one) where that live import lives. Keeping
    this pure core free of that import preserves its strict no-import purity.
    """
    options = []
    for o in capture["options"]:
        opt = {"n": o["n"], "label": o["label"]}
        if o.get("detail"):
            opt["detail"] = o["detail"]
        # Multi-select checkbox state : carry per-option
        # `checked` so the surface renders the toggle set. Additive — absent on
        # single-select/permission captures, so those payloads are unchanged.
        if o.get("checked") is not None:
            opt["checked"] = o["checked"]
        options.append(opt)

    payload = {
        "question": capture["question"],
        "options": options,
        "selected_n": capture.get("selected_n"),
        "source_session": source_session,
        "captured_at": capture.get("captured_at"),
    }
    # Menu-level multi-select structure : a checkbox/multi-tab
    # AskUserQuestion carries these so the app knows to render toggles + Submit
    # (and the gateway knows the submit-tab nav). All additive — a single-select
    # or permission menu has none of them, so its stored payload is untouched.
    #
    # multipart/part_count/part_index/walk_complete/parts are REQUIRED by the
    # client to render the multi-part pager (P1 field regression 2026-08-16: they
    # were dropped here, so a bridged multi-part card had no parts[] and the
    # client fail-safed to the raw view = the old broken thing).
    # perm_shaped (perm-card lane, a prior decision Q1): a permission-shaped AUQ
    # ("Do you want to proceed?" + enter_select) carries this hint so the card
    # renders Approve-styled verbs (relabel the affirmative to "Approve", keep the
    # raw option labels as secondary). Additive — absent on ordinary menus.
    for k in ("checkbox", "has_submit", "tabs", "submit_tab_index",
              "multipart", "part_count", "part_index", "walk_complete", "parts",
              "perm_shaped"):
        if capture.get(k) is not None:
            payload[k] = capture[k]
    return payload


def needs_hydration(capture) -> bool:
    """True when a passive capture is a multi-part menu that is NOT yet fully
    walked (part_count>1 / multipart AND walk_complete false). Such a capture
    holds only part-1; the bridge must run the active capture walk to hydrate the
    full parts[] before storing, else the client can't render the pager. Pure
    predicate — the actual walk (which presses keys on the live pane) is the
    bridge layer's job (menu_bridge.bridge_one), gated by this."""
    if not isinstance(capture, dict):
        return False
    is_multi = bool(capture.get("multipart")) or (capture.get("part_count") or 1) > 1
    return is_multi and not capture.get("walk_complete", False)


def should_bridge(
    state: str,
    pending_menu,
    first_seen_ts,
    now,
    debounce_s: float = 5.0,
) -> bool:
    """Gate: bridge whenever a native menu is present and has settled.

    True when a pending_menu is present (the authoritative signal — the detector
    only emits it on real menu chrome), the state is not an explicit non-bridge
    state (a live composer mid-type), and the debounce window has elapsed. The
    coarse `state` label is otherwise ignored on purpose: a real parked
    AskUserQuestion reports state='working', not 'idle'/'waiting_permission'.
    """
    if pending_menu is None:
        return False
    # PERMISSION-FILTER (spec: permission-filter-durable-bridge-spec.md,
    # incident <card-id>): a capture the D1 classifier stamped
    # kind='permission' is NEVER durably bridged — its sole surface is the D3
    # read-time pseudo-row (perm: id, /agent-key live-pane transport), which is
    # structurally immune to stale answers. Strictly KIND-keyed: a perm_shaped
    # AUQ is kind='options' and keeps bridging .
    if isinstance(pending_menu, dict) and pending_menu.get("kind") == "permission":
        return False
    if state in _NON_BRIDGE_STATES:
        return False
    return (now - first_seen_ts) >= debounce_s


def orphans(previously_bridged: set, currently_present: set) -> set:
    """op_keys bridged last cycle but absent this cycle (to mark resolved)."""
    return previously_bridged - currently_present
