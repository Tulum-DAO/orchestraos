"""Shared helpers for the Jev suite (fake decide functions and answer builders)."""


def answers_decide(answers, calls=None, model="jev-test"):
    """A decide() that returns fixed answers and records each (state, questions)."""
    def decide(state, questions):
        if calls is not None:
            calls.append((state, questions))
        return {"model": model, "answers": answers, "usage": {"input_tokens": 10, "output_tokens": 2}}
    return decide


def noul_a(p):
    return {"type": "noul", "noul": p}


def choice_a(pick, conf, options=None):
    probs = {o: (conf if o == pick else round((1 - conf) / max(1, len(options) - 1), 4)) for o in options} if options else {pick: conf}
    return {"type": "choice", "choice": pick, "confidence": conf, "probabilities": probs}


def score_a(s, levels=3):
    return {"type": "score", "score": s, "confidence": 0.8, "legend": {str(i): f"l{i}" for i in range(levels)},
            "probabilities": {str(i): 1 / levels for i in range(levels)}}
