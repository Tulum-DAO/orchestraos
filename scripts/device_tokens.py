"""device_tokens.py — per-device gateway bearers with a verb scope.

Why this exists: `/pair/exchange` handed every paired device the ONE fleet-wide gateway
bearer. So the Quest app's "no inject" gate was a promise its own UI made, and anything
holding the headset's browser storage had full gateway power over a public Funnel. A
capability the server does not enforce is not a capability, it is a hope.

The shape, and the reasoning behind each decision:

  * A token is 32 random bytes. We store ONLY its sha256, so a stolen store yields nothing
    usable — the same reason a password file holds hashes. Lookup is by hashing what the
    caller presented, which is also why it stays O(1) with no scan.
  * SCOPES ARE VERBS, not roles. Roles are a presentation choice that can be built on top;
    what a route needs is a verb. Six: read, approve, message, inject, voice, admin.
  * `voice` is separate from `read` because every /arturo call SPENDS REAL PROVIDER MONEY.
    Reading state and buying tokens from a vendor are not the same permission.
  * `approve` is separate from everything because it ANSWERS ON THE OPERATOR'S BEHALF.
  * Revocation is a field honoured on every request, not a deletion, so a revoked device's
    past answers stay attributable (see the provenance field on approval answers).
  * One file per device, 0600, under <data dir>/state/devices — same reasoning as the
    pairing store: the CLI that mints and the gateway that resolves share them with no
    database and no running process between them.

There is NO default scope here on purpose. `mint()` requires an explicit, non-empty set:
operator ruling via gm 2026-10-05, "no silent default", so no device inherits `approve` by
accident. A caller that wants read-only must say so.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

TOKEN_BYTES = 32
VERBS = ("read", "approve", "message", "inject", "voice", "admin")

#: The one remaining all-powerful credential. The fleet token resolves to this so Shaw's
#: phone and watch keep working unchanged; it is NAMED in listings rather than hidden, so
#: nobody forgets it outranks every scoped device.
LEGACY_LABEL = "legacy-fleet-token"
ALL_SCOPES = ("*",)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class ScopeError(ValueError):
    """A scope set that is empty, or names a verb that does not exist."""


def normalize_scopes(scopes) -> tuple[str, ...]:
    """Reject unknown verbs loudly. A typo'd scope that silently granted nothing would
    produce a device that mysteriously 403s; one that silently granted everything would be
    the bug this module exists to remove."""
    if isinstance(scopes, str):
        scopes = [s for s in scopes.replace(",", " ").split() if s]
    out = []
    for s in scopes or []:
        s = str(s).strip().lower()
        if not s:
            continue
        if s == "*":
            return ALL_SCOPES
        if s not in VERBS:
            raise ScopeError(f"unknown scope {s!r}; known verbs are {', '.join(VERBS)}")
        if s not in out:
            out.append(s)
    if not out:
        raise ScopeError("a device must be minted with at least one explicit scope "
                         "(no silent default; use 'read' for read-only)")
    return tuple(out)


class DeviceStore:
    def __init__(self, directory):
        self.dir = Path(directory)

    # ---------------------------------------------------------------- writing

    def mint(self, label: str, scopes) -> tuple[str, str]:
        """Create a device and return (device_id, token). The token is returned ONCE and
        never stored; only its hash is persisted."""
        verbs = normalize_scopes(scopes)
        self.dir.mkdir(parents=True, exist_ok=True)
        token = secrets.token_urlsafe(TOKEN_BYTES)
        device_id = secrets.token_hex(8)
        rec = {
            "id": device_id,
            "label": str(label or "").strip() or "unnamed-device",
            "scopes": list(verbs),
            "token_sha256": _hash(token),
            "created_at": time.time(),
            "last_seen_at": None,
            "revoked_at": None,
        }
        path = self.dir / f"{device_id}.json"
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(rec, fh)
        return device_id, token

    def revoke(self, device_id: str) -> bool:
        """Mark a device revoked. Kept rather than deleted so its past answers remain
        attributable; returns False if there was nothing to revoke."""
        rec = self._read_by_id(device_id)
        if not rec or rec.get("revoked_at"):
            return False
        rec["revoked_at"] = time.time()
        self._write(rec)
        return True

    def touch(self, device_id: str) -> None:
        """Best-effort last_seen. Never allowed to fail a request."""
        try:
            rec = self._read_by_id(device_id)
            if rec:
                rec["last_seen_at"] = time.time()
                self._write(rec)
        except OSError:
            pass

    # ---------------------------------------------------------------- reading

    def resolve(self, token: str) -> dict | None:
        """Token -> device record, or None. A revoked device resolves to None.

        Compares the HASH with compare_digest. Hashing first already removes the obvious
        timing signal, but a plain `==` on the stored digest would leak a prefix-match
        oracle, and that costs nothing to avoid."""
        if not token:
            return None
        want = _hash(token)
        for rec in self._all():
            got = str(rec.get("token_sha256") or "")
            if got and hmac.compare_digest(got, want):
                if rec.get("revoked_at"):
                    return None
                return rec
        return None

    def list(self) -> list[dict]:
        """Every device, newest first, WITHOUT the hash — a listing is for humans and has
        no business carrying credential material, even hashed."""
        out = []
        for rec in self._all():
            out.append({k: v for k, v in rec.items() if k != "token_sha256"})
        out.sort(key=lambda r: r.get("created_at") or 0, reverse=True)
        return out

    # ---------------------------------------------------------------- internals

    def _all(self):
        if not self.dir.exists():
            return
        for path in sorted(self.dir.glob("*.json")):
            try:
                yield json.loads(path.read_text())
            except (OSError, ValueError):
                continue        # a half-written device file is not a usable credential

    def _read_by_id(self, device_id: str) -> dict | None:
        safe = "".join(ch for ch in str(device_id) if ch.isalnum() or ch in "-_")
        if not safe:
            return None
        try:
            return json.loads((self.dir / f"{safe}.json").read_text())
        except (OSError, ValueError):
            return None

    def _write(self, rec: dict) -> None:
        path = self.dir / f"{rec['id']}.json"
        tmp = path.with_suffix(".json.tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(rec, fh)
        os.replace(tmp, path)


def scopes_allow(scopes, needed: str | None) -> bool:
    """Does this scope set permit `needed`? `None` means a public route.

    `'*'` is the legacy fleet token. Everything else must name the verb explicitly —
    there is no implication between verbs (holding `inject` does not imply `read`),
    because an implication graph is a second policy nobody reviews."""
    if needed is None:
        return True
    if not scopes:
        return False
    if "*" in scopes:
        return True
    return needed in scopes
