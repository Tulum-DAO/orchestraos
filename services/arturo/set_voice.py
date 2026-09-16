#!/usr/bin/env python3
"""Audition / switch Arturo's ElevenLabs voice — config-only, reversible, instant.

Usage:
  python3 services/arturo/set_voice.py list                 # show candidates + current
  python3 services/arturo/set_voice.py set <name-or-id>     # switch the Arturo agent's voice
  python3 services/arturo/set_voice.py preview <name-or-id> "text"  # save a sample .mp3 to /tmp

Voice is ONE field on the ElevenLabs agent (conversation_config.tts.voice_id), so switching is
a single PATCH — no clone/code change, no restart. Nothing else on the agent is touched.
"""
import os
import sys
import json
import urllib.request
import urllib.error
from pathlib import Path

AGENT_ID = "agent_9001kzmyj4jwe3m8xk2a2s5vn3ac"

# Curated candidates for the operator (professional-warm male + neutral). the operator hates the current default
# (Raffaele Montini, jarvis-poc-builder's pick). Pick one; audition with `preview`.
CANDIDATES = {
    "eric":    ("cjVigY5qzO86Huf0OWal", "Smooth, trustworthy — American conversational (warm-pro default pick)"),
    "chris":   ("iP95p4xoKVk53GoZ742B", "Charming, down-to-earth — American conversational"),
    "roger":   ("CwhRBWXzGAHq8TQ4Fs17", "Laid-back, resonant — American conversational"),
    "brian":   ("nPczCjzI2devNBz1zQrb", "Deep, resonant, comforting — American"),
    "daniel":  ("onwK4e9ZLuTAKqWW03F9", "Steady broadcaster — British, neutral-professional"),
    # the current one, for A/B reference
    "raffaele": ("HhBRXe1sPBLe9D9lX1HC", "CURRENT (the operator dislikes) — Raffaele Montini"),
}


def _key():
    secrets_file = Path.home() / "scripts" / "agent-orchestra" / ".env.secrets"
    for line in secrets_file.read_text().splitlines():
        if line.startswith("ELEVENLABS_API_KEY="):
            return line.split("=", 1)[1].strip()
    return os.environ.get("ELEVENLABS_API_KEY", "")


def _resolve(name_or_id):
    key = name_or_id.lower()
    if key in CANDIDATES:
        return CANDIDATES[key][0]
    return name_or_id   # assume it's a raw voice_id


def _current_voice():
    req = urllib.request.Request(f"https://api.elevenlabs.io/v1/convai/agents/{AGENT_ID}",
                                 headers={"xi-api-key": _key()})
    d = json.load(urllib.request.urlopen(req, timeout=20))
    return d["conversation_config"]["tts"]["voice_id"]


def cmd_list():
    cur = _current_voice()
    print(f"Current Arturo voice_id: {cur}\n")
    print("Candidates (use `set <name>`):")
    for name, (vid, desc) in CANDIDATES.items():
        mark = "  <-- CURRENT" if vid == cur else ""
        print(f"  {name:9s} {vid}  {desc}{mark}")


def cmd_set(name_or_id):
    vid = _resolve(name_or_id)
    patch = {"conversation_config": {"tts": {"voice_id": vid}}}
    req = urllib.request.Request(f"https://api.elevenlabs.io/v1/convai/agents/{AGENT_ID}",
                                 data=json.dumps(patch).encode(),
                                 headers={"xi-api-key": _key(), "Content-Type": "application/json"},
                                 method="PATCH")
    d = json.load(urllib.request.urlopen(req, timeout=30))
    got = d["conversation_config"]["tts"]["voice_id"]
    print(f"Arturo voice set -> {got}" if got == vid else f"WARN: expected {vid}, agent shows {got}")


def cmd_preview(name_or_id, text):
    vid = _resolve(name_or_id)
    body = json.dumps({"text": text, "model_id": "eleven_turbo_v2_5"}).encode()
    req = urllib.request.Request(f"https://api.elevenlabs.io/v1/text-to-speech/{vid}",
                                 data=body,
                                 headers={"xi-api-key": _key(), "Content-Type": "application/json"},
                                 method="POST")
    out = Path(f"/tmp/arturo-voice-{name_or_id}.mp3")
    with urllib.request.urlopen(req, timeout=30) as r:
        out.write_bytes(r.read())
    print(f"saved {out}")


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] == "list":
        cmd_list()
    elif sys.argv[1] == "set" and len(sys.argv) >= 3:
        cmd_set(sys.argv[2])
    elif sys.argv[1] == "preview" and len(sys.argv) >= 4:
        cmd_preview(sys.argv[2], sys.argv[3])
    else:
        print(__doc__)
        sys.exit(1)
