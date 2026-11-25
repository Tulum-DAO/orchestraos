"""Arturo's "spending" tool group: the spending tracker's agent API (/home/shaw/apps/spending-tracker, README "Agent API
+ MCP") as NATIVE tools on both of Arturo's paths (the relay proxy's TOOLS mechanism and the native GPT-Live boundary).
Arturo's Claude brain runs with --strict-mcp-config and no MCP servers, so the tracker's mcp_server.py is not the path.

One file, the same in both trees (services/arturo/spending_tools.py). What it guarantees, each with a test:
  G1  GENERATED: the group is built from GET <base>/api/agent/tools.json (no auth, no data), never hard-coded, so a tool
      the tracker adds appears without a code change. Names are namespaced spending_<name>, and every description says
      so. Nothing is fetched until arm(): a tracker that is down at startup leaves the group EMPTY (logged once), and
      it is retried lazily, in the background, with a backoff - Arturo's startup never waits on or crashes for it.
  G2  TOKEN BY PATH AT CALL TIME: the bearer is read from the token file on EVERY call (ARTURO_SPENDING_TOKEN_FILE,
      default the tracker's data/agent-token), never cached, never logged, never put in a prompt or a result.
      X-Agent-Source: arturo. Base URL: ARTURO_SPENDING_URL (default http://127.0.0.1:8760). Short timeouts.
  G3  VOICE RESULTS: one speakable sentence + the key numbers; the raw JSON only for lookups, bounded (RAW_MAX).
  G4  NEVER AUTO-CONFIRM: confirm / allow_duplicate (any boolean confirm*/allow_*/force* argument) are STRIPPED from
      what the model sends unless the tracker's own guard asked for them on an EARLIER caller turn of the SAME call
      (scope) for the SAME arguments, within CONFIRM_TTL_S, and the caller's words this turn say yes. A guard answer
      (duplicate:true, or the >$5,000 needs-confirm refusal) comes back as a QUESTION for the caller, never an action.
  G5  READ vs WRITE: a tool is a read only if it is one of KNOWN_READS (or the tracker marks it read-only) and takes
      no guard argument; anything else, including a tool the tracker adds later, is a WRITE (fail closed).
  G6  NO MONEY IN LOGS: amounts, merchants, notes and dates never reach a log or the native ledger. Logs carry only the
      tool name, read/write class, outcome (ok / duplicate-asked / confirm-asked / error:<code>) and the argument KEYS
      (log_line / log_args / log_result / ledger_args / ledger_result); each path routes its own generic tool logging
      for spending_* names through these.
  G7  TRACKER QUIRKS (docs/MCP.md): a duplicate is 200 ok:true with NOTHING saved, so it is said as "already logged"
      and asked about, never as saved; the 30/min write limit counts refused attempts and undos, so a 429 is said
      plainly and never retried; month_summary / search_transactions include pending bank rows unflagged, so their
      sentence says the figures may include pending charges.
"""
import copy
import hashlib
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request

log = logging.getLogger("arturo.spending")

PREFIX = "spending_"
ENABLE_ENV = "ARTURO_SPENDING_TOOLS"                 # "0" = no spending group at all
URL_ENV = "ARTURO_SPENDING_URL"
TOKEN_FILE_ENV = "ARTURO_SPENDING_TOKEN_FILE"
DEFAULT_URL = "http://127.0.0.1:8760"
DEFAULT_TOKEN_FILE = "/home/shaw/apps/spending-tracker/data/agent-token"
SOURCE = "arturo"
SCHEMA_TIMEOUT_S = 2.0
CALL_TIMEOUT_S = 5.0
RETRY_S = (10.0, 30.0, 120.0, 300.0)                 # lazy retry backoff while the tracker is down
REFRESH_S = 600.0                                    # re-read tools.json this often once loaded (new tools appear)
RAW_MAX = 1200                                       # bound on the raw JSON handed to the model
CONFIRM_TTL_S = 180.0
KNOWN_READS = frozenset({"list_upcoming", "month_summary", "search_transactions", "list_options"})
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")     # the tracker's name goes into a URL path
_GUARD_RE = re.compile(r"^(confirm|allow_|force)", re.I)
_YES_RE = re.compile(r"\b(yes|yeah|yep|yup|sure|correct|confirm(?:ed)?|go ahead|do it|save it|that'?s right|"
                     r"ok(?:ay)?|absolutely|definitely|please do|s[ií])\b", re.I)
_NO_START_RE = re.compile(r"^\W*(no|nope|nah|don'?t|do not|never|cancel|stop|wait|hold on)\b", re.I)
_NO_ANY_RE = re.compile(r"\b(not right|not correct|wrong|don'?t|do not|cancel|never ?mind)\b", re.I)
_MONEY_RE = re.compile(r"\$[\d,]+(?:\.\d+)?")


def enabled():
    return os.environ.get(ENABLE_ENV, "1") != "0"


def base_url():
    return (os.environ.get(URL_ENV) or DEFAULT_URL).rstrip("/")


def token_file():
    return os.environ.get(TOKEN_FILE_ENV) or DEFAULT_TOKEN_FILE


def _read_token():
    """Read at call time, every call (rotation takes effect on the next call). Never logged."""
    try:
        with open(token_file(), encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def _sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _money(v):
    try:
        return f"${abs(float(v)):,.2f}"
    except (TypeError, ValueError):
        return str(v)


def said_yes(words):
    """The caller's own words this turn read as a yes (and do not open with a no)."""
    w = (words or "").strip()
    return bool(w) and bool(_YES_RE.search(w)) and not _NO_START_RE.match(w) and not _NO_ANY_RE.search(w)


class SpendingTools:
    def __init__(self, clock=time.time, schema_timeout=SCHEMA_TIMEOUT_S, call_timeout=CALL_TIMEOUT_S,
                 retry_s=RETRY_S, refresh_s=REFRESH_S, confirm_ttl_s=CONFIRM_TTL_S):
        self.clock = clock
        self.schema_timeout, self.call_timeout = schema_timeout, call_timeout
        self.retry_s, self.refresh_s, self.confirm_ttl_s = tuple(retry_s), refresh_s, confirm_ttl_s
        self.armed = False
        self._lock = threading.Lock()
        self._tools = {}                 # spending_<name> -> {"remote", "description", "parameters", "read", "flags"}
        self._loaded_at = None
        self._next_try = 0.0
        self._failures = 0
        self._down_logged = False
        self._refreshing = False
        self._pending = {}               # (scope, remote, args_sha) -> {"flags", "turn", "exp", "guard"}
        self.trace = []                  # (name, decision) for tests/diagnostics; never args or the token

    # ---- generation (G1) ------------------------------------------------------------------------------------
    def arm(self, background=True):
        """Start using the tracker. background=True never blocks the caller (Arturo's startup)."""
        if not enabled():
            log.info("spending tools off (%s=0)", ENABLE_ENV)
            return self
        self.armed = True
        if background:
            self._kick()
        else:
            self.refresh()
        return self

    def _due(self):
        now = self.clock()
        if now < self._next_try:         # backing off after a failed try (loaded or not)
            return False
        return self._loaded_at is None or now - self._loaded_at >= self.refresh_s

    def _kick(self):
        with self._lock:
            if self._refreshing:
                return
            self._refreshing = True
        threading.Thread(target=self._refresh_bg, name="spending-tools-refresh", daemon=True).start()

    def _refresh_bg(self):
        try:
            self.refresh()
        except Exception as e:  # noqa: BLE001 - a refresh never takes a turn down
            log.warning("spending tools refresh failed: %s", type(e).__name__)
        finally:
            with self._lock:
                self._refreshing = False

    def refresh(self):
        """Fetch tools.json and rebuild the group. True on success. On failure the group keeps what it had (empty at
        startup) and the next try is scheduled with a backoff; the first failure is logged once."""
        url = base_url() + "/api/agent/tools.json"
        try:
            req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=self.schema_timeout) as r:
                raw = json.loads(r.read() or b"[]")
            built = self._build(raw)
        except (urllib.error.URLError, OSError, ValueError, TypeError) as e:
            with self._lock:
                self._next_try = self.clock() + self.retry_s[min(self._failures, len(self.retry_s) - 1)]
                self._failures += 1
                first = not self._down_logged
                self._down_logged = True
            if first:
                log.warning("spending tracker tools unavailable (%s at %s): the spending group is %s; retrying lazily",
                            type(e).__name__, base_url(), "kept as loaded" if self._tools else "off for now")
            return False
        with self._lock:
            was = set(self._tools)
            self._tools, self._loaded_at = built, self.clock()
            self._failures, self._down_logged = 0, False
        if set(built) != was:
            log.info("spending tools loaded from tracker: %s", sorted(built))
        return True

    @staticmethod
    def _build(raw):
        if not isinstance(raw, list):
            raise ValueError("tools.json is not a list")
        out = {}
        for t in raw:
            fn = (t or {}).get("function") if isinstance(t, dict) else None
            if not isinstance(fn, dict):
                continue
            remote = fn.get("name")
            if not isinstance(remote, str) or not _NAME_RE.match(remote):
                continue
            params = fn.get("parameters") if isinstance(fn.get("parameters"), dict) else {"type": "object",
                                                                                             "properties": {}}
            params = copy.deepcopy(params)
            props = params.get("properties") if isinstance(params.get("properties"), dict) else {}
            flags = frozenset(k for k, v in props.items()
                              if _GUARD_RE.match(k) and isinstance(v, dict) and v.get("type") == "boolean")
            marked_ro = fn.get("x-read-only") is True or (fn.get("annotations") or {}).get("readOnlyHint") is True
            read = (remote in KNOWN_READS or marked_ro) and not flags
            for f in flags:
                props[f] = dict(props[f], description=(
                    "Only after the caller said yes to the question this tool asked on an earlier turn; set any other "
                    "time it is ignored. " + str(props[f].get("description") or "")).strip())
            kind = ("READ-ONLY lookup: changes nothing." if read else
                    "CHANGES the spending tracker. If the result asks a question, ask the caller and wait for "
                    "their answer; never confirm on their behalf.")
            desc = (f"[spending group: the spending tracker's '{remote}' tool, namespaced as {PREFIX}{remote} so it "
                    f"never collides with Arturo's other tools. {kind}] {str(fn.get('description') or '')[:900]}")
            out[PREFIX + remote] = {"remote": remote, "description": desc, "parameters": params, "read": read,
                                    "flags": flags}
        return out

    # ---- what a path declares ---------------------------------------------------------------------------------
    def _snapshot(self):
        if self.armed and self._due():
            self._kick()                 # lazy retry / refresh in the background: this turn never waits on it
        with self._lock:
            return dict(self._tools)

    def owns(self, name):
        return isinstance(name, str) and name.startswith(PREFIX)

    def names(self):
        return set(self._snapshot())

    def is_read(self, name):
        t = self._tools.get(name)
        return bool(t and t["read"])

    def is_write(self, name):
        return self.owns(name) and not self.is_read(name)

    def specs(self, reads_only=False):
        """{name: (description, parameters)} (the native boundary's shape)."""
        return {n: (t["description"], copy.deepcopy(t["parameters"])) for n, t in sorted(self._snapshot().items())
                if t["read"] or not reads_only}

    def openai_tools(self, reads_only=False):
        """OpenAI function-calling entries (the relay proxy's TOOLS shape)."""
        return [{"type": "function", "function": {"name": n, "description": d, "parameters": p}}
                for n, (d, p) in self.specs(reads_only).items()]

    # ---- one call (G2-G4) ---------------------------------------------------------------------------------------
    def call(self, name, args, scope=None, turn_key=None, caller_words=None):
        """Run one spending tool. scope = the call/conversation (+ principal) a confirmation is bound to; turn_key =
        the caller's current turn (a confirmation is honoured only on a LATER turn than the one that asked);
        caller_words = the caller's own words this turn (must say yes). Returns a compact dict, never raises."""
        t = self._tools.get(name)
        if t is None:
            self.trace.append((name, "unknown"))
            return self._out(False, name, "The spending tracker doesn't have that tool right now.", error="UNKNOWN_TOOL")
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return self._out(False, name, "Those arguments weren't understood.", error="BAD_ARGUMENTS")
        flags = t["flags"]
        base = {k: v for k, v in args.items() if k not in flags}
        asked = {k for k in flags if args.get(k) is True}
        key = (scope, t["remote"], _sha(base))
        granted = set()
        if asked:
            granted = self._redeem(key, asked, turn_key, caller_words)
            if asked - granted:
                self.trace.append((name, "stripped:" + ",".join(sorted(asked - granted))))
        body = dict(base, **{f: True for f in granted})
        status, data = self._post(t["remote"], body)
        out = self._result(name, t, key, granted, status, data, turn_key)
        self.trace.append((name, "guard:" + out["guard"] if out.get("needs_answer") else
                           ("ok" if out["ok"] else out.get("error", "failed"))))
        log.info(self.log_line(name, args, out))
        return self._scrub(out)

    # ---- what may be logged / stored (G6) --------------------------------------------------------------------
    def kind(self, name):
        return "read" if self.is_read(name) else "write"

    def log_args(self, name, args):
        keys = sorted(args) if isinstance(args, dict) else []
        return f"[spending {name} {self.kind(name)}] arg_keys={keys}"

    def log_line(self, name, args, out):
        return f"{self.log_args(name, args)} outcome={outcome(out)}"

    def log_result(self, name, text):
        return f"[spending {name} {self.kind(name)}] outcome={text_outcome(text)}"

    @staticmethod
    def ledger_args(args):
        return {"arg_keys": sorted(args) if isinstance(args, dict) else []}

    @staticmethod
    def ledger_result(out):
        r = {k: out[k] for k in ("ok", "tool", "needs_answer", "guard", "wrote", "error") if k in out}
        r["outcome"] = outcome(out)
        return r

    def _redeem(self, key, asked, turn_key, caller_words):
        scope = key[0]
        with self._lock:
            now = self.clock()
            for k in [k for k, p in self._pending.items() if p["exp"] <= now]:
                del self._pending[k]
            p = self._pending.get(key)
            if (scope is None or p is None or turn_key is None or p["turn"] == turn_key
                    or not said_yes(caller_words)):
                return set()
            del self._pending[key]       # single use
            return set(asked) & p["flags"]

    def _issue(self, key, flags, turn_key):
        if key[0] is None:
            return
        with self._lock:
            self._pending[key] = {"flags": frozenset(flags), "turn": turn_key, "exp": self.clock() + self.confirm_ttl_s}

    def _post(self, remote, body):
        tok = _read_token()
        if not tok:
            return -1, {"error": "no access key"}
        req = urllib.request.Request(base_url() + "/api/agent/tools/" + remote, method="POST",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok,
                                              "X-Agent-Source": SOURCE})
        del tok
        try:
            with urllib.request.urlopen(req, timeout=self.call_timeout) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except ValueError:
                return e.code, {"error": str(e.reason)}
        except (urllib.error.URLError, OSError, ValueError) as e:
            return 0, {"error": type(e).__name__}

    def _result(self, name, t, key, granted, status, data, turn_key):
        if not isinstance(data, (dict, list)):
            data = {"result": data}
        err = str(data.get("error") or "") if isinstance(data, dict) else ""
        if status == -1:
            return self._out(False, name, "I can't use the spending tracker: its access key isn't readable.",
                             error="NO_TOKEN")
        if status == 0:
            return self._out(False, name, "I couldn't reach the spending tracker just now.", error="TRACKER_DOWN")
        if status == 401:
            return self._out(False, name, "The spending tracker refused Arturo's access key.", error="UNAUTHORIZED")
        if status == 429:                # attempts (even refused ones and undos) count: never retried here
            return self._out(False, name, "Too many changes in a minute; try again shortly.", error="RATE_LIMITED")
        if status == 400 and "confirm" in t["flags"] and re.search(r"confirm\s*=\s*true|unusually large", err, re.I):
            self._issue(key, set(granted) | {"confirm"}, turn_key)
            amt = _MONEY_RE.search(err)
            q = (f"That's {amt.group(0) if amt else 'a large amount'}, which is unusually large. "
                 f"Did I hear that right, should I save it?")
            return self._question(name, "confirm", q, {"amount": amt.group(0) if amt else None})
        if status != 200:
            return self._out(False, name, f"The tracker said: {err[:200] or f'error {status}'}", error=f"HTTP_{status}")
        if isinstance(data, dict) and data.get("duplicate") is True and "allow_duplicate" in t["flags"]:
            self._issue(key, set(granted) | {"allow_duplicate"}, turn_key)
            tx = data.get("transaction") or {}
            what = " ".join(x for x in (_money(tx.get("amount")) if tx.get("amount") is not None else "",
                                        f"at {tx['description']}" if tx.get("description") else "",
                                        f"on {tx['date']}" if tx.get("date") else "") if x)
            q = (f"That looks already logged: {what or 'the same transaction'} went in a few minutes ago, so nothing "
                 f"new was added. Should I log it again anyway?")
            return self._question(name, "duplicate", q, {"amount": tx.get("amount"), "existing_id": tx.get("id")})
        if isinstance(data, dict) and data.get("ok") is False:
            return self._out(False, name, str(data.get("message") or data.get("error") or "Nothing was changed.")[:200],
                             error="NOT_DONE")
        say, nums = summarize(t["remote"], data)
        out = self._out(True, name, say, numbers=nums)
        if t["read"]:
            out["data"] = _bounded(data)
        else:
            out["wrote"] = True
        return out

    @staticmethod
    def _out(ok, name, say, error=None, numbers=None):
        o = {"ok": bool(ok), "tool": name, "say": say}
        if numbers:
            o["numbers"] = numbers
        if error:
            o["error"] = error
        return o

    @staticmethod
    def _question(name, guard, q, numbers):
        return {"ok": False, "tool": name, "needs_answer": True, "guard": guard, "question": q, "say": q,
                "numbers": {k: v for k, v in numbers.items() if v is not None},
                "next": "Ask the caller this question and stop. Call again only after they say yes, on their next turn."}

    @staticmethod
    def _scrub(out):
        tok = _read_token()
        if not tok:
            return out
        s = json.dumps(out)
        return json.loads(s.replace(tok, "[redacted]")) if tok in s else out


def outcome(out):
    if out.get("needs_answer"):
        return f"{out.get('guard')}-asked"
    return "ok" if out.get("ok") else f"error:{out.get('error') or 'failed'}"


def text_outcome(text):
    """The outcome of a relay-rendered result (render_text), for a log line that must not carry its numbers."""
    t = str(text or "")
    if t.startswith("ASK FIRST:"):
        return "duplicate-asked" if "already logged" in t else "confirm-asked"
    if t.startswith(("ERROR", "error")):
        return "error"
    if t.startswith("NOT RUN"):
        return "refused"
    return "ok"


PENDING_NOTE = " These figures may include pending charges."


def _bounded(data):
    s = json.dumps(data, separators=(",", ":"), default=str)
    return s if len(s) <= RAW_MAX else s[:RAW_MAX] + "...(truncated)"


def summarize(remote, d):
    """(one speakable sentence, key numbers) for a tracker result. Unknown tools use the tracker's message."""
    if not isinstance(d, dict):
        return "Done.", {}
    if remote == "list_upcoming":
        items = d.get("items") or []
        due = [i for i in items if not i.get("paid")]
        overdue = [i for i in due if i.get("overdue")]
        nums = {"days": d.get("days"), "items": len(items), "unpaid": len(due), "overdue": len(overdue),
                "unpaid_bills_total": d.get("unpaid_bills_total"), "expected_income_total": d.get("expected_income_total")}
        nxt = sorted(due, key=lambda i: str(i.get("date")))[:1]
        tail = (f" Next up: {nxt[0].get('name')} {_money(nxt[0].get('amount'))} on {nxt[0].get('date')}." if nxt else "")
        return (f"In the next {d.get('days')} days you have {len(due)} unpaid item{'s' if len(due) != 1 else ''}"
                f"{f', {len(overdue)} overdue' if overdue else ''}: {_money(d.get('unpaid_bills_total') or 0)} in bills "
                f"and {_money(d.get('expected_income_total') or 0)} expected in.{tail}"), nums
    if remote == "month_summary":
        cats = d.get("by_category") or []
        top = cats[0] if cats and isinstance(cats[0], dict) else None
        nums = {k: d.get(k) for k in ("month", "income", "spending", "net", "current_balance",
                                       "projected_end_of_month_balance") if d.get(k) is not None}
        net = d.get("net") or 0
        tail = ""
        if top:
            tn = top.get("name") or top.get("category")
            ta = top.get("amount", top.get("total"))
            if tn is not None and ta is not None:
                tail = f" Biggest category: {tn} at {_money(ta)}."
        return (f"For {d.get('month')} you've spent {_money(d.get('spending') or 0)} and taken in "
                f"{_money(d.get('income') or 0)}, net {'+' if net >= 0 else '-'}{_money(net)}.{tail}{PENDING_NOTE}"), nums
    if remote == "search_transactions":
        tx = d.get("transactions") or []
        nums = {"count": d.get("count", len(tx)), "total": d.get("total"), "more": bool(d.get("more"))}
        lead = "; ".join(f"{_money(t.get('amount'))} {t.get('description') or ''} on {t.get('date')}".replace("  ", " ")
                         for t in tx[:3])
        return (f"Found {nums['count']} transaction{'s' if nums['count'] != 1 else ''}"
                f"{' (more not shown)' if nums['more'] else ''}, totalling {_money(d.get('total') or 0)}."
                f"{' Latest: ' + lead + '.' if lead else ''}{PENDING_NOTE}"), nums
    if remote == "list_options":
        nums = {"categories": len(d.get("categories") or []), "accounts": len(d.get("accounts") or []),
                "recurring": len(d.get("recurring") or [])}
        return (f"The tracker has {nums['categories']} categories, {nums['accounts']} accounts and {nums['recurring']} "
                f"recurring items."), nums
    nums = {k: d.get(k) for k in ("action_id", "undone_action_id", "transaction_id") if d.get(k) is not None}
    tx = d.get("transaction") if isinstance(d.get("transaction"), dict) else None
    if tx and tx.get("amount") is not None:
        nums["amount"] = tx.get("amount")
    msg = str(d.get("message") or "").strip()
    return (msg[:240] + ("" if msg.endswith(".") else ".")) if msg else "Done.", nums


def render_text(out):
    """The relay's string form of a result (its tool results are text): failure prefixes match _tool_ok's."""
    if out.get("needs_answer"):
        return f"ASK FIRST: {out['question']} (Ask the caller and stop; call again only after they say yes.)"
    if not out.get("ok"):
        return f"ERROR: spending tracker: {out.get('say')}"
    s = out.get("say") or "Done."
    if out.get("numbers"):
        s += " Numbers: " + json.dumps(out["numbers"], separators=(",", ":"), default=str)
    if out.get("data"):
        s += " Data: " + out["data"]
    return s
