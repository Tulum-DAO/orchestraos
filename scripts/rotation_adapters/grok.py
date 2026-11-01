"""Grok rotation: not built. The adapter refuses with the reason every caller shows."""
from .base import Adapter, RotationUnsupported


class GrokAdapter(Adapter):
    runtime = "grok"

    def __init__(self, *a, **k):
        raise RotationUnsupported("grok rotation is not supported yet: its adapter isn't built and tested")
