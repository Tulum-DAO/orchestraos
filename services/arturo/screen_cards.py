# screen_cards.py — the cards on the operator's screen, as the dashboard reports them with each text turn
# (contract/notes/arturo-screen-context.md): read into a small, bounded record, and written out as the block the
# brain reads for THIS turn only (it rides in the turn's context, never in the stored message).
#
# The dashboard sends `context.cards` = {v, focus, cards[]}: the card in focus in full (kind, who it is from, its
# state, the question, the options, and for a multi-part card the part on show with what is picked or typed), every
# other card as one line. Each card has a short ref (c1, c2…) that only tools use: the brain never says it, and no
# card id ever reaches it.
#
# pick_cards mirrors api/src/routes/arturo.ts pickCards; fixture shared with its test:
# fixtures/screen_cards_cases.json.

import re

_REF = re.compile(r"^c\d{1,2}$")
_MAX_CARDS = 12
_MAX_OPTIONS = 12
_WS = re.compile(r"\s+")


def _clip(v, n):
    if not isinstance(v, str):
        return None
    t = _WS.sub(" ", v).strip()
    if not t:
        return None
    return f"{t[:n - 1]}…" if len(t) > n else t


def _clip_list(v, n):
    if not isinstance(v, list):
        return None
    out = [x for x in (_clip(i, n) for i in v) if x][:_MAX_OPTIONS]
    return out or None


def _small_int(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 50 else None


def _put(o, k, v):
    if v is not None:
        o[k] = v


def pick_cards(raw):
    """The record rebuilt from known fields only, every string clipped and every list capped; None when `raw` is
    not a cards record at all."""
    if not isinstance(raw, dict) or not isinstance(raw.get("cards"), list):
        return None
    cards = []
    for c in raw["cards"][:_MAX_CARDS]:
        if not isinstance(c, dict):
            continue
        ref = c.get("ref")
        if not isinstance(ref, str) or not _REF.match(ref) or any(x["ref"] == ref for x in cards):
            continue
        line = _clip(c.get("line"), 200)
        if line:
            cards.append({"ref": ref, "line": line})
            continue
        out = {"ref": ref}
        for k, n in (("kind", 30), ("from", 60), ("state", 30), ("surface", 30), ("question", 300), ("summary", 400)):
            _put(out, k, _clip(c.get(k), n))
        _put(out, "options", _clip_list(c.get("options"), 100))
        _put(out, "answer", _clip(c.get("answer"), 300))
        p = c.get("part")
        if isinstance(p, dict):
            part = {}
            _put(part, "n", _small_int(p.get("n")))
            _put(part, "of", _small_int(p.get("of")))
            _put(part, "question", _clip(p.get("question"), 300))
            _put(part, "select", p.get("select") if p.get("select") in ("single", "multi") else None)
            _put(part, "options", _clip_list(p.get("options"), 100))
            _put(part, "picked", _clip_list(p.get("picked"), 100))
            _put(part, "typed", _clip(p.get("typed"), 300))
            if part:
                out["part"] = part
        if out.get("question") or out.get("part"):
            cards.append(out)
    focus = raw.get("focus")
    focus = focus if isinstance(focus, str) and any(x["ref"] == focus for x in cards) else None
    return {"v": 1, "focus": focus, "cards": cards}


def refs(cards):
    """The refs this turn's screen holds (the only cards a tool may name)."""
    return [c["ref"] for c in (cards or {}).get("cards") or []]


_KIND = {"decision": "Decision", "approval": "Approval", "multipart": "Multi-part question",
         "questionnaire": "Questionnaire", "menu": "Menu", "permission": "Permission request", "choice": "Choice card"}
_SURFACE = {"popover": "opened from a reference", "approvals": "on the Approvals page", "history": "in the drawer's History",
            "chat": "in the chat", "inbox": "in the Inbox", "questionnaires": "on the Questionnaires page"}


def _full_line(c, focused):
    kind = _KIND.get(c.get("kind") or "", "Card")
    bits = [f"{kind}{' from ' + c['from'] if c.get('from') else ''}"]
    if c.get("state"):
        bits.append(c["state"])
    if c.get("surface"):
        bits.append(_SURFACE.get(c["surface"], c["surface"]))
    head = f"- {c['ref']}{' [IN FOCUS]' if focused else ''} " + ", ".join(bits) + "."
    lines = [head]
    if c.get("question"):
        lines.append(f'  Question: "{c["question"]}"')
    if c.get("summary"):
        lines.append(f"  Says: {c['summary']}")
    p = c.get("part")
    if p:
        where = f"Part {p['n']} of {p['of']}" if p.get("n") and p.get("of") else "The part on show"
        how = " (pick any)" if p.get("select") == "multi" else " (pick one)" if p.get("select") == "single" else ""
        lines.append(f'  {where}, the CURRENT question: "{p.get("question", "")}"{how}')
        if p.get("options"):
            lines.append("  Its options: " + "; ".join(p["options"]))
        picked = "; ".join(p.get("picked") or [])
        typed = p.get("typed")
        lines.append("  Picked so far on this part: " + (picked or "nothing") + (f"; typed: {typed}" if typed else ""))
    elif c.get("options"):
        lines.append("  Options: " + "; ".join(c["options"]))
    if c.get("answer"):
        lines.append(f"  Answer: {c['answer']}")
    return "\n".join(lines)


def _brief_question(c):
    """A card's question in a few words, for naming it: the full card's question, or the end of its one line."""
    q = c.get("question") or (c.get("line") or "").split(": ", 1)[-1]
    q = re.sub(r"\s*\((?:part|answered)[^)]*\)\s*$", "", q).strip()
    return q if len(q) <= 60 else q[:59].rstrip() + "…"


def cards_block(cards):
    """The brain's view of the screen for this turn, or "" when no card is on it."""
    items = (cards or {}).get("cards") or []
    if not items:
        return ""
    focus = cards.get("focus")
    out = ["CARDS ON THE OPERATOR'S SCREEN NOW (refs like c1 are for your tools only: never say a ref or any id):"]
    ordered = sorted(items, key=lambda c: c["ref"] != focus)
    for c in ordered:
        out.append(f"- {c['ref']} {c['line']}" if "line" in c else _full_line(c, c["ref"] == focus))
    if focus:
        out.append("The card marked IN FOCUS is the one the operator is looking at: \"this\", \"what is this?\", "
                   "\"what should I pick?\" or \"answer B\" are about it and its CURRENT question.")
    elif len(items) > 1:
        # Seen live: told only to ask "if unclear", the brain described both cards and offered help with either.
        names = " or ".join(f'the "{_brief_question(c)}" card' for c in ordered[:3])
        out.append("No card is in focus and more than one is on screen. Unless the conversation already says which "
                   "card they mean, do not describe the cards, guess or offer help with each: reply with only the "
                   f"question of which one, naming each by its question, like this: {names[0].upper() + names[1:]}? "
                   "Then wait for their answer.")
    else:
        out.append("It is the only card on screen: \"this\" means it.")
    out.append("Describe a card in words (its question, who asked, its state, the options or the answer). To answer "
               "one for the operator, only when they ask you to, call answer_screen_card with its ref: the card on their "
               "screen sends it the same way as their own tap and shows them the result.")
    return "\n".join(out)


def answer_for(turn, fleet, card, picks=None, words=None):
    """The answer_screen_card tool: checks the card is on this turn's screen and keeps the answer on the turn
    (`answer_card`, sent back with the reply), or says why not. Nothing is answered here: the page's own card sends
    it through that card's route, exactly as a tap would, with the same send states. Dashboard turns only."""
    if turn is None or turn.get("principal") != "fleet" or not fleet:
        return "NOT SENT: answering a card on screen works only from the dashboard. Tell them where to answer it."
    if turn.get("answer_card") is not None:
        return "NOT SENT: one card per turn, and this turn already answered one."
    ref = str(card or "").strip()
    if ref not in (turn.get("screen_refs") or []):
        return "NOT SENT: no such card is on their screen now. Ask which card they mean, by its question."
    clean = []
    for x in (picks if isinstance(picks, list) else []):
        t = " ".join(str(x or "").split())[:100]
        if t and t not in clean:
            clean.append(t)
    text = " ".join(str(words or "").split())[:1000]
    if not clean and not text:
        return "NOT SENT: say which option to pick, or the words to send."
    turn["answer_card"] = {"card": ref, "picks": clean[:12], **({"words": text} if text else {})}
    return ("HANDED TO THE CARD: it sends this the same way as their tap and shows them how it went. Say in one short "
            "line what you answered, by the card's question, never its ref.")
