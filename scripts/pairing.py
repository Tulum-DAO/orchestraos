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
import base64
import json
import os
import re
import secrets
import time
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_TTL_S = 600          # ten minutes, per the contract
CODE_BYTES = 12              # ~19 chars of urlsafe base64: unguessable inside the window
PAIR_TOKEN_PREFIX = "orc1_"  # versions the one-paste token; a client tells it from a bare code


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
        # An app released before pair_token existed sends whatever was pasted, so the whole
        # token or JSON line can arrive here as the "code". Unwrap it.
        parsed = parse_pair_input(code)
        path = self._path(parsed["code"]) if parsed else None
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


def pair_token(code, base_url):
    """The ONE string the operator pastes (and the QR carries): the qr_payload JSON,
    base64url'd without padding behind PAIR_TOKEN_PREFIX. One word, nothing a keyboard
    autocorrects, and it still tells the app WHERE the gateway is, so there is no address to type.
    contract/pair-token-cases.json is generated from this function and parse_pair_input."""
    raw = json.dumps({"code": code, "base_url": base_url}, separators=(",", ":")).encode()
    return PAIR_TOKEN_PREFIX + base64.urlsafe_b64encode(raw).decode().rstrip("=")


MAX_PAIR_INPUT = 4096        # a real token is ~110 chars; refuse a megabyte paste before decoding
_BARE_CODE = re.compile(r"[A-Za-z0-9_-]{1,256}")      # what token_urlsafe can produce
_TOKEN_BODY = re.compile(r"[A-Za-z0-9_-]+={0,2}")     # urlsafe base64, padding optional


def valid_base_url(base_url):
    """https with a host. `orchestra pair` refuses to mint against anything else, so it can
    never print a token that the parsers below would reject."""
    try:
        u = urlsplit(str(base_url or "").strip())
        u.port                       # raises on a malformed port
    except ValueError:
        return False
    return u.scheme.lower() == "https" and bool(u.hostname)


def _pair_json(text):
    try:
        obj = json.loads(text)
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    code, base_url = obj.get("code"), obj.get("base_url")
    if not (isinstance(code, str) and isinstance(base_url, str)):
        return None
    code, base_url = code.strip(), base_url.strip()
    if not (_BARE_CODE.fullmatch(code) and valid_base_url(base_url)):
        return None
    return {"code": code, "base_url": base_url}


def parse_pair_input(text):
    """Reference parser for whatever lands in the app's pairing box. The Mac and iOS parsers
    follow the same rules, and contract/pair-token-cases.json (scripts/gen_pair_token_cases.py)
    pins them. Returns {code, base_url} (base_url None for a bare code), or None when the input
    is not a pairing code at all.
      0. trim; empty or longer than MAX_PAIR_INPUT -> None;
      1. starts with orc1_ -> drop ALL whitespace from the rest (a ~110-char token wraps in an
         80-column terminal, and base64 never contains whitespace) -> urlsafe base64, padding
         optional -> the JSON rules below. If any of that fails, fall through to 3: a
         token_urlsafe code can itself begin with orc1_;
      2. starts with { -> the legacy compact JSON line released CLIs print. The object needs a
         string "code" (urlsafe alphabet once trimmed) and a string "base_url" (https with a
         host once trimmed). Anything else falls through to 3;
      3. a bare code: urlsafe alphabet only, 1-256 chars, no inner whitespace -> base_url None,
         and the app takes the address from its own field. Anything else -> None."""
    s = str(text or "").strip()
    if not s or len(s) > MAX_PAIR_INPUT:
        return None
    got = None
    if s.startswith(PAIR_TOKEN_PREFIX):
        body = "".join(s[len(PAIR_TOKEN_PREFIX):].split())
        if _TOKEN_BODY.fullmatch(body):
            try:
                raw = base64.urlsafe_b64decode(body.rstrip("=") + "=" * (-len(body.rstrip("=")) % 4))
                got = _pair_json(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                got = None
    elif s.startswith("{"):
        got = _pair_json(s)
    if got:
        return got
    return {"code": s, "base_url": None} if _BARE_CODE.fullmatch(s) else None
