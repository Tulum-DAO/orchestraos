"""build_swap_documents — the DP-A2 full-record list for a Blue-Green swap.

execute_swap persists ``documents`` (a list of ``(file, kind, key, record)``)
into source_records INSIDE the swap txn, so a fail-closed swap writes NOTHING and
a committed swap leaves the faithful projection coherent at COMMIT. This builder
produces exactly the records the legacy 3-store promote would have written for a
gen{N}->gen{N+1} rotation, mirroring promote_successor's new_entry / sess_entry /
agent_state:

  * successor doc  (registry.json / agent / <root>)               = the new gen{N+1}
  * successor session (agent-sessions.json / session / <root>)
  * successor state (state/agents / state_agent / <root>)
  * predecessor ARCHIVE (registry.json / agent / <root>-gen<N>)   = retired gen{N}

The archive lives under the DISTINCT key ``<root>-gen<N>`` (never the canonical
key), so the same-id-corpse splinter cannot recur (the tonight de-dup root cause,
now impossible at the source).
"""


def build_swap_documents(root, blue_generation, green, blue_record, cwd=None,
                         now=None):
    green_gen = green["generation"]
    green_sid = green.get("session_id")
    # P0.4 (leg-(ii)): the projected docs carry the green's ACTUAL gen-suffixed pane
    # binding (set by make_swap_fn) so registry/session/state agree with canonical;
    # default root keeps non-bg callers byte-identical.
    green_tmux = green.get("tmux_session") or root
    # Model + runtime describe the seat's identity: the green provisional often lacks
    # these fields, so fall back to the blue record (blue+green share the lineage) — else
    # the promoted seat lands model=None/runtime=None in the projection.
    model = green.get("model") or blue_record.get("model")
    runtime = green.get("runtime") or blue_record.get("runtime")
    resume = green.get("resume_command")
    # #15 per-runtime resume builder: a green promoted via the BG-capsule fire path
    # carries NO resume_command (only the lineage rotate_agent path stamped one), so a
    # promoted gemini seat lands resume_command=None — unresumable, the silent
    # seat-corruption class. DERIVE it from the seat's runtime + captured sid via the
    # ONE builder (promote_successor.resume_command_for): claude -> `claude --resume`,
    # gemini/agy -> `agy --conversation`, codex -> `codex --yolo resume`. A
    # missing/unknown runtime REFUSES (leaves resume absent) rather than guessing
    # claude — a gemini seat resumed as claude is the exact defect this closes. An
    # explicit green resume_command always wins (byte-identical for the claude path).
    if not resume and green_sid and runtime:
        import promote_successor as _ps
        try:
            resume = _ps.resume_command_for(runtime, green_sid)
        except _ps.RuntimeRefused:
            resume = None

    # --- successor (the new canonical gen) ---
    succ_registry = {
        "name": root,
        "generation": green_gen,
        "session_id": green_sid,
        "model": model,
        "status": "online",
        "tier": blue_record.get("tier", "T2"),
        "machine": blue_record.get("machine", "vps"),
        "cwd": cwd or blue_record.get("cwd"),
        "tmux_session": green_tmux,
    }
    if runtime:
        succ_registry["runtime"] = runtime
    if resume:
        succ_registry["resume_command"] = resume

    # The successor SESSION doc must carry the promoted seat's resumable identity
    # (runtime + model + resume_command), not just the sid — else read_ctx / the
    # idle-gate / the NEXT rotation read runtime=None off agent-sessions.json.
    succ_session = {
        "session_id": green_sid,
        "generation": green_gen,
        "status": "online",
        "tmux_session": green_tmux,
    }
    if model:
        succ_session["model"] = model
    if runtime:
        succ_session["runtime"] = runtime
    if resume:
        succ_session["resume_command"] = resume

    succ_state = {
        "name": root,
        "generation": green_gen,
        "status": "online",
    }

    # --- predecessor archive under the DISTINCT gen key ---
    archive_key = f"{root}-gen{blue_generation}"
    archive_record = dict(blue_record)
    archive_record["generation"] = blue_generation
    archive_record["status"] = "retired"
    archive_record["retired_at"] = now or "swap"
    # the archive keeps its own name/id, distinct from the canonical
    archive_record["name"] = archive_key

    return [
        ("registry.json", "agent", root, succ_registry),
        ("agent-sessions.json", "session", root, succ_session),
        ("state/agents", "state_agent", root, succ_state),
        ("registry.json", "agent", archive_key, archive_record),
    ]
