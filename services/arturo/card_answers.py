# card_answers.py — Arturo's choice cards answered on the server.
#
# A card (ask_choices) is answered by the operator's next text turn: a tap sends the picked labels as words, or they
# type their own. The turn now says which card it answers (`card_answer` = {card_id, picks, words}), and the answer is
# kept on the card's own row in the same write as the turn, so every surface (the page, the pane, a reload, the
# drawer's History, later the apps) reads the card's answer from the server instead of guessing it from the message
# that followed.
#
# Once: the card's answer IS the turn's message, so it reaches Arturo exactly once, as the words he always got; a
# retry of the same send is replayed by its turn id before any of this runs; a second, different send answering a
# card already answered (another tab, another device) is refused and runs nothing, with the stored answer, so the
# page can show it.

import re

CARD_ID = re.compile(r"^[0-9a-f]{8,32}$")
MAX_PICKS = 8
PICK_MAX = 60
WORDS_MAX = 1000


def pick(raw):
    """(the answer rebuilt from known fields, or None; a refusal body, or None). Absent = (None, None)."""
    if raw is None:
        return None, None
    bad = {"ok": False, "error": "bad_card_answer", "field": "card_answer"}
    if not isinstance(raw, dict) or not isinstance(raw.get("card_id"), str) or not CARD_ID.match(raw["card_id"]):
        return None, bad
    picks = raw.get("picks", [])
    if not isinstance(picks, list) or len(picks) > MAX_PICKS or not all(isinstance(p, str) for p in picks):
        return None, bad
    words = raw.get("words", "")
    if not isinstance(words, str) or len(words) > WORDS_MAX:
        return None, bad
    clean = [" ".join(p.split())[:PICK_MAX] for p in picks]
    clean = [p for i, p in enumerate(clean) if p and p not in clean[:i]]
    words = words.strip()
    if not clean and not words:
        return None, bad
    return {"card_id": raw["card_id"], "picks": clean, "words": words}, None


def check(meta, answer):
    """Whether this card can take this answer: a refusal body (nothing runs), or None. `meta` = the card's stored
    meta (None when the thread holds no such card)."""
    if not isinstance(meta, dict) or meta.get("kind") != "card":
        return {"ok": False, "error": "card_unknown", "card_id": answer["card_id"]}
    if meta.get("dismissed"):
        return {"ok": False, "error": "card_dismissed", "card_id": answer["card_id"]}
    if isinstance(meta.get("answer"), dict):
        return {"ok": False, "error": "card_answered", "card_id": answer["card_id"], "answer": meta["answer"]}
    choices = meta.get("choices") or {}
    options = choices.get("options") or []
    if any(p not in options for p in answer["picks"]):
        return {"ok": False, "error": "bad_card_answer", "field": "card_answer.picks", "card_id": answer["card_id"]}
    if len(answer["picks"]) > 1 and not choices.get("multi"):
        return {"ok": False, "error": "bad_card_answer", "field": "card_answer.picks", "card_id": answer["card_id"]}
    return None


def stored(answer, now):
    """What the card's row keeps: the picks in the card's own order is the page's job; here, as sent, and when."""
    out = {"picks": list(answer["picks"]), "at": float(now)}
    if answer.get("words"):
        out["words"] = answer["words"]
    return out
