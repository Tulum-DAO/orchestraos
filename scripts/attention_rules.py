"""attention_rules.py — person-owned timeout rules: a trigger, a timeframe, and follow-up actions.

There are no fixed "stuck after X" thresholds anywhere. Each person decides, with per-agent overrides:

  trigger      no_progress (an agent made no progress for X) | unanswered (a question waited X for an answer)
  after_s      the timeframe, 60 s .. 7 d (no_progress cannot fire before the detector's 10 min stall mark)
  scope        optional agent; unanswered rules may also scope kind (decision|questionnaire|permission|
               human_task) and risk (low|medium|high)
  actions      remind [repeat_every_s >= 300] | mark_stuck | expire [mode default|no_answer]
               | hand_off [to supervisor|<agent>] | choose_for_me [by self|supervisor] (part 2b: an agent's own
               session decides and answers with `approval.py answer --on-behalf-of`)

Which rule applies: the most specific matching rule of the same trigger (agent 4 + kind 2 + risk 1, ties to
the newest edit). A per-agent rule switched off still wins, which is how a person silences one agent.

The clock: a rule fires on a target when now >= max(observation since, rule.updated_at, rules_epoch) +
after_s. rules_epoch is when the tables were created (the release), so nothing waiting at release is
backfilled, and editing a rule restarts its clock. Each (rule, target, action) fires once; a remind with
repeat_every_s fires again when its next_at comes.

SAFETY (the operator's rule): an action that acts FOR the person (expire, hand_off, choose_for_me) never fires on
a card whose action would be held or is risky: a permission card or grant proposal (a safety-gate card: only a
person answers those, plan Task 7a), a high-risk card, a hard-to-undo card, a protected agent's card, a card whose
declared categories (`approval.py request --category`) the agent's level and grants would hold, or an undeclared
card from an agent the gate does not cover (unless it is explicitly low risk and easy to undo). A gate card whose
held action a standing permission now covers is left to the gate, which releases it. The checks run again right
before each act and inside `approval.py answer --on-behalf-of`. A refused action is recorded with its reason;
reminding still happens.
"""
from __future__ import annotations

import json
import os
import re
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import attention_detect  # noqa: E402
import attention_items  # noqa: E402

#: The detector's stall mark (agent-status.py STALL_S): a no-progress rule cannot fire earlier than this.
STALL_S = 600
TRIGGERS = ("no_progress", "unanswered")
SCOPE_KINDS = ("decision", "questionnaire", "permission", "human_task")
SCOPE_RISKS = ("low", "medium", "high")
MIN_AFTER_S, MAX_AFTER_S = 60, 7 * 24 * 3600
MIN_REPEAT_S = 300
RETRY_S = 600                   # a hand-off / choose-for-me that could not be delivered is tried again after this
MAX_TRIES = 4                   # the first try and 3 retries, then the result is final (gave_up:<why>)
ANSWER_WITHIN_S = 900           # choose-for-me: no answer from the chooser in this long -> no_answer_from:<agent>
ON_BEHALF_ACTIONS = ("hand_off", "choose_for_me")
_NOT_A_TURN = ("crashed", "stopped", "offline", "gone")
APPROVAL_CLI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "approval.py")
_GEN = re.compile(r"-gen\d+$")
_EPISODE_SLACK_S = 120          # a re-read stall start within this is the same no-progress episode

DEFAULT_RULES = (
    {"name": "Remind me about unanswered questions", "trigger": "unanswered", "after_s": 3600,
     "actions": [{"type": "remind"}], "enabled": True},
    {"name": "Tell me when an agent stops making progress", "trigger": "no_progress", "after_s": 1200,
     "actions": [{"type": "remind"}], "enabled": False},
)


class RuleInvalid(ValueError):
    def __init__(self, code, detail=""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


def _iso(t: datetime | None = None) -> str:
    t = t or datetime.now(timezone.utc)
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).isoformat()


def _ts(v):
    try:
        t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def human(seconds: int) -> str:
    s = int(seconds)
    if s < 3600:
        return f"{max(1, round(s / 60))} min"
    if s < 86400:
        h, m = divmod(round(s / 60), 60)
        return f"{h} h" + (f" {m} min" if m else "")
    d, rem = divmod(s, 86400)
    h = round(rem / 3600)
    return f"{d} d" + (f" {h} h" if h else "")


def min_after_s(trigger) -> int:
    return STALL_S if trigger == "no_progress" else MIN_AFTER_S


# ---- the safety gate ---------------------------------------------------------------------------

@dataclass
class GateView:
    """What the safety gate reads from supervision (part 2 §8.2). Every callable may raise: the gate then refuses.
    covers(item, raw) -> a gate card's held action is covered by an allow grant now
    held(agent, hits) -> the hits this agent's level and grants would hold
    status(agent)     -> its gate coverage: protected | observing | off | not_seen | not_supported"""
    covers: Callable | None = None
    held: Callable | None = None
    status: Callable | None = None


def _is_gate_row(r) -> bool:
    try:
        from approval_schema import is_gate_row
        return is_gate_row(r)
    except Exception:  # noqa: BLE001 — cannot tell: treat as one (the safe side)
        return True


def declared_hits(raw) -> list:
    """The categories a card declared (`approval.py request --category`, stored as menu.gate_hits)."""
    from gate_classify import Hit
    menu = attention_items._menu(raw or {}) or {}
    out = []
    for h in menu.get("gate_hits") or ():
        if isinstance(h, (list, tuple)) and h and isinstance(h[0], str):
            act = h[1] if len(h) > 1 and isinstance(h[1], str) else ""
            out.append(Hit(h[0], act, "declared on the card"))
    return out


def may_act_for_person(item, raw=None, is_protected=None, gate: GateView | None = None) -> tuple[bool, str | None]:
    """May a timeout rule answer, close or hand off this card for the person? (False, why) for anything that would
    be held or is risky. `raw` is the authoritative ledger row when there is one (checked too, so a feed row
    missing a field never widens the gate). `gate` (part 2) adds the supervision checks; without it a card is
    judged on its own fields, as in part 1."""
    card = item.get("card") if isinstance(item.get("card"), dict) else {}
    rows = [r for r in (item, card, raw or {}) if r]
    if raw is not None and _is_gate_row(raw) or _is_gate_row(card):
        row = raw if raw is not None else card
        try:
            if gate is not None and gate.covers is not None and gate.covers(item, row):
                return False, "covered:released_by_gate"
        except Exception:  # noqa: BLE001
            pass
        return False, "gate_card"
    if item.get("kind") == "permission" or str(item.get("id", "")).startswith("perm:") or (
            raw and attention_items.kind_of(raw) == "permission"):
        return False, "permission"
    for r in rows:
        if r.get("risk") == "high" or r.get("risk_level") == "high":
            return False, "high_risk"
        if r.get("reversibility") in ("hard", "irreversible"):
            return False, f"reversibility_{r.get('reversibility')}"
    agent = item.get("agent") or (raw or {}).get("from_agent")
    if agent and is_protected is not None:
        try:
            # A protected seat stays protected under its rotated names (gm-gen3 is gm).
            if any(is_protected(a) for a in dict.fromkeys((agent, _GEN.sub("", agent.strip())))):
                return False, "protected_agent"
        except Exception:  # noqa: BLE001 — cannot tell: refuse, the safe side
            return False, "protected_unknown"
    if gate is None:
        return True, None
    hits = declared_hits(raw if raw is not None else card)
    if hits:
        try:
            held = gate.held(agent, hits) if gate.held is not None else hits
        except Exception:  # noqa: BLE001
            return False, "gated:unreadable"
        if held:
            return False, f"gated:{held[0].category}"
        return True, None
    try:
        st = gate.status(agent) if (gate.status is not None and agent) else "not_seen"
    except Exception:  # noqa: BLE001
        st = "unknown"
    if st != "protected":
        src = [r for r in (raw or {}, card, item) if r]
        explicit_low = any(r.get("risk_level") == "low" or r.get("risk") == "low" for r in src)
        explicit_easy = any(r.get("reversibility") == "easy" for r in src)
        if not (explicit_low and explicit_easy):
            return False, "ungated_agent"
    return True, None


def _refusal(why) -> str:
    return why if str(why).startswith("covered:") else f"safety:{why}"


# ---- actions -------------------------------------------------------------------------------------

class TimeoutAction(Protocol):
    name: str
    acts_for_person: bool

    def available(self) -> tuple[bool, str]: ...
    def validate(self, params: dict, env: dict | None = None) -> dict: ...
    def fire(self, target, rule, params, ctx) -> dict: ...


class Remind:
    name, acts_for_person = "remind", False

    def available(self):
        return True, ""

    def validate(self, params, env=None):
        out = {}
        rep = params.get("repeat_every_s")
        if rep is not None:
            if isinstance(rep, bool) or not isinstance(rep, int) or rep < MIN_REPEAT_S:
                raise RuleInvalid("invalid", f"repeat_every_s must be an integer >= {MIN_REPEAT_S}")
            out["repeat_every_s"] = rep
        return out

    def fire(self, target, rule, params, ctx):
        if target.kind == "no_progress":
            return {"ok": True, "detail": "stuck row raised"}       # the engine raises the agent's stuck row
        iid = target.item_id
        if iid.startswith("apr_"):
            ctx.notify_card(iid)
        elif iid.startswith("qnr_"):
            ctx.notify_questionnaire(iid)
        return {"ok": True, "detail": "reminded"}


class MarkStuck:
    name, acts_for_person = "mark_stuck", False

    def available(self):
        return True, ""

    def validate(self, params, env=None):
        return {}

    def fire(self, target, rule, params, ctx):
        return {"ok": True, "detail": "marked stuck"}               # the engine opens the quiet row


class Expire:
    name, acts_for_person, answers_later = "expire", True, False

    def available(self):
        return True, ""

    def validate(self, params, env=None):
        mode = params.get("mode", "default")
        if mode not in ("default", "no_answer"):
            raise RuleInvalid("invalid", "expire mode is default or no_answer")
        return {"mode": mode}

    def fire(self, target, rule, params, ctx):
        item = target.item
        iid = item["id"]
        if item.get("kind") == "human_task":
            return {"ok": False, "detail": "human_task_never_expires"}
        if iid.startswith("qnr_"):
            ok, why = may_act_for_person(item, None, ctx.is_protected, ctx.gate)
            if not ok:
                return {"ok": False, "detail": _refusal(why)}
            if not ctx.qstore.discard(iid):
                return {"ok": False, "detail": "not_pending"}
            ctx.qnr_deliver_discarded(ctx.qstore.get(iid), ctx.qstore)
            return {"ok": True, "detail": "discarded", "chosen": "no answer"}
        raw = ctx.ledger.get(iid) if iid.startswith("apr_") else None
        ok, why = may_act_for_person(item, raw, ctx.is_protected, ctx.gate)
        if not ok:
            return {"ok": False, "detail": _refusal(why)}
        if raw is None or raw.get("status") != "pending":
            return {"ok": False, "detail": "not_pending"}
        by = f"rule:{rule['id']}"
        default = raw.get("default_option") if params.get("mode", "default") == "default" else None
        if default:
            menu = attention_items._menu(raw)
            if raw.get("kind") == "menu" and menu:
                res = ctx.apply_answer(ctx.ledger, iid, "option", option_n=str(default), fire=False,
                                       surface="rule", answered_by=by)
                chosen = next((o.get("label") for o in menu.get("options") or []
                               if isinstance(o, dict) and str(o.get("n")) == str(default)), str(default))
            else:
                res = ctx.apply_answer(ctx.ledger, iid, str(default), fire=False, surface="rule", answered_by=by)
                chosen = str(default)
            if not res.get("ok") or not res.get("applied"):
                return {"ok": False, "detail": f"answer_refused:{res.get('error') or 'not applied'}"}
            if res.get("resume_row") is not None:
                ctx.schedule_resume(res["resume_row"])
            return {"ok": True, "detail": "took the default", "chosen": chosen}
        # No default (or "no answer" asked for): close it so the agent stops waiting. A live pane menu
        # cannot be closed from here (the prompt stays on the agent's screen), so it is left alone.
        if raw.get("kind") == "menu" and not _headless_card(raw) and (
                raw.get("origin") == "menu_bridge" or (attention_items._menu(raw) or {}).get("source_session")):
            return {"ok": False, "detail": "pane_menu_needs_an_answer"}
        if not ctx.ledger.discard(iid):
            return {"ok": False, "detail": "not_pending"}
        ctx.deliver_discarded(ctx.ledger.get(iid), ctx.ledger)
        return {"ok": True, "detail": "discarded", "chosen": "no answer"}


def _headless_card(row) -> bool:
    try:
        import headless_cards
        return headless_cards.is_bridged(row)
    except Exception:  # noqa: BLE001
        return False


def on_behalf_title(row, ob) -> str:
    return f"Chosen on your behalf by {ob.get('by')}: {row.get('question') or row.get('id')} ({ob.get('chosen')})"


def _registry_agents() -> dict:
    """The install's registry agents (id -> row). Empty when unreadable."""
    try:
        import approval_config
        with open(os.path.join(str(approval_config.orchestra_dir()), "registry.json")) as f:
            a = (json.load(f) or {}).get("agents")
        return a if isinstance(a, dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}


def _known_people() -> set:
    try:
        import approval_config
        d = os.path.join(str(approval_config.orchestra_dir()), "state", "users")
        return {n for n in os.listdir(d) if os.path.isdir(os.path.join(d, n))}
    except OSError:
        return set()


def _base(agent) -> str:
    return _GEN.sub("", str(agent or "").strip())


def _reg_row(registry: dict, agent):
    row = registry.get(agent) if agent else None
    if not isinstance(row, dict):
        row = registry.get(_base(agent)) if agent else None
    return row if isinstance(row, dict) else None


def _supervisor(registry: dict, agent):
    row = _reg_row(registry, agent) or {}
    sup = row.get("reports_to")
    return sup.strip() if isinstance(sup, str) and sup.strip() else None


def _card_lines(raw: dict, rule_id: str, you_hint: str = "<you>", action: str | None = None) -> tuple[str, str]:
    """(the card as text, the exact answer command) for a hand-off or choose-for-me turn."""
    iid = raw["id"]
    menu = attention_items._menu(raw) if raw.get("kind") == "menu" else None
    lines = [f"Card {iid} from {raw.get('from_agent')}: {raw.get('question') or ''}".rstrip()]
    if raw.get("summary"):
        lines.append(f"Summary: {str(raw['summary'])[:1200]}")
    if menu and isinstance(menu.get("options"), list):
        lines.append("Options:")
        for o in menu["options"]:
            if isinstance(o, dict):
                extra = " (write-in: add --answer-text \"...\")" if o.get("input_kind") == "free_text" else ""
                lines.append(f"  {o.get('n')}. {o.get('label')}{extra}")
        how = "--answer option --option-n N"
    else:
        lines.append("Options: approve | deny")
        how = "--answer approve   (or --answer deny --text \"why\")"
    if raw.get("default_option"):
        lines.append(f"The agent's default: {raw['default_option']}")
    cmd = (f"python3 {APPROVAL_CLI} answer --id {iid} {how} --surface rule --answered-by {you_hint} "
           f"--on-behalf-of {rule_id}{':' + action if action else ''} --reasoning \"<one line: why>\"")
    return "\n".join(lines), cmd


def _send(ctx, msg: dict):
    (ctx.msg_send or _default_msg_send)(msg)


def _default_msg_send(msg: dict):
    """The real msg_store send, under the same fences as approval_resume's real seams (never from a test)."""
    import approval_resume
    why = approval_resume._real_seam_refusal()
    if why:
        raise RuntimeError(f"message refused: {why}")
    approval_resume._msg_store().send(**msg)


def _tries_left(params) -> bool:
    return int(params.get("_try") or 1) < MAX_TRIES


def _retry(params, why) -> dict:
    """A delivery that could not happen now: retried after RETRY_S, at most MAX_TRIES in all (review 11)."""
    if _tries_left(params):
        return {"ok": False, "detail": f"retry:{why}", "retry_in_s": RETRY_S}
    return {"ok": False, "detail": f"gave_up:{why}"}


def _check(target, ctx):
    """(raw row, item, refusal detail). The row is re-read: the authoritative state, never a stale feed item."""
    item = target.item or {}
    iid = item.get("id") or ""
    if not iid.startswith("apr_"):
        return None, item, "safety:not_a_card"          # questionnaires are answered field by field: not handed
    raw = ctx.ledger.get(iid)
    if raw is None or raw.get("status") != "pending":
        return raw, item, "not_pending"
    ok, why = may_act_for_person(item, raw, ctx.is_protected, ctx.gate)
    if not ok:
        return raw, item, _refusal(why)
    return raw, item, None


def _validate_target(to, env):
    if to in (None, "", "supervisor"):
        return "supervisor"
    if not isinstance(to, str):
        raise RuleInvalid("unknown_agent", "to is 'supervisor' or an agent id")
    to = to.strip()
    person = (env or {}).get("person")
    if to == person or to in _known_people():
        raise RuleInvalid("unknown_person", "handing a card to a person arrives with multi-user")
    if _reg_row(_registry_agents(), to) is None:
        raise RuleInvalid("unknown_agent", f"{to} is not a registered agent")
    return to


class HandOff:
    """Hand the card to another agent (its supervisor by default), who decides it for the person. The card stays
    waiting for the person too: whoever answers first wins."""
    name, acts_for_person, answers_later = "hand_off", True, True

    def available(self):
        return True, ""

    def validate(self, params, env=None):
        return {"to": _validate_target(params.get("to"), env)}

    def fire(self, target, rule, params, ctx):
        raw, item, refused = _check(target, ctx)
        if refused:
            return {"ok": False, "detail": refused}
        registry = ctx.registry() or {}
        to = params.get("to") or "supervisor"
        dest = _supervisor(registry, raw.get("from_agent")) if to == "supervisor" else to
        if not dest:
            return _retry(params, "no_supervisor")
        if _reg_row(registry, dest) is None:
            return _retry(params, f"unknown_agent:{dest}")
        text, cmd = _card_lines(raw, rule["id"], you_hint=_base(dest), action="hand_off")
        after = human(rule["after_s"])
        body = (f"{ctx.person} didn't answer {raw.get('from_agent')}'s card within {after}. Decide it for them:\n\n"
                f"{text}\n\nAnswer with exactly:\n{cmd}\n\nThe card stays open for {ctx.person} too; whoever "
                f"answers first wins. If you can't decide it, leave it.")
        try:
            _send(ctx, {"from_agent": "attention-rules", "to_agent": dest, "type": "handoff", "priority": "high",
                        "subject": f"Decide for {ctx.person}: {raw.get('question') or raw['id']}"[:200],
                        "body": body, "metadata": {"approval_id": raw["id"], "rule_id": rule["id"]}})
        except Exception as e:  # noqa: BLE001
            return _retry(params, f"send_failed:{type(e).__name__}")
        return {"ok": True, "detail": "handed_off", "to": dest, "assignee": _base(dest), "item": raw["id"],
                "action": "hand_off"}


class ChooseForMe:
    """Ask an agent's own session to pick the option the person would most likely choose (no side model): the
    asking agent itself when it can take a turn, otherwise its supervisor."""
    name, acts_for_person, answers_later = "choose_for_me", True, True

    def available(self):
        return True, ""

    def validate(self, params, env=None):
        by = params.get("by", "self")
        if by not in ("self", "supervisor"):
            raise RuleInvalid("bad_by", "by is 'self' or 'supervisor'")
        out = {"by": by}
        w = params.get("answer_within_s")
        if w is not None:
            if isinstance(w, bool) or not isinstance(w, int) or not 60 <= w <= 86400:
                raise RuleInvalid("invalid", "answer_within_s must be an integer 60..86400")
            out["answer_within_s"] = w
        return out

    def _intent(self, raw, ctx) -> list:
        out = []
        try:
            prefs = ctx.prefs() or {}
        except Exception:  # noqa: BLE001
            prefs = {}
        texts = [f"{k}: {v}" for k, v in prefs.items() if k != "instructions" and isinstance(v, str) and v.strip()]
        if isinstance(prefs.get("instructions"), str) and prefs["instructions"].strip():
            out.append(f"{ctx.person}'s standing instructions: {prefs['instructions'].strip()[:1500]}")
        if texts:
            out.append(f"{ctx.person}'s notification settings: " + "; ".join(texts)[:600])
        try:
            hist = ctx.history(200) if ctx.history else []
        except Exception:  # noqa: BLE001
            hist = []
        kind = attention_items.kind_of(raw)
        agent = _base(raw.get("from_agent"))
        prior = []
        for h in hist:
            if h.get("id") == raw.get("id") or _base(h.get("from_agent")) != agent:
                continue
            if attention_items.kind_of(h) != kind or not h.get("answer"):
                continue
            label = h.get("answer")
            if h.get("answer") == "option":
                m = attention_items._menu(h) or {}
                label = next((o.get("label") for o in m.get("options") or []
                              if isinstance(o, dict) and str(o.get("n")) == str(h.get("option_n"))),
                             h.get("option_n"))
            prior.append(f"  {h.get('question')} → {label} ({h.get('answered_by') or 'unknown'})")
            if len(prior) == 5:
                break
        if prior:
            out.append(f"{ctx.person}'s last answers to {agent}'s {kind} cards:\n" + "\n".join(prior))
        return out

    def fire(self, target, rule, params, ctx):
        raw, item, refused = _check(target, ctx)
        if refused:
            return {"ok": False, "detail": refused}
        registry = ctx.registry() or {}
        asker = raw.get("from_agent")
        try:
            states = ctx.agent_states() or {}
            blocking = bool((attention_items.from_pending_row(
                raw, attention_items.Ctx(person=ctx.person, agent_states=states)) or {}).get("blocking", True))
        except Exception:  # noqa: BLE001 — cannot tell: treat as blocking (never ask a stopped agent)
            states, blocking = {}, True
        if params.get("by", "self") == "self" and not blocking and _reg_row(registry, asker) is not None:
            chooser = asker
        else:
            chooser = _supervisor(registry, asker)
        if not chooser:
            return _retry(params, "no_one_to_choose")
        if _reg_row(registry, chooser) is None:
            return _retry(params, f"unknown_agent:{chooser}")
        st = states.get(chooser) or states.get(_base(chooser))
        if st is None:
            return _retry(params, "chooser_not_running")          # no live session to take the turn
        if st in _NOT_A_TURN:
            return _retry(params, f"chooser_{st}")
        text, cmd = _card_lines(raw, rule["id"], you_hint=_base(chooser), action="choose_for_me")
        intent = self._intent(raw, ctx)
        body = (f"{ctx.person} didn't answer {asker}'s card within {human(rule['after_s'])} and asked you to choose "
                f"for them.\n\n{text}\n\n" + ("\n\n".join(intent) + "\n\n" if intent else "")
                + f"Pick the option {ctx.person} would most likely choose, then run:\n{cmd}\n"
                  "If you can't tell, don't answer.")
        try:
            _send(ctx, {"from_agent": "attention-rules", "to_agent": chooser, "type": "choose_for_me",
                        "priority": "high", "subject": f"Choose for {ctx.person}: {raw.get('question') or raw['id']}"[:200],
                        "body": body, "metadata": {"approval_id": raw["id"], "rule_id": rule["id"]}})
        except Exception as e:  # noqa: BLE001
            return _retry(params, f"send_failed:{type(e).__name__}")
        return {"ok": True, "detail": "asked", "chooser": chooser, "assignee": _base(chooser), "item": raw["id"],
                "action": "choose_for_me", "answer_within_s": int(params.get("answer_within_s") or ANSWER_WITHIN_S)}


ACTIONS: dict[str, TimeoutAction] = {"remind": Remind(), "mark_stuck": MarkStuck(), "expire": Expire(),
                                     "hand_off": HandOff(), "choose_for_me": ChooseForMe()}


def available_actions():
    out = []
    for name, a in ACTIONS.items():
        ok, why = a.available()
        out.append({"type": name, "available": ok, "why": why or None})
    return out


# ---- rule store -----------------------------------------------------------------------------------

def validate_rule(r: dict, env: dict | None = None) -> dict:
    trig = r.get("trigger")
    if trig not in TRIGGERS:
        raise RuleInvalid("invalid", f"trigger must be one of {TRIGGERS}")
    after = r.get("after_s")
    if isinstance(after, bool) or not isinstance(after, int) or not MIN_AFTER_S <= after <= MAX_AFTER_S:
        raise RuleInvalid("invalid", f"after_s must be an integer {MIN_AFTER_S}..{MAX_AFTER_S}")
    sa, sk, sr = r.get("scope_agent"), r.get("scope_kind"), r.get("scope_risk")
    if sa is not None and (not isinstance(sa, str) or not sa.strip()):
        raise RuleInvalid("invalid", "scope_agent must be an agent id")
    if sk is not None and sk not in SCOPE_KINDS:
        raise RuleInvalid("invalid", f"scope_kind must be one of {SCOPE_KINDS}")
    if sr is not None and sr not in SCOPE_RISKS:
        raise RuleInvalid("invalid", f"scope_risk must be one of {SCOPE_RISKS}")
    if trig == "no_progress" and (sk or sr):
        raise RuleInvalid("invalid", "a no_progress rule scopes an agent only")
    acts = r.get("actions")
    if not isinstance(acts, list) or not acts:
        raise RuleInvalid("invalid", "actions must be a non-empty list")
    norm = []
    for a in acts:
        if not isinstance(a, dict) or a.get("type") not in ACTIONS:
            raise RuleInvalid("invalid", f"unknown action {a!r}")
        impl = ACTIONS[a["type"]]
        ok, why = impl.available()
        if not ok:
            raise RuleInvalid("action_not_available", why)
        params = impl.validate({k: v for k, v in a.items() if k != "type"}, env)
        if a["type"] in ("expire",) + ON_BEHALF_ACTIONS:
            if trig != "unanswered":
                raise RuleInvalid("invalid", f"{a['type']} acts on a question: it needs the unanswered trigger")
            if sk == "permission" or sr == "high":
                raise RuleInvalid("unsafe_scope", f"{a['type']} never acts on permission or high-risk cards")
        norm.append({"type": a["type"], **params})
    return {**r, "actions": norm, "scope_agent": sa.strip() if isinstance(sa, str) else None}


class RuleStore:
    """CRUD over attention_rules / attention_rule_fires, through AttentionStore's transactions (rev bumps)."""

    FIELDS = ("name", "trigger", "after_s", "scope_agent", "scope_kind", "scope_risk", "actions", "enabled")

    def __init__(self, astore):
        self.astore = astore

    @staticmethod
    def _out(row):
        r = dict(row)
        r["actions"] = json.loads(r["actions"])
        r["available_actions"] = available_actions()
        r["min_after_s"] = min_after_s(r["trigger"])
        return r

    def list(self, person):
        return self.astore._read(lambda c: [self._out(r) for r in c.execute(
            "SELECT * FROM attention_rules WHERE person=? ORDER BY created_at, id", [person])], [])

    def get(self, person, rule_id):
        return self.astore._read(lambda c: (lambda r: self._out(r) if r else None)(c.execute(
            "SELECT * FROM attention_rules WHERE person=? AND id=?", [person, rule_id]).fetchone()), None)

    def create(self, person, *, trigger, after_s, actions, scope_agent=None, scope_kind=None, scope_risk=None,
               enabled=True, name=None, is_default=False, now=None):
        r = validate_rule({"trigger": trigger, "after_s": after_s, "actions": actions, "scope_agent": scope_agent,
                           "scope_kind": scope_kind, "scope_risk": scope_risk}, {"person": person})
        rid = f"rul_{uuid.uuid4().hex[:8]}"
        ts = _iso(now)

        def f(c):
            c.execute("INSERT INTO attention_rules (id, person, name, trigger, after_s, scope_agent, scope_kind, "
                      "scope_risk, actions, enabled, is_default, created_at, updated_at) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      [rid, person, name, r["trigger"], r["after_s"], r["scope_agent"], r["scope_kind"],
                       r["scope_risk"], json.dumps(r["actions"]), 1 if enabled else 0, 1 if is_default else 0,
                       ts, ts])
            return rid, True
        self.astore._write(f)
        return self.get(person, rid)

    def update(self, person, rule_id, now=None, **fields):
        cur = self.get(person, rule_id)
        if cur is None:
            return None
        merged = {k: cur[k] for k in self.FIELDS}
        merged.update({k: v for k, v in fields.items() if k in self.FIELDS})
        r = validate_rule(merged, {"person": person})
        ts = _iso(now)

        def f(c):
            c.execute("UPDATE attention_rules SET name=?, trigger=?, after_s=?, scope_agent=?, scope_kind=?, "
                      "scope_risk=?, actions=?, enabled=?, updated_at=? WHERE person=? AND id=?",
                      [r.get("name"), r["trigger"], r["after_s"], r["scope_agent"], r["scope_kind"],
                       r["scope_risk"], json.dumps(r["actions"]), 1 if r.get("enabled") else 0, ts, person,
                       rule_id])
            return None, True
        self.astore._write(f)
        return self.get(person, rule_id)

    def delete(self, person, rule_id) -> bool:
        def f(c):
            n = c.execute("DELETE FROM attention_rules WHERE person=? AND id=?", [person, rule_id]).rowcount
            return n > 0, n > 0
        return self.astore._write(f)

    def seed_defaults(self, person) -> bool:
        """Seed the defaults once per person. Remind-only or off, so nothing changes silently. Never re-seeded
        once the marker exists, so an edited or deleted default stays the person's choice."""
        if not self.astore._is_armed():
            return False
        marker, ts = f"defaults_seeded:{person}", _iso()
        rules = [validate_rule({"trigger": d["trigger"], "after_s": d["after_s"], "actions": d["actions"]})
                 | {"name": d["name"], "enabled": d["enabled"]} for d in DEFAULT_RULES]

        def f(c):
            # Marker check, both rules and the marker in ONE write transaction: concurrent first callers
            # (the CLI migrate, the gateway's tick, GET /attention/rules) or a crash midway never double-seed.
            if c.execute("SELECT 1 FROM attention_meta WHERE key=?", [marker]).fetchone():
                return False, False
            for r in rules:
                c.execute("INSERT INTO attention_rules (id, person, name, trigger, after_s, scope_agent, scope_kind, "
                          "scope_risk, actions, enabled, is_default, created_at, updated_at) "
                          "VALUES (?,?,?,?,?,NULL,NULL,NULL,?,?,1,?,?)",
                          [f"rul_{uuid.uuid4().hex[:8]}", person, r["name"], r["trigger"], r["after_s"],
                           json.dumps(r["actions"]), 1 if r["enabled"] else 0, ts, ts])
            c.execute("INSERT INTO attention_meta(key, value) VALUES(?, ?)", [marker, ts])
            return True, True
        return self.astore._write(f)

    # ---- fires ----
    def fires(self, rule_id=None):
        sql, args = "SELECT * FROM attention_rule_fires", []
        if rule_id:
            sql, args = sql + " WHERE rule_id=?", [rule_id]
        return self.astore._read(lambda c: [dict(r) for r in c.execute(sql + " ORDER BY fired_at, target", args)],
                                 [])

    def claim(self, rule_id, target, action, now, repeat_s=None) -> int | None:
        """Claim the next fire of (rule, target, action): its 1-based count, or None when it is not due."""
        ts = _iso(now)
        nxt = _iso(now + timedelta(seconds=repeat_s)) if repeat_s else None

        def f(c):
            r = c.execute("SELECT count, next_at FROM attention_rule_fires WHERE rule_id=? AND target=? AND action=?",
                          [rule_id, target, action]).fetchone()
            if r is None:
                c.execute("INSERT INTO attention_rule_fires (rule_id, target, action, fired_at, last_at, count, "
                          "next_at) VALUES (?,?,?,?,?,1,?)", [rule_id, target, action, ts, ts, nxt])
                return 1, True
            due = _ts(r["next_at"])
            if due is None or now < due:
                return None, False
            c.execute("UPDATE attention_rule_fires SET last_at=?, count=count+1, next_at=? WHERE rule_id=? AND "
                      "target=? AND action=?", [ts, nxt, rule_id, target, action])
            return r["count"] + 1, True
        return self.astore._write(f)

    def record(self, rule_id, target, action, result):
        def f(c):
            c.execute("UPDATE attention_rule_fires SET result=? WHERE rule_id=? AND target=? AND action=?",
                      [json.dumps(result), rule_id, target, action])
            return None, False
        self.astore._write(f)

    def record_if_unclaimed(self, rule_id, target, action, update) -> bool:
        """Write `update(current result)` only while the fire is still unclaimed, re-read inside the write
        transaction (a compare-and-set against consume() in another process). False when it was claimed first."""
        def f(c):
            r = c.execute("SELECT result FROM attention_rule_fires WHERE rule_id=? AND target=? AND action=?",
                          [rule_id, target, action]).fetchone()
            try:
                cur = json.loads((r["result"] if r else None) or "{}")
            except ValueError:
                return False, False
            if not r or cur.get("consumed_at"):
                return False, False
            c.execute("UPDATE attention_rule_fires SET result=? WHERE rule_id=? AND target=? AND action=?",
                      [json.dumps(update(cur)), rule_id, target, action])
            return True, True
        return bool(self.astore._write(f))

    def set_next(self, rule_id, target, action, at: datetime):
        """When a fire may be claimed again (a delivery retry)."""
        def f(c):
            c.execute("UPDATE attention_rule_fires SET next_at=? WHERE rule_id=? AND target=? AND action=?",
                      [_iso(at), rule_id, target, action])
            return None, False
        self.astore._write(f)

    def consume(self, rule_id, item_id, agent, now=None, *, pending_on_behalf=None, card_pending=True, action=None):
        """The on-behalf answer's claim (plan §8.1, review 10), one BEGIN IMMEDIATE CAS: a hand_off/choose_for_me
        fire of exactly (rule, item) whose result is ok, whose assignee is base(agent), not yet consumed. Returns
        (action, result) on success, else raises RuleInvalid(code) with no_handoff | handed_to_other |
        already_answered. A claim is spent once (review r3: no lease, so two claims can never race on one card):
        if the answering process dies before its answer lands, the card simply keeps waiting for the person
        (fail closed). `pending_on_behalf` is the record to write once the answer lands; reconcile() writes it if
        the process dies after the answer. `action` names
        the exact fire (a rule may hand off AND choose for me); each claim gets its own token, and only the holder
        of the token may give it back or finalise it."""
        only_action = action
        now = now or datetime.now(timezone.utc)
        ts, me = _iso(now), _base(agent)

        def f(c):
            acts = (only_action,) if only_action in ON_BEHALF_ACTIONS else ON_BEHALF_ACTIONS
            rows = c.execute(f"SELECT action, result FROM attention_rule_fires WHERE rule_id=? AND target=? AND "
                             f"action IN ({','.join('?' * len(acts))}) ORDER BY action",
                             [rule_id, item_id, *acts]).fetchall()
            found = []
            for r in rows:
                try:
                    res = json.loads(r["result"] or "{}")
                except ValueError:
                    continue
                if res.get("ok") and res.get("assignee"):
                    found.append((r["action"], res))
            if not found:
                return ("no_handoff", None), False
            mine = [(a, res) for a, res in found if _base(res.get("assignee")) == me]
            if not mine:
                return ("handed_to_other", found[0][1].get("assignee")), False
            free = [(a, res) for a, res in mine if not res.get("consumed_at")]
            if not free:
                return ("already_answered", None), False
            action, res = free[0]
            res = dict(res, consumed_at=ts, consumed_by=agent, claim=uuid.uuid4().hex)
            if pending_on_behalf is not None:
                res["pending_on_behalf"] = pending_on_behalf
            c.execute("UPDATE attention_rule_fires SET result=? WHERE rule_id=? AND target=? AND action=?",
                      [json.dumps(res), rule_id, item_id, action])
            return (action, res), True
        out = self.astore._write(f)
        code, detail = out if isinstance(out, tuple) else (None, None)
        if code in ("no_handoff", "handed_to_other", "already_answered") or code is None:
            raise RuleInvalid(code or "no_handoff", detail or "")
        return code, detail

    def unconsume(self, rule_id, item_id, action, claim=None):
        """Give the claim back when the answer itself failed (the agent may try again). Only the claim's own
        holder (its token) may: a later claim that replaced an expired one is never undone by the old one."""
        def f(c):
            r = c.execute("SELECT result FROM attention_rule_fires WHERE rule_id=? AND target=? AND action=?",
                          [rule_id, item_id, action]).fetchone()
            if r is None:
                return False, False
            res = json.loads(r["result"] or "{}")
            if claim is not None and res.get("claim") != claim:
                return False, False
            for k in ("consumed_at", "consumed_by", "pending_on_behalf", "claim"):
                res.pop(k, None)
            c.execute("UPDATE attention_rule_fires SET result=? WHERE rule_id=? AND target=? AND action=?",
                      [json.dumps(res), rule_id, item_id, action])
            return True, True
        return self.astore._write(f)

    def record_on_behalf(self, rule_id, item_id, action, on_behalf: dict, claim=None):
        """Finalise: the on_behalf record on the fire row (only for the claim's holder when a token is given)."""
        def f(c):
            r = c.execute("SELECT result FROM attention_rule_fires WHERE rule_id=? AND target=? AND action=?",
                          [rule_id, item_id, action]).fetchone()
            res = json.loads((r["result"] if r else None) or "{}")
            if claim is not None and res.get("claim") != claim:
                return False, False
            res["on_behalf"] = on_behalf
            res.pop("pending_on_behalf", None)
            c.execute("UPDATE attention_rule_fires SET result=? WHERE rule_id=? AND target=? AND action=?",
                      [json.dumps(res), rule_id, item_id, action])
            return True, True
        return self.astore._write(f)

    def reconcile(self, ledger, now=None) -> list:
        """Finish an on-behalf answer whose process died between the answer and its record: a claimed fire with a
        pending record whose card was answered by the claiming agent gets its on_behalf record (fire row and
        Updates row). Returns the item ids recorded."""
        done = []
        for fr in self.fires():
            if fr["action"] not in ON_BEHALF_ACTIONS:
                continue
            try:
                res = json.loads(fr["result"] or "{}")
            except ValueError:
                continue
            ob = res.get("pending_on_behalf")
            if not res.get("consumed_at") or res.get("on_behalf") or not isinstance(ob, dict):
                continue
            row = ledger.get(fr["target"]) if fr["target"].startswith("apr_") else None
            if not row or row.get("status") == "pending":
                continue
            if row.get("answer_surface") != "rule" or _base(row.get("answered_by")) != _base(res.get("consumed_by")):
                continue                                 # someone else (the person) answered it first
            ob = {**ob, "action": fr["action"]}
            self.record_on_behalf(fr["rule_id"], fr["target"], fr["action"], ob)
            self.astore.record_on_behalf(fr["target"], row.get("from_agent") or "", on_behalf_title(row, ob),
                                         body=ob.get("reasoning"), on_behalf=ob, rule_id=fr["rule_id"], now=now)
            done.append(fr["target"])
        return done

    def expire_asks(self, now) -> list:
        """Choose-for-me asks the chooser did not answer within answer_within_s: their result becomes
        no_answer_from:<agent> (the card keeps waiting; nothing else acts for the person)."""
        done = []
        for fr in self.fires():
            if fr["action"] != "choose_for_me":
                continue
            try:
                res = json.loads(fr["result"] or "{}")
            except ValueError:
                continue
            if not res.get("ok") or res.get("detail") != "asked" or res.get("consumed_at"):
                continue
            asked = _ts(fr["last_at"] or fr["fired_at"])
            if asked is None or now < asked + timedelta(seconds=int(res.get("answer_within_s") or ANSWER_WITHIN_S)):
                continue
            # The chooser may claim it between the read above and this write: only an unclaimed fire expires.
            if self.record_if_unclaimed(fr["rule_id"], fr["target"], "choose_for_me",
                                        lambda cur: {**cur, "ok": False,
                                                     "detail": f"no_answer_from:{cur.get('chooser')}"}):
                done.append(fr["target"])
        return done

    def episode_target(self, rule_id, agent, since: datetime) -> str:
        """A no-progress episode's target. The stall start is re-derived every tick and can jitter by a
        second, so a fire within _EPISODE_SLACK_S of the given start is the same episode."""
        prefix = f"agent:{agent}@"
        for f in self.fires(rule_id):
            if f["target"].startswith(prefix):
                t = _ts(f["target"][len(prefix):])
                if t is not None and abs((t - since).total_seconds()) <= _EPISODE_SLACK_S:
                    return f["target"]
        return prefix + _iso(since.replace(microsecond=0))

    def reminders(self):
        """target -> {rule_id, n}: the latest remind fire per waiting item (the builder raises its push)."""
        out = {}
        for f in self.fires():
            if f["action"] == "remind" and not f["target"].startswith("agent:"):
                out[f["target"]] = {"rule_id": f["rule_id"], "n": f["count"]}
        return out

    def on_behalf_map(self):
        out = {}
        for f in self.fires():
            try:
                res = json.loads(f["result"] or "{}")
            except ValueError:
                continue
            if res.get("ok") and isinstance(res.get("on_behalf"), dict):
                out[f["target"]] = res["on_behalf"]
        return out


# ---- matching ----------------------------------------------------------------------------------

def _agent_matches(scope, agent) -> bool:
    if scope is None:
        return True
    return bool(agent) and scope in (agent, _GEN.sub("", agent.strip()))


def pick(rules, *, trigger, agent, kind=None, risk=None):
    """The single most specific rule of this trigger matching the target (enabled or not), else None."""
    best, best_key = None, None
    for r in rules:
        if r["trigger"] != trigger or not _agent_matches(r.get("scope_agent"), agent):
            continue
        if r.get("scope_kind") is not None and r["scope_kind"] != kind:
            continue
        if r.get("scope_risk") is not None and r["scope_risk"] != risk:
            continue
        key = ((4 if r.get("scope_agent") else 0) + (2 if r.get("scope_kind") else 0)
               + (1 if r.get("scope_risk") else 0), r.get("updated_at") or "", r.get("created_at") or "")
        if best_key is None or key > best_key:
            best, best_key = r, key
    return best


# ---- engine ------------------------------------------------------------------------------------

@dataclass
class EngineCtx:
    person: str
    ledger: object
    qstore: object
    apply_answer: Callable                       # watch_gateway.apply_answer (called with fire=False)
    notify_card: Callable                        # approval_notify.notify(rid)
    notify_questionnaire: Callable               # approval_notify.notify_questionnaire(qid)
    deliver_discarded: Callable                  # approval_resume.deliver_discarded(row, store)
    qnr_deliver_discarded: Callable              # questionnaire_resume.deliver_discarded(row, store)
    schedule_resume: Callable                    # the gateway runs approval_resume.fire_resume for it
    is_protected: Callable = lambda agent: False
    observe: Callable | None = None              # now -> observations (tests); the gateway passes them in
    log: Callable = field(default=lambda msg: print(f"[attention_rules] {msg}", file=sys.stderr))
    # part 2b (hand off, choose for me)
    msg_send: Callable | None = None             # one msg_store message as a dict (default: the real store)
    registry: Callable = field(default=_registry_agents)    # -> {agent id: registry row}
    agent_states: Callable = lambda: {}          # -> {agent id: /agents state}
    history: Callable | None = None              # limit -> answered ledger rows, newest first
    prefs: Callable = lambda: {}                 # -> the person's prefs.json (standing instructions)
    gate: GateView | None = None                 # the supervision side of the safety gate


@dataclass
class _Target:
    kind: str                                    # unanswered | no_progress
    key: str                                     # fires target
    agent: str
    since: datetime
    item: dict | None = None

    @property
    def item_id(self):
        return self.item["id"] if self.item else None


class Engine:
    def __init__(self, rule_store: RuleStore, astore, ctx: EngineCtx):
        self.rs, self.astore, self.ctx = rule_store, astore, ctx

    def tick(self, now, observations=None, agents_ok=True):
        if observations is None:
            observations = self.ctx.observe(now) if self.ctx.observe else []
        rules = self.rs.list(self.ctx.person)
        epoch = _ts(self.astore.meta("rules_epoch")) or now
        stalled = set()
        results = []
        for ob in observations:
            if isinstance(ob, attention_detect.NoProgress):
                stalled.add(ob.agent)
                rule = pick(rules, trigger="no_progress", agent=ob.agent)
                if not rule or not rule["enabled"]:
                    continue
                target = _Target("no_progress", self.rs.episode_target(rule["id"], ob.agent, ob.since),
                                 ob.agent, ob.since)
            else:
                it = ob.item
                rule = pick(rules, trigger="unanswered", agent=it.get("agent"), kind=it.get("kind"),
                            risk=it.get("risk"))
                if not rule or not rule["enabled"]:
                    continue
                target = _Target("unanswered", it["id"], it.get("agent"), ob.since, it)
            anchor = max(target.since, _ts(rule["updated_at"]) or epoch, epoch)
            if now < anchor + timedelta(seconds=rule["after_s"]):
                continue
            results += self._fire_rule(rule, target, now)
        try:
            self.rs.expire_asks(now)
            self.rs.reconcile(self.ctx.ledger, now)
        except Exception as e:  # noqa: BLE001
            self.ctx.log(f"on-behalf sweep failed: {e!r}")
        if agents_ok:
            for row in self.astore.open_events(kinds=("stuck",)):
                if row["dedup_key"].endswith(":no_progress") and row["agent"] not in stalled:
                    self.astore.resolve(row["id"], "progressed", now=now)
        return results

    def _fire_rule(self, rule, target, now):
        out = []
        raised = {}
        for a in rule["actions"]:
            impl = ACTIONS.get(a["type"])
            if impl is None:
                continue
            params = {k: v for k, v in a.items() if k != "type"}
            n = self.rs.claim(rule["id"], target.key, a["type"], now,
                              repeat_s=params.get("repeat_every_s") if a["type"] == "remind" else None)
            if n is None:
                continue
            try:
                ok, why = impl.available()
                res = impl.fire(target, rule, {**params, "_try": n}, self.ctx) if ok else {
                    "ok": False, "detail": "not_available"}
                if res.get("ok") and a["type"] in ("remind", "mark_stuck"):
                    raised[a["type"]] = n
                if str(res.get("detail", "")).startswith("retry:"):
                    self.rs.set_next(rule["id"], target.key, a["type"],
                                     now + timedelta(seconds=int(res.pop("retry_in_s", RETRY_S))))
                if res.get("ok") and impl.acts_for_person and not getattr(impl, "answers_later", False):
                    res["on_behalf"] = {"by": f"rule:{rule['id']}", "rule_id": rule["id"], "action": a["type"],
                                        "chosen": res.get("chosen"),
                                        "reasoning": f"No answer within {human(rule['after_s'])}; your rule "
                                                     f"\"{rule.get('name') or rule['id']}\" "
                                                     + ("takes the agent's default" if res.get("chosen") != "no answer"
                                                        else "closes it with no answer")}
                    self.astore.record_on_behalf(
                        target.item_id, target.agent or "",
                        f"Closed for you: {target.item.get('title') or target.item_id} "
                        f"({res.get('chosen')})", body=res["on_behalf"]["reasoning"],
                        on_behalf=res["on_behalf"], rule_id=rule["id"], now=now)
            except Exception as e:  # noqa: BLE001 — one bad card never stops the tick
                res = {"ok": False, "detail": f"error: {e}"}
                self.ctx.log(f"{a['type']} on {target.key} failed: {e!r}")
            self.rs.record(rule["id"], target.key, a["type"], res)
            out.append({"rule_id": rule["id"], "target": target.key, "action": a["type"], **res})
        if raised:
            self._raise_rows(rule, target, raised, now)
        return out

    def _raise_rows(self, rule, target, raised, now):
        """The Updates rows a remind / mark_stuck asks for. Remind wins over quiet when both fire."""
        if "remind" in raised:
            po = {"reminder": {"rule_id": rule["id"], "n": raised["remind"]}}
        else:
            po = {"decision": "list", "reason": f"rule:{rule['id']}"}
        if target.kind == "no_progress":
            self.astore.open_condition(
                "stuck", target.agent, "no_progress", f"stuck:{target.agent}:no_progress",
                f"{target.agent} has made no progress for {human(rule['after_s'])} (your rule)",
                now=now, rule_id=rule["id"], push_override=po)
        elif "mark_stuck" in raised:
            self.astore.open_condition(
                "stuck", target.agent or "", "question_unanswered",
                f"stuck:{target.agent}:question:{target.item_id}",
                f"{target.agent} has waited {human(rule['after_s'])} for your answer (your rule)",
                source_id=target.item_id, now=now, rule_id=rule["id"], push_override=po)
        elif "remind" in raised:
            self.astore.set_meta("last_reminder", f"{target.item_id}:{raised['remind']}", bump=True)
