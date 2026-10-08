"""`orchestra pair` — hand a phone the two things it cannot guess.

The operator, 2026-09-18: strangers must be able to onboard on Saturday, on the web port and the
iOS app. This is the terminal half: it mints a short-lived, single-use code (see
scripts/pairing.py) and shows it, so the phone can POST /pair/exchange and receive the
gateway's base_url and bearer.

Two deliberate refusals in here:

  * The QR is drawn only if this machine actually has an encoder. If it does not, we print
    NO QR rather than ASCII art that merely looks like one. A fake QR fails at the exact
    moment someone points a phone at it — in front of a room — and the raw string below it
    works either way.
  * The warning is in plain words, not security jargon, because the person who needs it is
    mid-screenshare and has about one second to understand what they are showing.
"""
import json


def qr_text_or_none(payload, encoder):
    """Render the payload as a terminal QR, or None when nothing here can. Never fabricates
    something QR-shaped that will not scan."""
    if encoder is None:
        return None
    try:
        return encoder.render(payload)
    except Exception:  # noqa: BLE001 — a failed draw is the same as no encoder
        return None


def default_encoder():
    """Whatever this install happens to have. Absent on a stock box, which is exactly why
    the raw string is the contract and the QR is the convenience."""
    try:
        import segno  # type: ignore

        class _Segno:
            def render(self, payload):
                import io
                buf = io.StringIO()
                segno.make(payload, error="m").terminal(out=buf, compact=True)
                return buf.getvalue()

        return _Segno()
    except ImportError:
        return None


def build_pair_output(payload, qr=None, ttl_s=600):
    """The whole screen, as one string. The raw payload is ALWAYS present, beneath the QR
    when there is one and in its place when there is not."""
    minutes = max(1, int(ttl_s) // 60)
    lines = []
    lines.append("Pair a phone with this OrchestraOS")
    lines.append("")
    if qr:
        lines.append(qr)
        lines.append("Scan this with the OrchestraOS app, or paste the code below into its pairing box:")
    else:
        lines.append("No QR encoder is installed on this machine, so here is the code itself.")
        lines.append("Copy this code and paste it into the OrchestraOS app's pairing box:")
    lines.append("")
    lines.append(payload)
    lines.append("")
    lines.append(f"This code contains a password for your server. Do not screenshare it,")
    lines.append(f"photograph it for anyone, or paste it into a chat. It stops working once")
    lines.append(f"a phone uses it, and it expires by itself in {minutes} minutes.")
    return "\n".join(lines)


def run_pair(args, settings=None, store=None, out=print, clear_after_s=60):
    """Mint a code and show it. Clears the screen afterwards so the password does not sit
    in somebody's scrollback."""
    import os
    import time
    from pathlib import Path

    from scripts.pairing import PairingStore, pair_token

    base_url = getattr(args, "base_url", None) or os.environ.get("ORCHESTRA_PUBLIC_URL") or ""
    if not base_url:
        out("I do not know this gateway's public address, so a phone could not reach it.")
        out("Re-run with:  orchestra pair --base-url https://<host>:<port>")
        return 2
    from scripts.pairing import valid_base_url
    if not valid_base_url(base_url):
        # The app refuses a token whose address is not https with a host, so minting one would
        # print a code that cannot pair anything.
        out(f"{base_url!r} is not an https address the app can reach.")
        out("Re-run with:  orchestra pair --base-url https://<host>:<port>")
        return 2
    # A pairing used to hand over the FLEET bearer, so every paired device held full gateway
    # power and any "this device cannot inject" rule was a promise the client made about itself.
    # It now mints a PER-DEVICE token with an explicit verb scope, which the gateway enforces.
    from scripts.device_tokens import DeviceStore, ScopeError, normalize_scopes

    raw_scopes = getattr(args, "scopes", None)
    if not raw_scopes:
        out("Refusing to pair without --scopes: a device's power has to be a deliberate choice.")
        out("")
        out("  read     see approvals, agents, transcripts            (a viewer)")
        out("  approve  ANSWER approvals and questionnaires            (acts as you)")
        out("  message  send a message to an agent, upload a file")
        out("  inject   press keys into a live agent pane")
        out("  voice    talk to Arturo  (every call spends provider credit)")
        out("  admin    file red-alert reports, post telemetry")
        out("")
        out("  A read-only phone:   orchestra pair --scopes read")
        out("  A headset that approves:  orchestra pair --scopes read,approve,message")
        return 2
    try:
        verbs = normalize_scopes(raw_scopes)
    except ScopeError as e:
        out(f"Not a usable scope list: {e}")
        return 2

    base = os.environ.get("ORCHESTRA_DIR") or str(Path.home() / ".orchestra")
    devices = DeviceStore(Path(base) / "state" / "devices")
    label = (getattr(args, "label", None) or "paired-device").strip()
    try:
        device_id, token = devices.mint(label, verbs)
    except OSError as e:
        out(f"Could not write the device record: {e}")
        return 2
    out(f"Minted device {device_id} ({label}) with scopes: {', '.join(verbs)}")
    out(f"Revoke it any time with:  orchestra devices --revoke {device_id}")
    out("")
    if store is None:
        base = os.environ.get("ORCHESTRA_DIR") or str(Path.home() / ".orchestra")
        store = PairingStore(Path(base) / "state" / "pairing")
    store.sweep()
    code = store.mint(base_url=base_url, token=token)
    payload = pair_token(code, base_url=base_url)
    out(build_pair_output(payload, qr=qr_text_or_none(payload, default_encoder()), ttl_s=store.ttl_s))
    if clear_after_s:
        try:
            time.sleep(clear_after_s)
            os.system("clear")
            out("Pairing code hidden. Run `orchestra pair` again if you still need it.")
        except KeyboardInterrupt:
            os.system("clear")
    return 0


def run_devices(args, out=print):
    """List paired devices, or revoke one.

    A revoked device is KEPT rather than deleted, so an approval it answered in the past stays
    attributable to it. The listing never carries token material, not even the hash."""
    import os
    from datetime import datetime, timezone
    from pathlib import Path

    from scripts.device_tokens import DeviceStore, LEGACY_LABEL

    base = os.environ.get("ORCHESTRA_DIR") or str(Path.home() / ".orchestra")
    devices = DeviceStore(Path(base) / "state" / "devices")

    revoke_id = getattr(args, "revoke", None)
    if revoke_id:
        if devices.revoke(revoke_id):
            out(f"Revoked {revoke_id}. Its token stops working on the very next request.")
            return 0
        out(f"Nothing to revoke: {revoke_id} is not a device here, or was already revoked.")
        return 2

    rows = devices.list()
    if not rows:
        out("No paired devices yet. `orchestra pair --scopes read` mints one.")
    else:
        def when(ts):
            if not ts:
                return "never"
            return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        out(f"{'DEVICE':18} {'LABEL':22} {'SCOPES':34} {'LAST SEEN':20} STATE")
        for r in rows:
            state = "REVOKED" if r.get("revoked_at") else "active"
            out(f"{r['id']:18} {(r.get('label') or ''):22} "
                f"{','.join(r.get('scopes') or []):34} {when(r.get('last_seen_at')):20} {state}")
    # The one credential that is NOT in this list and outranks everything in it.
    out("")
    out(f"Note: the gateway's own bearer ({LEGACY_LABEL}) still works and has EVERY scope. "
        "It is not a device and cannot be revoked here.")
    return 0


def run_rotate_fleet_token(args, out=print):
    """Rotate the gateway's fleet bearer AND revoke everything it minted.

    Condition (c), and the reason it is one command rather than two: a rotation that leaves the
    old credential's children alive is theatre. The key everyone believes is dead keeps working
    through the tokens it issued, and the people who decided to rotate think they are done.

    So revocation happens FIRST. If writing the new secret fails afterwards, the worst outcome is
    that some devices need re-pairing — strictly better than a rotation that reported success
    while leaving descendants live.
    """
    import os
    import secrets
    from pathlib import Path

    from scripts.device_tokens import DeviceStore

    base = os.environ.get("ORCHESTRA_DIR") or str(Path.home() / ".orchestra")
    devices = DeviceStore(Path(base) / "state" / "devices")
    token_file = os.environ.get("WATCH_GATEWAY_TOKEN_FILE") or str(
        Path(base) / "state" / "watch-gateway-token")

    minter = getattr(args, "minted_by", None) or "legacy-fleet-token"
    revoked = devices.revoke_minted_by(minter)
    out(f"Revoked {len(revoked)} device token(s) minted by {minter}.")
    for d in revoked:
        out(f"  {d}")

    if getattr(args, "revoke_only", False):
        out("--revoke-only: the fleet bearer itself was NOT rotated.")
        return 0

    # Leave POSITIVE evidence of the rotation before the old bearer is gone, so the push
    # sender prunes the old bearer's tokens (a lost phone stops receiving pushes) without ever
    # treating a merely different bearer as a rotation.
    try:
        old = Path(token_file).read_text().strip()
    except OSError:
        old = ""
    if old:
        from scripts.push_tokens import PushTokenStore
        PushTokenStore(Path(base) / "state" / "push-tokens.json").retire_bearer(old)

    new = secrets.token_urlsafe(32)
    path = Path(token_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(new + "\n")
    out(f"Wrote a new fleet bearer to {token_file} (mode 0600). The value is NOT printed.")
    out("")
    out("Now RESTART the gateway so it reads the new secret, then re-pair each device that")
    out("needs one. Every token the old bearer minted is already dead, so a device that still")
    out("works is one you minted by hand on the host — check `orchestra devices`.")
    return 0
