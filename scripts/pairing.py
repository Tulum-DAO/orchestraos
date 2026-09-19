"""pairing.py — short-lived, single-use pairing codes for `orchestra pair`.

The operator, 2026-09-18: strangers must be able to onboard on Saturday, on the web port and the
iOS app. Pairing is the step where a phone learns two things it cannot guess: WHERE the
gateway is, and the bearer that opens it.

The security shape drove every decision here, because the thing being handed over is a
bearer for the operator's entire gateway:

  * the QR carries a CODE, never the token — a photograph of a QR is not a password, it is
    a claim ticket that expires;
  * single-use — redeeming it destroys it, so a screenshot in a group chat cannot be
    replayed after the operator has paired;
  * short-lived — ten minutes by default, so a leaked photo goes stale on its own;
  * a wrong code, a spent code and a code that never existed are refused IDENTICALLY, so
    nothing tells an attacker which half of a guess was right.

Codes live as one file each under the data dir, so the CLI that mints and the gateway that
redeems share them without a database or a running process between them.
"""
import json
import os
import secrets
import time
from pathlib import Path

DEFAULT_TTL_S = 600          # ten minutes, per the contract
CODE_BYTES = 12              # ~19 chars of urlsafe base64: unguessable inside the window


class PairingStore:
    def __init__(self, directory, ttl_s=DEFAULT_TTL_S):
        self.dir = Path(directory)
        self.ttl_s = int(ttl_s)

    def _now(self):
        return time.time()

    def _path(self, code):
        # codes are urlsafe base64; refuse anything that could escape the directory
        safe = "".join(ch for ch in str(code) if ch.isalnum() or ch in "-_")
        if not safe:
            return None
        return self.dir / f"{safe}.json"

    def mint(self, base_url, token):
        """Create a code that can be exchanged ONCE for {base_url, token}."""
        self.dir.mkdir(parents=True, exist_ok=True)
        code = secrets.token_urlsafe(CODE_BYTES)
        payload = {"base_url": base_url, "token": token, "expires_at": self._now() + self.ttl_s}
        path = self._path(code)
        # 0600 before any content is written: the token is inside.
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh)
        return code

    def redeem(self, code):
        """Exchange a code for {base_url, token}, or None. Destroys the code either way it
        was valid — spent, expired and never-existed all return None."""
        path = self._path(code)
        if path is None:
            return None
        try:
            raw = path.read_text()
        except OSError:
            return None
        # Unlink FIRST: a concurrent second exchange must lose, even if what follows throws.
        try:
            path.unlink()
        except OSError:
            return None
        try:
            payload = json.loads(raw)
        except ValueError:
            return None
        if float(payload.get("expires_at", 0)) < self._now():
            return None
        return {"base_url": payload.get("base_url"), "token": payload.get("token")}

    def sweep(self):
        """Drop expired codes so the directory does not grow without bound."""
        if not self.dir.exists():
            return 0
        dropped = 0
        for path in self.dir.glob("*.json"):
            try:
                payload = json.loads(path.read_text())
                if float(payload.get("expires_at", 0)) < self._now():
                    path.unlink()
                    dropped += 1
            except (OSError, ValueError):
                try:
                    path.unlink()
                    dropped += 1
                except OSError:
                    pass
        return dropped

    def count(self):
        return len(list(self.dir.glob("*.json"))) if self.dir.exists() else 0

    def qr_payload(self, code, base_url):
        """What the QR encodes — and exactly what the phone posts back. The token is NOT in
        here: it is what the code is exchanged FOR."""
        return json.dumps({"code": code, "base_url": base_url}, separators=(",", ":"))
