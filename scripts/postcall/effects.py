"""External writes go through one interface (design §7): live, shadow or test.

An adapter is the outside world as the ledger sees it: send(effect) performs the write and returns
an external id; find(effect) looks the write up by its deterministic effect id (any status), so
recovery can tell "landed but unrecorded" from "never landed"."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Effect:
    effect_id: str
    kind: str
    payload: dict = field(default_factory=dict, compare=False)


class Recorder:
    """Test adapter: an in-memory outside world. Touches nothing outside the process."""

    def __init__(self):
        self.sent = []

    def send(self, effect):
        self.sent.append(effect)
        return f"ext-{len(self.sent)}"

    def find(self, effect):
        for n, e in enumerate(self.sent, 1):
            if e.effect_id == effect.effect_id:
                return f"ext-{n}"
        return None
