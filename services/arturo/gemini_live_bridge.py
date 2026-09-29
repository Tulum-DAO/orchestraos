#!/usr/bin/env python3
import asyncio
import base64
import json
import logging
import os
import sys
import time
import uuid
import aiohttp
from datetime import datetime, timezone
from pathlib import Path

ARTURO_DIR = Path(__file__).resolve().parent
def _default_orchestra_dir() -> Path:
    """#86: the DATA dir, not the repo root.

    The bridge journals voice calls to <this>/state/voice-calls and the gateway serves transcript
    cards from <data dir>/state/voice-calls. Defaulting to the repo root made those two different
    directories on every fresh install, so every card 404'd — and the documented workaround was a
    symlink, i.e. a per-machine patch for a path orchestra.toml already knows.
    Falls back to the repo root only if the config cannot be read, which keeps a checkout with no
    orchestra.toml working exactly as before.
    """
    try:
        sys.path.insert(0, str(ARTURO_DIR.parent))
        from config import load as _load_config       # services/config.py
        return Path(_load_config().data_dir)
    except Exception:
        return ARTURO_DIR.parent.parent


ORCHESTRA_DIR = Path(os.environ.get("ORCHESTRA_DIR") or _default_orchestra_dir())  # #86: env first
sys.path.insert(0, str(ARTURO_DIR))
sys.path.insert(0, str(ORCHESTRA_DIR / "scripts"))

execute_tool_fn = None
build_context_fn = None
_attempt_gm_inject_fn = None

try:
    import importlib.util
    spec = importlib.util.spec_from_file_location("arturo_proxy", str(ARTURO_DIR / "arturo-proxy.py"))
    arturo_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(arturo_mod)
    execute_tool_fn = getattr(arturo_mod, "execute_tool", None)
    build_context_fn = getattr(arturo_mod, "build_context", None)
    _attempt_gm_inject_fn = getattr(arturo_mod, "_attempt_gm_inject", None)
except Exception as e:
    logging.error(f"Failed to import arturo-proxy modules: {e}")

log = logging.getLogger("gemini_live_bridge")
log.setLevel(logging.INFO)

GEMINI_LIVE_URL = "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent"
# PINNED (was "models/gemini-2.5-flash-native-audio-latest" — a floating tag that
# silently drifted to the newest release and misbehaved 2026-08-26: hallucinated
# name, complied with a negated command, ~10s greeting latency). Pinned to the
# PRIOR dated release to restore pre-drift behavior AND stop the float. gm verified
# both dated ids exist + support bidiGenerateContent against Google's live model
# list. Fallback if a 09-2025 test call is WORSE: pin to -12-2025 (freezes the
# current model, stops future swaps but likely keeps the drift symptoms).
# NOTE (voice-connect-resilience branch): this branch's base still carried the
# floating tag; the live watch_gateway runs the pinned value (uncommitted in the
# main checkout). We MATCH the live pin here — the resilience fix does NOT change
# the pin decision, it just carries the same string the running system uses.
MODEL_NAME = "models/gemini-2.5-flash-native-audio-preview-09-2025"
SUPPORTED_VOICES = {"Fenrir", "Charon", "Puck", "Orus", "Aoede", "Kore"}

# --- Upstream (Gemini Live) connect/setup RESILIENCE ------------------------
# The client `/live` WS upgrades (101) but we only send it {"event":"connected"}
# AFTER the UPSTREAM Gemini Live setup completes (ws_connect -> send setup frame
# -> await setupComplete). That upstream setup is INTERMITTENTLY slow (~11% of
# calls >10s, worst measured 113s = the operator's failed call). A single cold-start
# attempt that stalls hangs the whole call -> client times out -> "live
# connection closed" -> server "Cannot write to closing transport".
#
# Fix: bound each upstream-setup attempt with a timeout and fast-fail-and-retry
# with small backoff. A stalled 15-113s attempt is replaced by fast reconnects
# that usually hit a 0-1s connect, so the client's "connected" lands inside its
# window. The client-facing contract is UNCHANGED: still exactly one
# {"event":"connected"} on success, and nothing until then.
UPSTREAM_SETUP_TIMEOUT_S = 8.0   # per-attempt bound on ws_connect + setup roundtrip
UPSTREAM_SETUP_ATTEMPTS = 3      # total attempts before giving up on the client
UPSTREAM_RETRY_BACKOFF_S = 0.5   # small sleep between attempts


# Injectable connect seam so tests can mock the upstream ws with no real network.
# Returns a live aiohttp ClientWebSocketResponse-like object (send_str / receive /
# close / async-iter). NOTE: this intentionally does NOT use the ws_connect async
# context manager so the retry loop can own explicit teardown (close) of a
# half-open upstream ws before reconnecting.
async def _open_upstream_ws(url: str):
    # Bound the SOCKET itself. The outer asyncio.wait_for (in
    # _connect_upstream_with_retry) cancels the task, but aiohttp's ws_connect is
    # NOT cancel-responsive mid TCP/TLS connect, so a stalled cold connect can
    # still eat 30s+ (measured: attempt-1 ate 34s -> an 88.7s call). A session
    # ClientTimeout with sock_connect/sock_read makes the socket fail-fast so the
    # retry loop actually gets to reconnect; the outer wait_for stays as a belt.
    session = aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(
            total=UPSTREAM_SETUP_TIMEOUT_S, sock_connect=4, sock_read=4
        )
    )
    try:
        ws = await session.ws_connect(url)
    except Exception:
        await session.close()
        raise
    # Stash the owning session on the ws so the caller can close both on teardown.
    ws._bridge_owner_session = session
    return ws


async def _close_upstream_ws(ws):
    """Fully tear down a (possibly half-open) upstream ws AND its owning aiohttp
    session so a retry leaks no sockets and starts no double-relay."""
    if ws is None:
        return
    try:
        await ws.close()
    except Exception:
        pass
    owner = getattr(ws, "_bridge_owner_session", None)
    if owner is not None:
        try:
            await owner.close()
        except Exception:
            pass

def get_gemini_api_key() -> str:
    secrets_file = ORCHESTRA_DIR / ".env.secrets"
    if secrets_file.exists():
        for line in secrets_file.read_text().splitlines():
            if line.startswith("GEMINI_API_KEY="):
                return line.split("=", 1)[1].strip()
    return os.environ.get("GEMINI_API_KEY", "")

TOOL_DECLARATIONS = [
    {
        "name": "read_screen_context",
        "description": "Read the user active screen context on their device or Mac.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "detail": {"type": "STRING", "description": "Specific element to inspect, e.g. active_tab"}
            }
        }
    },
    {
        "name": "knowledge",
        "description": "Fast lookup of codebase, roadmap, system state, and facts.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {"type": "STRING", "description": "The search query"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "gm_command",
        "description": "Execute deep operations or queries via the Gemini deep brain (gemini-orchestra-dev).",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "prompt": {"type": "STRING", "description": "The command or question for the deep brain"}
            },
            "required": ["prompt"]
        }
    },
    {
        "name": "async_task",
        "description": "Dispatch a long-running background task and notify the operator via Telegram when done.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "task_description": {"type": "STRING", "description": "Detailed description of the task to perform"}
            },
            "required": ["task_description"]
        }
    },
    {
        "name": "send_telegram",
        "description": "Send a direct Telegram message to the operator.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "message": {"type": "STRING", "description": "Text message content"}
            },
            "required": ["message"]
        }
    },
    {
        "name": "list_agents",
        "description": "List all running agent sessions and their status in the orchestra fleet.",
        "parameters": {"type": "OBJECT", "properties": {}}
    }
]

class GeminiLiveSession:
    def __init__(self, client_ws, voice_name: str = "Fenrir", session_id: str = None):
        self.client_ws = client_ws
        self.voice_name = voice_name if voice_name in SUPPORTED_VOICES else "Fenrir"
        self.session_id = session_id or f"vc_live_{uuid.uuid4().hex[:12]}"
        self.start_time = time.time()
        self.turns = []
        self.user_buffer = ""
        self.model_buffer = ""
        self.model_turn_pcm = bytearray()
        self.user_turn_pcm = bytearray()
        self.last_user_turn_text = ""
        self.page, self.surface_desc = self._load_surface_context()

    def _load_surface_context(self) -> tuple[str, str]:
        surface_paths = [
            ORCHESTRA_DIR / "state" / "arturo" / "active-surface.json",
            ORCHESTRA_DIR / "state" / "surface.json",
        ]
        route = "/arturo"
        desc = ""
        for p in surface_paths:
            if p.exists():
                try:
                    data = json.loads(p.read_text())
                    cur = data.get("current") or {}
                    route = cur.get("route") or route
                    hint = (cur.get("hint") or "").strip()
                    focused = cur.get("focused")
                    device = cur.get("device", "ios")
                    desc_parts = [f"the operator is looking at {route} on {device}"]
                    if hint:
                        desc_parts.append(f'hint: "{hint}"')
                    if focused:
                        desc_parts.append(f"focused div/element: {focused}")
                    desc = " — ".join(desc_parts)
                    break
                except Exception as e:
                    log.warning(f"Error reading surface file {p}: {e}")
        return route, desc

    def _write_live_call_journal(self, status: str = "live"):
        now = time.time()
        duration_s = round(now - self.start_time, 1)
        call_doc = {
            "call_id": self.session_id,
            "voice": self.voice_name,
            "engine": "gemini-live-2.5-flash",
            "started_at": self.start_time,
            "start_time": datetime.fromtimestamp(self.start_time, timezone.utc).isoformat(),
            "duration_seconds": duration_s,
            "status": status,
            "page": self.page,
            "turns": list(self.turns),
            "summary": f"Voice call: {len(self.turns)} turns."
        }
        if status == "ended":
            call_doc["ended_at"] = now

        call_file = ORCHESTRA_DIR / "state" / "voice-calls" / f"{self.session_id}.json"
        call_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = call_file.with_suffix(f".tmp.{os.getpid()}")
        try:
            tmp_file.write_text(json.dumps(call_doc, indent=2))
            tmp_file.replace(call_file)
        except Exception as e:
            log.warning(f"[{self.session_id}] Failed to write call journal: {e}")

    async def run(self):
        api_key = get_gemini_api_key()
        if not api_key:
            log.error("GEMINI_API_KEY missing")
            await self.client_ws.send_json({"error": "GEMINI_API_KEY missing on server"})
            return

        url = f"{GEMINI_LIVE_URL}?key={api_key}"
        log.info(f"[{self.session_id}] Connecting to Gemini Live with voice={self.voice_name}...")

        system_instruction_parts = [
            "You are Arturo, the operator's personal AI Operations Commander and voice co-pilot inside OrchestraOS — his always-on partner who runs his agent fleet with him.",
            "You are NOT a generic phone or device assistant. You never talk about phone settings, accessibility menus, OS features, or 'live caption' toggles. If something on the operator's screen or app isn't working, you say you'll look into it or dispatch it — you never tell the operator to go change a device setting.",
            "You know the operator, his projects and clients, and his agent fleet. Act like it: be decisive, specific, and action-oriented — never vague, generic, or deflecting.",
            "Your deep brain reasoning runs in the 'gemini-orchestra-dev' session; use your tools to read his screen, look things up, dispatch tasks, and message him.",
            "When the operator asks you to do something, DO it with a tool — do not just describe it. When you don't know, use the knowledge or gm_command tool rather than guessing or giving a generic answer.",
            "Talk like a sharp, concise human partner. Speak in 1-2 natural spoken sentences max.",
            "NEVER use markdown, bullets, asterisks, or lists. NEVER speak internal thoughts or reasoning aloud.",
            "Execute tools directly and report results naturally.",
            "When corrected, say 'got it' and proceed immediately. Never argue with the operator about what he is seeing."
        ]
        if self.surface_desc:
            system_instruction_parts.append(f"ACTIVE SCREEN CONTEXT: {self.surface_desc}.")

        system_instruction = " ".join(system_instruction_parts)

        setup_frame = {
            "setup": {
                "model": MODEL_NAME,
                "generation_config": {
                    "response_modalities": ["AUDIO"],
                    "speech_config": {
                        "voice_config": {
                            "prebuilt_voice_config": {
                                "voice_name": self.voice_name
                            }
                        }
                    },
                    "thinking_config": {
                        "thinking_budget": 0
                    }
                },
                "system_instruction": {
                    "parts": [{"text": system_instruction}]
                },
                "tools": [{"function_declarations": TOOL_DECLARATIONS}],
                # Native Live-API OUTPUT transcription: streams Arturo's spoken words
                # word-by-word in serverContent.outputTranscription -> client caption
                # events (role:"arturo"). Replaces the old post-hoc batch
                # transcribe_pcm_audio path (laggy AND echoed its own instruction
                # prompt on near-silence). NB: flows through the retry helper unchanged.
                # USER captions are intentionally NOT server-transcribed: the client
                # renders the user's words live ON-DEVICE (SFSpeech) and posts them
                # back as {event:"user_turn"} for the journal, so a server
                # input_audio_transcription would be a redundant 2nd source (double
                # captions / journal). One owner per role: user=on-device, arturo=server.
                "output_audio_transcription": {}
            }
        }

        # Bounded-timeout retry around the upstream connect+setup. Only a
        # successfully-setup upstream ws is handed back; the client "connected"
        # signal is sent below, AFTER success (contract unchanged).
        gemini_ws = await self._connect_upstream_with_retry(url, setup_frame)
        if gemini_ws is None:
            # All attempts exhausted. The client was already told an explicit
            # reason inside the retry helper; nothing more to relay.
            self._finalize_call()
            return

        try:
            log.info(f"[{self.session_id}] Gemini Live setup complete. Starting audio streaming.")
            self._write_live_call_journal(status="live")
            await self.client_ws.send_json({
                "event": "connected",
                "session_id": self.session_id,
                "voice": self.voice_name
            })

            # Trigger Arturo opening greeting immediately on connection (<1s TTFT)
            greeting_turn = {
                "client_content": {
                    "turns": [{
                        "role": "user",
                        "parts": [{"text": "the operator just joined voice. Greet the operator in one short crisp sentence."}]
                    }],
                    "turn_complete": True
                }
            }
            await gemini_ws.send_str(json.dumps(greeting_turn))

            client_task = asyncio.create_task(self._client_to_gemini(gemini_ws))
            gemini_task = asyncio.create_task(self._gemini_to_client(gemini_ws))

            done, pending = await asyncio.wait(
                [client_task, gemini_task],
                return_when=asyncio.FIRST_COMPLETED
            )
            for t in pending:
                t.cancel()
        finally:
            # Tear down the live upstream ws (+ its owning session) on call end.
            await _close_upstream_ws(gemini_ws)

        self._finalize_call()

    async def _setup_upstream_once(self, url: str, setup_frame: dict):
        """ONE upstream attempt: open ws -> send setup frame -> await
        setupComplete. Returns the live upstream ws on success; raises on any
        failure (incl. a non-setupComplete response). The caller wraps this in
        asyncio.wait_for for the per-attempt timeout and owns teardown."""
        gemini_ws = await _open_upstream_ws(url)
        try:
            await gemini_ws.send_str(json.dumps(setup_frame))
            init_msg = await gemini_ws.receive()
            raw_init = init_msg.data.decode("utf-8") if isinstance(init_msg.data, (bytes, bytearray)) else init_msg.data
            init_data = json.loads(raw_init) if raw_init else {}
            if "setupComplete" not in init_data:
                raise RuntimeError(f"Gemini setup response missing setupComplete: {init_data}")
            return gemini_ws
        except BaseException:
            # This attempt failed mid-setup (error OR wait_for-timeout, which
            # cancels us -> asyncio.CancelledError, NOT an Exception subclass):
            # fully tear down the half-open ws so a retry leaks no socket and
            # starts no double-relay. Then re-raise (incl. CancelledError).
            await _close_upstream_ws(gemini_ws)
            raise

    async def _connect_upstream_with_retry(self, url: str, setup_frame: dict):
        """Run up to UPSTREAM_SETUP_ATTEMPTS bounded (UPSTREAM_SETUP_TIMEOUT_S)
        upstream-setup attempts with small backoff. Returns the live upstream ws
        on the first success, or None after exhausting all attempts (having told
        the client an explicit failure reason). No client "connected" is ever
        sent here."""
        last_reason = "unknown"
        for attempt in range(1, UPSTREAM_SETUP_ATTEMPTS + 1):
            try:
                gemini_ws = await asyncio.wait_for(
                    self._setup_upstream_once(url, setup_frame),
                    timeout=UPSTREAM_SETUP_TIMEOUT_S,
                )
                if attempt > 1:
                    log.info(f"[{self.session_id}] Upstream setup succeeded on attempt {attempt}/{UPSTREAM_SETUP_ATTEMPTS}.")
                return gemini_ws
            except asyncio.TimeoutError:
                # NOTE: on wait_for-timeout the wrapped coroutine is cancelled;
                # _setup_upstream_once's except-clause tears the half-open ws
                # down before propagating, so no socket leaks here.
                last_reason = f"upstream setup timed out after {UPSTREAM_SETUP_TIMEOUT_S}s"
                log.warning(f"[{self.session_id}] {last_reason} (attempt {attempt}/{UPSTREAM_SETUP_ATTEMPTS})")
            except Exception as e:
                last_reason = f"upstream setup error: {e}"
                log.warning(f"[{self.session_id}] {last_reason} (attempt {attempt}/{UPSTREAM_SETUP_ATTEMPTS})")

            if attempt < UPSTREAM_SETUP_ATTEMPTS and UPSTREAM_RETRY_BACKOFF_S > 0:
                await asyncio.sleep(UPSTREAM_RETRY_BACKOFF_S)

        # Exhausted every attempt: close the client with an explicit reason
        # (logged once) so it stops waiting on a "connected" that won't come.
        log.error(f"[{self.session_id}] Gemini Live setup failed after {UPSTREAM_SETUP_ATTEMPTS} attempts: {last_reason}")
        try:
            await self.client_ws.send_json({
                "error": "Gemini Live setup failed",
                "reason": last_reason,
                "attempts": UPSTREAM_SETUP_ATTEMPTS,
            })
        except Exception:
            pass
        try:
            await self.client_ws.close()
        except Exception:
            pass
        return None

    async def _client_to_gemini(self, gemini_ws):
        try:
            async for msg in self.client_ws:
                if msg.type == aiohttp.WSMsgType.BINARY:
                    self.user_turn_pcm.extend(msg.data)
                    pcm_b64 = base64.b64encode(msg.data).decode("utf-8")
                    realtime_chunk = {
                        "realtime_input": {
                            "media_chunks": [
                                {
                                    "mime_type": "audio/pcm;rate=16000",
                                    "data": pcm_b64
                                }
                            ]
                        }
                    }
                    await gemini_ws.send_str(json.dumps(realtime_chunk))
                elif msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                    except Exception:
                        continue
                    if "audio_chunk" in data:
                        realtime_chunk = {
                            "realtime_input": {
                                "media_chunks": [
                                    {
                                        "mime_type": "audio/pcm;rate=16000",
                                        "data": data["audio_chunk"]
                                    }
                                ]
                            }
                        }
                        await gemini_ws.send_str(json.dumps(realtime_chunk))
                    elif data.get("event") in ("user_turn", "user_turn_log"):
                        user_text = data.get("text", "").strip()
                        if user_text and user_text != self.last_user_turn_text:
                            self.last_user_turn_text = user_text
                            self.turns.append({"role": "user", "text": user_text, "ts": time.time()})
                            self._write_live_call_journal(status="live")
                            self.user_turn_pcm.clear()
                    elif "text" in data and not data.get("event"):
                        user_text = data.get("text", "").strip()
                        if user_text:
                            self.turns.append({"role": "user", "text": user_text, "ts": time.time()})
                            self._write_live_call_journal(status="live")
                            client_turn = {
                                "client_content": {
                                    "turns": [{"role": "user", "parts": [{"text": user_text}]}],
                                    "turn_complete": True
                                }
                            }
                            await gemini_ws.send_str(json.dumps(client_turn))
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.warning(f"[{self.session_id}] client_to_gemini exception: {e}")

    async def _gemini_to_client(self, gemini_ws):
        try:
            async for msg in gemini_ws:
                raw = msg.data.decode("utf-8") if isinstance(msg.data, (bytes, bytearray)) else msg.data
                try:
                    data = json.loads(raw)
                except Exception:
                    continue

                server_content = data.get("serverContent", {})
                if server_content.get("interrupted"):
                    log.info(f"[{self.session_id}] Arturo speech interrupted")
                    self.model_buffer = ""
                    self.model_turn_pcm.clear()
                    await self.client_ws.send_json({"event": "interrupted"})

                # Native live OUTPUT transcription (Live API output_audio_transcription):
                # streams Arturo's words word-by-word as he speaks -> live captions.
                # Accumulated in model_buffer and committed as a whole turn on
                # turnComplete (below). User captions are on-device (see setup note).
                output_tx = server_content.get("outputTranscription")
                if output_tx and output_tx.get("text"):
                    otext = output_tx["text"]
                    self.model_buffer += otext
                    await self.client_ws.send_json(
                        {"event": "transcript", "text": otext, "role": "arturo", "partial": True}
                    )

                model_turn = server_content.get("modelTurn", {})

                for part in model_turn.get("parts", []):
                    if part.get("thought", False):
                        continue
                    if "inlineData" in part:
                        audio_b64 = part["inlineData"].get("data", "")
                        pcm_bytes = base64.b64decode(audio_b64)
                        self.model_turn_pcm.extend(pcm_bytes)
                        await self.client_ws.send_bytes(pcm_bytes)

                    if "text" in part:
                        t = part["text"]
                        if t.startswith("**") and "**" in t[2:]:
                            t = t.split("**", 2)[-1].strip()
                        if t:
                            self.model_buffer += t
                            await self.client_ws.send_json({"event": "transcript", "text": t, "role": "arturo"})

                    if "functionCall" in part:
                        call = part["functionCall"]
                        fn_name = call.get("name", "")
                        fn_args = call.get("args", {})
                        call_id = call.get("id", str(uuid.uuid4()))
                        log.info(f"[{self.session_id}] Tool call: {fn_name}({fn_args})")
                        await self.client_ws.send_json({"event": "tool_start", "name": fn_name, "args": fn_args})

                        tool_turn_idx = len(self.turns)
                        self.turns.append({
                            "role": "tool",
                            "tool": fn_name,
                            "name": fn_name,
                            "input": fn_args,
                            "status": "running",
                            "ts": time.time()
                        })
                        self._write_live_call_journal(status="live")

                        result_str = ""
                        try:
                            if execute_tool_fn:
                                result_str = str(execute_tool_fn(fn_name, fn_args))
                            else:
                                result_str = f"Tool {fn_name} executed."
                        except Exception as e:
                            result_str = f"Tool execution error: {e}"

                        self.turns[tool_turn_idx] = {
                            "role": "tool",
                            "tool": fn_name,
                            "name": fn_name,
                            "input": fn_args,
                            "result": result_str[:500],
                            "status": "done",
                            "ts": time.time()
                        }
                        self._write_live_call_journal(status="live")
                        await self.client_ws.send_json({"event": "tool_done", "name": fn_name, "result": result_str[:200]})

                        tool_resp = {
                            "tool_response": {
                                "function_responses": [
                                    {
                                        "response": {"output": result_str},
                                        "id": call_id
                                    }
                                ]
                            }
                        }
                        await gemini_ws.send_str(json.dumps(tool_resp))

                if server_content.get("turnComplete"):
                    # Commit Arturo's streamed transcription (from outputTranscription)
                    # as a whole turn. User turns are journaled separately via the
                    # client's on-device {event:"user_turn"} path in _client_to_gemini.
                    model_text = self.model_buffer.strip()
                    if model_text:
                        self.turns.append({"role": "arturo", "text": model_text, "ts": time.time()})
                        self._write_live_call_journal(status="live")
                    self.model_buffer = ""
                    self.model_turn_pcm.clear()
                    self.user_turn_pcm.clear()

                    await self.client_ws.send_json({"event": "turn_complete"})

        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.warning(f"[{self.session_id}] gemini_to_client exception: {e}")

    def _finalize_call(self):
        duration_s = round(time.time() - self.start_time, 1)
        log.info(f"[{self.session_id}] Call finalized. Duration: {duration_s}s, Turns: {len(self.turns)}")
        self._write_live_call_journal(status="ended")

        if _attempt_gm_inject_fn and self.turns:
            try:
                _attempt_gm_inject_fn(f"{self.session_id}.json")
            except Exception as e:
                log.warning(f"Failed to inject call to GM: {e}")
