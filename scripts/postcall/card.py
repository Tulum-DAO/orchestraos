"""The review card: one questionnaire per call (design §4)."""


def build_questionnaire(call, items):
    if not items:
        raise ValueError("refusing to build a post-call card with no items")
    return {
        "title": f"{call.title}: {len(items)} items",
        "questions": [{"prompt": it.quote, "kind": "menu"} for it in items],
    }
