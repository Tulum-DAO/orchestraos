"""Spend policy for one native call, enforced on the server (ported from the POC's testdrive/spend.py; the scratch
day-ledger is replaced by the LIVE voice daily cap, gm msg_0aec5c71: "counts into the existing voice daily cap").

Caps: per-call minutes AND dollars (SESSION_MAX_USD, stated on the restart card), the voice daily cap for vendor
"openai" (voice_usage.UsageStore: minutes per vendor per day, VOICE_DAILY_CAP_MIN), and an idle hang-up.
The hosted-model rule stands: a completed backend response WITHOUT usage, or from an unpriced model, stops the call;
a call cannot even start while the configured backend model has no price.
"""
import os
import time

from .cost import BACKEND_PRICES, LIVE_USD_PER_S

VENDOR = "openai"
SESSION_MAX_S = float(os.environ.get("ARTURO_NATIVE_SESSION_MAX_S", "600"))
SESSION_MAX_USD = float(os.environ.get("ARTURO_NATIVE_SESSION_MAX_USD", "0.50"))
IDLE_S = float(os.environ.get("ARTURO_NATIVE_IDLE_S", "90"))


class SpendStop(RuntimeError):
    pass


class CallSpend:
    def __init__(self, usage_store, backend_model, prices=None, clock=time.time, session_max_s=SESSION_MAX_S,
                 session_max_usd=SESSION_MAX_USD, idle_s=IDLE_S):
        if usage_store is None:
            raise TypeError("CallSpend needs the voice usage store (the daily cap)")
        self.usage, self.backend_model, self.clock = usage_store, backend_model, clock
        self.prices = BACKEND_PRICES if prices is None else prices
        self.session_max_s, self.session_max_usd, self.idle_s = session_max_s, session_max_usd, idle_s
        self.started_at = self.last_activity = None
        self.backend_usd = 0.0
        self.backend_unmeasured = 0
        self.recorded = False
        self.charged_s = 0.0

    def authorize(self):
        """Refuse a call unless every cost is measurable and the daily cap has headroom."""
        if not self.backend_model or self.backend_model not in self.prices:
            raise SpendStop(f"backend model {self.backend_model!r} has no verified price: its spend cannot be capped")
        if self.usage.over_cap(VENDOR):
            raise SpendStop(f"today's GPT-Live voice cap is used up ({self.usage.cap_min} min)")
        return True

    def start(self):
        self.started_at = self.last_activity = self.clock()

    def activity(self):
        self.last_activity = self.clock()

    def on_frame(self, frame):
        """Every server frame; returns None or a stop reason (the backend usage rule)."""
        if frame.get("type") != "response.event":
            return None
        ev = frame.get("event") or {}
        if ev.get("type") not in ("response.completed", "response.incomplete", "response.failed"):
            return None
        resp = ev.get("response") or {}
        usage, price = resp.get("usage"), self.prices.get(resp.get("model") or self.backend_model)
        if not usage or price is None:
            self.backend_unmeasured += 1
            return "stop: backend spend unmeasurable (no usage or no price on a completed response)"
        self.backend_usd += (float(usage.get("input_tokens") or 0) * price["input"]
                             + float(usage.get("output_tokens") or 0) * price["output"]) / 1e6
        return None

    def usd(self, billed_s):
        return round(billed_s * LIVE_USD_PER_S + self.backend_usd, 6)

    def check(self, billed_s):
        """None, or why to hang up now. Called about once a second."""
        if self.started_at is None:
            return None
        now = self.clock()
        if now - self.started_at >= self.session_max_s:
            return f"call time cap ({self.session_max_s / 60:.0f} min)"
        if self.usd(billed_s) >= self.session_max_usd:
            return f"call cost cap (${self.session_max_usd:.2f})"
        if self.usage.today_minutes(VENDOR) + max(0.0, billed_s - self.charged_s) / 60.0 >= self.usage.cap_min:
            return f"today's GPT-Live voice cap ({self.usage.cap_min} min)"
        if now - (self.last_activity or now) >= self.idle_s:
            return f"idle for {self.idle_s:.0f} s"
        return None

    def check_clock(self):
        """Offline calls spend nothing, but still end on the call-time cap and when idle (re-review N6: an idle offline
        call held one of the MAX_CALLS slots forever)."""
        if self.started_at is None:
            return None
        now = self.clock()
        if now - self.started_at >= self.session_max_s:
            return f"call time cap ({self.session_max_s / 60:.0f} min)"
        if now - (self.last_activity or now) >= self.idle_s:
            return f"idle for {self.idle_s:.0f} s"
        return None

    def checkpoint(self, billed_s):
        """Charge the daily cap with the seconds billed since the last charge (review M7: charging only at close let
        concurrent calls all see the same 'today', and a crash lost a live call's minutes entirely)."""
        delta = float(billed_s or 0) - self.charged_s
        if delta > 0 and not self.recorded:
            self.charged_s += delta
            self.usage.add_seconds(VENDOR, delta)

    def close(self, billed_s):
        """Charge the remainder, once."""
        if self.recorded:
            return
        self.checkpoint(billed_s)
        self.recorded = True
