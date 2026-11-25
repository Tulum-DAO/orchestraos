"""External writes go through one interface (design §7): live, shadow or test."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Effect:
    effect_id: str
    kind: str
    payload: dict = field(default_factory=dict, compare=False)


class Recorder:
    """Test adapter: records planned effects, touches nothing outside the process."""

    def __init__(self):
        self.planned = []

    def plan(self, effect):
        self.planned.append(effect)
        return effect
