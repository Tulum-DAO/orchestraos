"""Which engine answers a Live voice call, from the server-wide voice provider alone (state/voice-vendor.json).

The realtime model goes with the provider; nothing per agent, and a key on its own never picks an engine:
  - openai      -> "relay" (GPT-Live over the stream relay), when the relay runs and the provider is usable;
  - hume, elevenlabs -> None: neither has a live engine yet, said plainly;
  - gemini      -> "gemini" only if a provider value of that name is ever added (it is not one today).
"""

NOT_YET = "Live calls aren't available with this voice provider yet"


def choose(vendor, relay_up: bool, unavailable_reason=None, gemini_key: bool = False):
    """(engine, reason): engine is "relay", "gemini" or None; reason says why there is none (else None)."""
    v = str(vendor or "").strip().lower()
    if v == "openai":
        if not relay_up:
            return None, "The voice relay isn't running, so live calls aren't available right now"
        why = unavailable_reason("openai") if callable(unavailable_reason) else None
        return (None, why) if why else ("relay", None)
    if v == "gemini":
        return ("gemini", None) if gemini_key else (None, "Live calls need a Gemini key for this voice provider")
    return None, NOT_YET
