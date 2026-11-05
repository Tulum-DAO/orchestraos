"""attention_rules.py — person-owned timeout rules: a trigger, a timeframe, and follow-up actions.

There are no fixed "stuck after X" thresholds anywhere. Each person decides, with per-agent overrides:

  trigger      no_progress (an agent made no progress for X) | unanswered (a question waited X for an answer)
  after_s      the timeframe, 60 s .. 7 d (no_progress cannot fire before the detector's 10 min stall mark)
  scope        optional agent; unanswered rules may also scope kind (decision|questionnaire|permission|
               human_task) and risk (low|medium|high)
  actions      remind [repeat_every_s >= 300] | mark_stuck | expire [mode default|no_answer]
               | hand_off | choose_for_me   (the last two are registered and refused until part 2)

Which rule applies: the most specific matching rule of the same trigger (agent 4 + kind 2 + risk 1, ties to
the newest edit). A per-agent rule switched off still wins, which is how a person silences one agent.

The clock: a rule fires on a target when now >= max(observation since, rule.updated_at, rules_epoch) +
after_s. rules_epoch is when the tables were created (the release), so nothing waiting at release is
backfilled, and editing a rule restarts its clock. Each (rule, target, action) fires once; a remind with
repeat_every_s fires again when its next_at comes.

SAFETY (the operator's rule): an action that acts FOR the person (expire now; hand_off and choose_for_me in
part 2) never fires on a card whose action would be held or is risky: a permission card, a high-risk card,
a hard-to-undo card, or a protected agent's card. Part 1 has no standing permissions, so it simply refuses
those (may_act_for_person). Part 2 swaps that body for the classifier plus the standing-grant lookup; the
call site does not change. A refused action is recorded with its reason; reminding still happens.
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
PART2 = "arrives with supervision levels (part 2)"
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

def may_act_for_person(item, raw=None, is_protected=None) -> tuple[bool, str | None]:
    """May a timeout rule answer or close this card for the person? (False, why) for anything that would
    be held or is risky. `raw` is the authoritative ledger row when there is one (checked too, so a feed
    row missing a field never widens the gate)."""
    card = item.get("card") if isinstance(item.get("card"), dict) else {}
    rows = [r for r in (item, card, raw or {}) if r]
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
    return True, None


# ---- actions -------------------------------------------------------------------------------------

class TimeoutAction(Protocol):
    name: str
    acts_for_person: bool

    def available(self) -> tuple[bool, str]: ...
    def validate(self, params: dict) -> dict: ...
    def fire(self, target, rule, params, ctx) -> dict: ...


class Remind:
    name, acts_for_person = "remind", False

    def available(self):
        return True, ""

    def validate(self, params):
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

    def validate(self, params):
        return {}

    def fire(self, target, rule, params, ctx):
        return {"ok": True, "detail": "marked stuck"}               # the engine opens the quiet row


class Expire:
    name, acts_for_person = "expire", True

    def available(self):
        return True, ""

    def validate(self, params):
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
            ok, why = may_act_for_person(item, None, ctx.is_protected)
            if not ok:
                return {"ok": False, "detail": f"safety:{why}"}
            if not ctx.qstore.discard(iid):
                return {"ok": False, "detail": "not_pending"}
            ctx.qnr_deliver_discarded(ctx.qstore.get(iid), ctx.qstore)
            return {"ok": True, "detail": "discarded", "chosen": "no answer"}
        raw = ctx.ledger.get(iid) if iid.startswith("apr_") else None
        ok, why = may_act_for_person(item, raw, ctx.is_protected)
        if not ok:
            return {"ok": False, "detail": f"safety:{why}"}
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


class _Part2:
    acts_for_person = True

    def __init__(self, name):
        self.name = name

    def available(self):
        return False, PART2

    def validate(self, params):
        return dict(params)

    def fire(self, target, rule, params, ctx):
        return {"ok": False, "detail": "not_available"}


ACTIONS: dict[str, TimeoutAction] = {"remind": Remind(), "mark_stuck": MarkStuck(), "expire": Expire(),
                                     "hand_off": _Part2("hand_off"), "choose_for_me": _Part2("choose_for_me")}


def available_actions():
    out = []
    for name, a in ACTIONS.items():
        ok, why = a.available()
        out.append({"type": name, "available": ok, "why": why or None})
    return out


# ---- rule store -----------------------------------------------------------------------------------

def validate_rule(r: dict) -> dict:
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
        params = impl.validate({k: v for k, v in a.items() if k != "type"})
        if a["type"] == "expire":
            if trig != "unanswered":
                raise RuleInvalid("invalid", "expire closes a question: it needs the unanswered trigger")
            if sk == "permission" or sr == "high":
                raise RuleInvalid("unsafe_scope", "expire never acts on permission or high-risk cards")
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
                           "scope_kind": scope_kind, "scope_risk": scope_risk})
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
        r = validate_rule(merged)
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
                res = impl.fire(target, rule, params, self.ctx) if ok else {"ok": False, "detail": "not_available"}
                if res.get("ok") and a["type"] in ("remind", "mark_stuck"):
                    raised[a["type"]] = n
                if res.get("ok") and impl.acts_for_person:
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
