"""Codex rotation: not built. The adapter refuses with the reason every caller shows."""
from .base import Adapter, RotationUnsupported


class CodexAdapter(Adapter):
    runtime = "codex"

    def __init__(self, *a, **k):
        raise RotationUnsupported("codex rotation is not supported yet: its adapter isn't built and tested")
