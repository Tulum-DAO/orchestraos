# Security

## Reporting

Open a GitHub issue for anything already public. For an unpatched vulnerability, use GitHub's
private vulnerability reporting on this repository rather than a public issue.

## Advisory: hardcoded session/JWT secret fallbacks (fixed 2026-10-05)

**Affected:** any deployment before this fix that did not set `SESSION_SECRET` and `JWT_SECRET`
in the API's environment — which was the default.

`api/src/routes/token-auth.ts` and `api/src/routes/voice-live.ts` resolved their HMAC keys as
`process.env.X || '<literal>'`. Those literals were committed to this public repository. A
fallback secret in public source is not a default, it is a **published key**: anyone could read
it here, sign an `orchestra_session` cookie or a bearer token claiming `{ r: 'admin' }`, and be
trusted by any instance that had not overridden the env var.

**If you ran an affected build**, treat every previously issued session cookie and token as
forgeable, and assume any instance that was reachable off-host may have been accessed. Rotate the
secrets (below), which invalidates all outstanding tokens. The published literals are **burned**
and must never be reused as a secret anywhere.

### How secrets resolve now

`api/src/lib/shared-secret.ts` is the single reader, and there is no literal in it. For each of
`SESSION_SECRET` and `JWT_SECRET`, in order:

1. the environment variable itself;
2. the path in `<VAR>_FILE`, if set;
3. `<data dir>/state/session-secret` / `<data dir>/state/jwt-secret`;
4. failing all of those, **32 random bytes for that process only**.

Step 4 is fail-closed, not a fallback key: nobody else can know it, so anything signed elsewhere
is rejected. The API logs a warning once per secret when it happens.

### Setting them

```sh
# option A: a file, mode 0600 (survives restarts; what most installs want)
mkdir -p "$ORCHESTRA_DIR/state"
umask 077 && openssl rand -hex 32 > "$ORCHESTRA_DIR/state/session-secret"
umask 077 && openssl rand -hex 32 > "$ORCHESTRA_DIR/state/jwt-secret"

# option B: the environment
export SESSION_SECRET="$(openssl rand -hex 32)"
export JWT_SECRET="$(openssl rand -hex 32)"
```

If a separate process signs the session cookie, both must read the **same** `SESSION_SECRET`, or
cookies signed by one will not verify in the other. Point `SESSION_SECRET_FILE` at one shared
file.

**Consequence of doing nothing:** with no env var and no secret file, each API restart generates a
new key, so sessions do not survive a restart. That is deliberate — a forgeable key is worse than
a short-lived one.

## Rotating the gateway bearer

```sh
orchestra rotate-gateway-token        # revokes what it minted, THEN writes a new bearer
orchestra rotate-gateway-token --revoke-only   # revoke the children, keep the current bearer
```

**Rotation revokes every device token the bearer minted.** That is deliberate, and it is the whole
point: a rotation that leaves the old credential's children alive is theatre — the key everyone
believes is dead keeps working through the tokens it issued, and the people who decided to rotate
think they are finished.

So the order matters and is fixed: **revoke first, write the new secret second.** If the write fails,
the worst case is devices needing a re-pair, which is strictly better than a rotation that reported
success while descendants stayed live.

**What this means operationally:** after rotating, restart the gateway so it reads the new secret,
then **re-pair every device** — `orchestra pair --scopes …` or `POST /device/upgrade`. A device that
still works after a rotation is one minted BY HAND on the host (`minted_by` is not the fleet bearer);
check `orchestra devices`.

Tokens minted by the CLI on the host are **not** children of the fleet bearer and survive rotation by
design — that is how you keep a credential alive across a rotation on purpose.

## CI gates

Three separate jobs, because a green tick should mean what it says:

- `secret-scan` — `detect-secrets`, for credential-shaped strings.
- `secret-fallback-scan` — `scripts/scan_secret_fallbacks.py`, for `SECRET = env.X || "literal"`.
  It matches the **shape**, not the value, because the literal in this advisory read like a label
  and scored zero on entropy.
- `operator-identifier-scan` — `scripts/scan_operator_identifiers.py`, for session ids, incident
  ids and operator paths, which are a privacy question rather than a credential one.
