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
        lines.append("Scan this with the OrchestraOS app, or type the line below into it by hand:")
    else:
        lines.append("No QR encoder is installed on this machine, so here is the code itself.")
        lines.append("Type or paste this line into the OrchestraOS app:")
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

    from scripts.pairing import PairingStore

    base_url = getattr(args, "base_url", None) or os.environ.get("ORCHESTRA_PUBLIC_URL") or ""
    if not base_url:
        out("I do not know this gateway's public address, so a phone could not reach it.")
        out("Re-run with:  orchestra pair --base-url https://<host>:<port>")
        return 2
    token_file = os.environ.get("WATCH_GATEWAY_TOKEN_FILE") or str(
        Path(os.environ.get("ORCHESTRA_DIR") or (Path.home() / ".orchestra")) / "watch-gateway-token")
    try:
        token = Path(token_file).read_text().strip()
    except OSError:
        out(f"No gateway token at {token_file} — start the gateway once (`orchestra up`) first.")
        return 2
    if store is None:
        base = os.environ.get("ORCHESTRA_DIR") or str(Path.home() / ".orchestra")
        store = PairingStore(Path(base) / "state" / "pairing")
    store.sweep()
    code = store.mint(base_url=base_url, token=token)
    payload = store.qr_payload(code, base_url=base_url)
    out(build_pair_output(payload, qr=qr_text_or_none(payload, default_encoder()), ttl_s=store.ttl_s))
    if clear_after_s:
        try:
            time.sleep(clear_after_s)
            os.system("clear")
            out("Pairing code hidden. Run `orchestra pair` again if you still need it.")
        except KeyboardInterrupt:
            os.system("clear")
    return 0
