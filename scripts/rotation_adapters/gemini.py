"""Gemini rotation: not built. The adapter refuses with the reason every caller shows."""
from .base import Adapter, RotationUnsupported


class GeminiAdapter(Adapter):
    runtime = "gemini"

    def __init__(self, *a, **k):
        raise RotationUnsupported("gemini rotation is not supported yet: its adapter isn't built and tested")
