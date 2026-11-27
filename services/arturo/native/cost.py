"""Usage and cost of one native call (ported from the reviewed POC, poc/gptlive_native/cost.py: CostRecorder + prices).

Live audio: session.usage.updated carries CUMULATIVE seconds ("Do not sum this value across usage events", DOC
primary-websocket); session.closed carries the final usage. Price $0.05/min billed per second. Hosted Responses model:
tokens from the nested response.completed usage x BACKEND_PRICES (input charged at the uncached rate: an over-estimate).
"""
LIVE_USD_PER_S = 0.05 / 60.0
# USD per 1M tokens, from docs_snapshot/luna.md L36-39 (fetched 2026-10-13). Any other model is unpriced -> refused.
BACKEND_PRICES = {"gpt-6-luna": {"input": 0.10, "cached_input": 0.01, "output": 0.50}}


class CostRecorder:
    def __init__(self):
        self.live_seconds = 0.0          # latest cumulative value, never summed
        self.closed_seconds = None
        self.response_usage = []         # nested usage blobs as reported (UNKNOWN U7)
        self.prior_seconds = 0.0         # earlier provider sessions of this call (the backend fallback opens a second)

    def on_frame(self, frame):
        t = frame.get("type")
        if t == "session.usage.updated":
            self.live_seconds = max(self.live_seconds, float((frame.get("usage") or {}).get("seconds") or 0.0))
        elif t == "session.closed":
            self.closed_seconds = float((frame.get("usage") or {}).get("seconds") or 0.0)
        elif t == "response.event":
            ev = frame.get("event") or {}
            usage = (ev.get("response") or {}).get("usage")
            if usage:
                self.response_usage.append(usage)

    def new_session(self):
        """A second provider session in the same call: its cumulative counter restarts at 0."""
        self.prior_seconds, self.live_seconds, self.closed_seconds = self.billed_seconds, 0.0, None

    @property
    def billed_seconds(self):
        return self.prior_seconds + (self.closed_seconds if self.closed_seconds is not None else self.live_seconds)

    @property
    def live_usd(self):
        return round(self.billed_seconds * LIVE_USD_PER_S, 4)

    def summary(self):
        return {"billed_s": self.billed_seconds, "live_usd": self.live_usd,
                "backend_usage_events": len(self.response_usage)}
