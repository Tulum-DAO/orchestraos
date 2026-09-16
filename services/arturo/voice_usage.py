"""Per-vendor daily voice minutes (spec §4.6): the guard against a third three-day burn.
Cap default 120 min/day/vendor (env VOICE_DAILY_CAP_MIN). 80% -> one alert per vendor per day
(native card via approval.py); 100% -> new conversations for that vendor are refused (503),
visibly, never silently."""
import json
import os
import subprocess
import threading
import time
from pathlib import Path

ORCHESTRA_DIR = Path(os.environ.get("ORCHESTRA_DIR", Path.home() / "scripts/agent-orchestra"))
DEFAULT_PATH = ORCHESTRA_DIR / "state/voice-usage.json"
DEFAULT_CAP = int(os.environ.get("VOICE_DAILY_CAP_MIN", "120"))


def _card_alert(message):
    """Native 'waiting on you' card so the operator sees the burn before it completes.

    FIRE-AND-FORGET on a daemon thread (gm must-fix): this is called from add_seconds(), which
    runs on the relay's end()/finalize path — a blocking subprocess here would stall call-finalize
    (same class as "timers must never block the relay"). The subprocess is dispatched to a daemon
    thread so the caller returns immediately.
    """
    def _run():
        try:
            subprocess.run(["python3", str(ORCHESTRA_DIR / "scripts/approval.py"), "human-task",
                            "--from", "arturo-proxy", "--task", message,
                            "--op-key", "voice-usage-" + time.strftime("%Y%m%d") + "-" + message.split()[0].lower(),
                            "--feature", "voice-usage", "--worker-kind", "pane"],
                           timeout=15, capture_output=True)
        except Exception:
            pass
    threading.Thread(target=_run, daemon=True, name="voice-usage-alert").start()


class UsageStore:
    def __init__(self, path=DEFAULT_PATH, cap_min=DEFAULT_CAP, now=time.time, alert=_card_alert):
        self.path = Path(path); self.cap_min = cap_min; self.now = now; self.alert = alert
        self._lock = threading.Lock()

    def _day(self):
        return time.strftime("%Y-%m-%d", time.gmtime(self.now()))

    def _read(self):
        try:
            d = json.loads(self.path.read_text())
        except Exception:
            d = {}
        if d.get("day") != self._day():
            d = {"day": self._day(), "seconds": {}, "alerted": []}
        return d

    def _write(self, d):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp"); tmp.write_text(json.dumps(d)); tmp.replace(self.path)

    def today_minutes(self, vendor):
        return int(self._read()["seconds"].get(vendor, 0) // 60)

    def over_cap(self, vendor):
        return self.today_minutes(vendor) >= self.cap_min

    def add_seconds(self, vendor, seconds):
        with self._lock:
            d = self._read()
            d["seconds"][vendor] = d["seconds"].get(vendor, 0) + max(0, float(seconds))
            mins = d["seconds"][vendor] / 60
            if mins >= 0.8 * self.cap_min and vendor not in d["alerted"]:
                d["alerted"].append(vendor)
                self._write(d)
                self.alert(f"{vendor} voice usage is at 80% of today's {self.cap_min}-minute cap "
                           f"({int(mins)} min). New calls on {vendor} stop at 100% — raise VOICE_DAILY_CAP_MIN "
                           f"or switch vendor if that's wrong.")
                return
            self._write(d)
