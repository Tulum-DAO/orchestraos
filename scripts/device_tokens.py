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
    what a route needs is a verb: read, approve, message, inject, ptt, voice, admin, usage, owner.
  * `ptt` exists because `voice` was too coarse for a headset: it also reached the FLEET-WIDE
    voice-vendor and voice-id writes, so a compromised headset could switch what every
    conversation on the box uses.

    What `ptt` reaches: a voice call to Arturo. On its own that call runs Arturo's READ-ONLY
    tool allowlist: it can look things up and message agents (attributed to Arturo, marked
    unverified), and cannot run commands, spawn, inject or text anyone (arturo-proxy.py
    _NON_FLEET_ALLOWED). It reaches the tools that act only when the caller holds `owner` (below)
    or is the fleet bearer: the gateway stamps who is calling and Arturo records it for the call.
    Still treat `ptt` as a STRONG grant: messaging an agent is acting on the fleet, and every turn
    spends provider credit, so it is excluded from HTTP_MINTABLE.

    The name is kept rather than changed because the live client gates on the string `ptt` and a
    rename would break a paired headset for a cosmetic gain. The honest fix is this paragraph.
  * `owner` says "this device is the operator's own": its push-to-talk calls (`ptt` routes) get
    Arturo's full tools, the same as the dashboard; its typed turns are unchanged. No route
    requires it; the gateway reads it only to stamp a call's caller. Mintable by the host CLI
    only, never over HTTP.
  * `voice` is separate from `read` because every live-audio call (/live, /arturo/transcribe)
    SPENDS REAL PROVIDER MONEY. Reading state and buying tokens from a vendor are not the same
    permission. Typed Arturo chat (/arturo/text*) is `message`, not `voice` (ruling 2026-10-09):
    its strongest act is an unverified agent message, which `message` already grants.
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
#: `usage` is INERT for now: no route requires it yet. It exists ahead of its consumer so devices
#: minted today can carry it, and a later usage-read route does not cost every phone a re-pair
#: (pairing codes are single-use, and a watch inherits its phone's token). It is not in
#: HTTP_MINTABLE: like every verb added later, it is mintable only by the host CLI until decided.
VERBS = ("read", "approve", "message", "inject", "ptt", "voice", "admin", "usage", "owner")

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

    def mint(self, label: str, scopes, minted_by: str | None = None) -> tuple[str, str]:
        """Create a device and return (device_id, token). The token is returned ONCE and
        never stored; only its hash is persisted.

        `minted_by` records WHICH credential issued this one (condition b). It is what makes
        rotation mean rotation: rotating a credential must be able to revoke everything it
        issued, or the old key outlives its own revocation through its children."""
        verbs = normalize_scopes(scopes)
        self.dir.mkdir(parents=True, exist_ok=True)
        token = secrets.token_urlsafe(TOKEN_BYTES)
        device_id = secrets.token_hex(8)
        rec = {
            "id": device_id,
            "label": str(label or "").strip() or "unnamed-device",
            "scopes": list(verbs),
            "token_sha256": _hash(token),
            "minted_by": str(minted_by) if minted_by else None,
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

    def revoke_minted_by(self, minted_by: str) -> list[str]:
        """Revoke every live device issued BY this credential. Returns the ids revoked.

        This is what makes rotation honest (condition c). Rotating the fleet bearer without
        this leaves its children working, so the credential everyone believes is dead still
        has living descendants — the exact failure mode of "we rotated, we're fine"."""
        revoked = []
        for rec in list(self._all()):
            if rec.get("revoked_at"):
                continue
            if str(rec.get("minted_by") or "") != str(minted_by):
                continue
            rec["revoked_at"] = time.time()
            self._write(rec)
            revoked.append(rec["id"])
        return revoked

    def revoke_label(self, label: str, *, minted_by: str | None = None) -> list[str]:
        """Revoke every live device carrying this label. Returns the ids revoked.

        Condition (e): an upgrade is idempotent per label, so re-upgrading 'quest-3' REPLACES
        the previous quest-3 token rather than leaving a growing pile of live credentials
        nobody is tracking. Accumulation is how a revocation list stops being read."""
        revoked = []
        want = str(label or "").strip()
        for rec in list(self._all()):
            if rec.get("revoked_at") or str(rec.get("label") or "").strip() != want:
                continue
            if minted_by is not None and str(rec.get("minted_by") or "") != str(minted_by):
                continue
            rec["revoked_at"] = time.time()
            self._write(rec)
            revoked.append(rec["id"])
        return revoked

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


#: Condition (a): what the FLEET BEARER may mint OVER HTTP. Everything else — inject, ptt,
#: voice, admin, owner — is mintable only by the VPS mint CLI, where a human is at a shell.
#:
#: `ptt` is excluded too, which is not an oversight: a credential that can mint itself speech is
#: minting PROVIDER SPEND, and the whole reason the upgrade endpoint is safe to expose is that
#: nothing it can issue presses keys or buys LIVE AUDIO. `message` does let a device run turns that
#: spend credit (an agent's turn after /agent-message, and since 2026-10-09 a typed Arturo turn,
#: lookups plus unverified agent messages only); that is accepted deliberately, as it always was
#: for /agent-message.
#:
#: It is an ALLOWLIST, not a denylist, so a verb added later is NOT mintable until someone
#: decides it is. A denylist would silently grant every future verb.
HTTP_MINTABLE = ("read", "approve", "message")


def http_mintable(scopes) -> tuple[bool, str]:
    """(ok, reason). Used by the upgrade endpoint to refuse before anything is created."""
    try:
        verbs = normalize_scopes(scopes)
    except ScopeError as e:
        return False, str(e)
    if "*" in verbs:
        return False, "the unscoped '*' is never mintable over HTTP"
    bad = [v for v in verbs if v not in HTTP_MINTABLE]
    if bad:
        return False, (f"{', '.join(bad)} cannot be minted over HTTP; only "
                       f"{', '.join(HTTP_MINTABLE)} can. Use the mint CLI on the host.")
    return True, ""
