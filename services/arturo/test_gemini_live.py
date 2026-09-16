#!/usr/bin/env python3
import asyncio
import json
import os
import sys
import aiohttp
from pathlib import Path

def get_gemini_api_key():
    secrets_file = Path.home() / "scripts" / "agent-orchestra" / ".env.secrets"
    if secrets_file.exists():
        for line in secrets_file.read_text().splitlines():
            if line.startswith("GEMINI_API_KEY="):
                return line.split("=", 1)[1].strip()
    return os.environ.get("GEMINI_API_KEY", "")

async def main():
    api_key = get_gemini_api_key()
    if not api_key:
        print("FAIL: No GEMINI_API_KEY")
        return 1

    url = f"wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent?key={api_key}"
    print(f"Connecting to Gemini Live WebSocket...")

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(url) as ws:
            print("Connected! Sending setup frame...")
            setup_msg = {
                "setup": {
                    "model": "models/gemini-2.5-flash-native-audio-latest",
                    "generation_config": {
                        "response_modalities": ["AUDIO"],
                        "speech_config": {
                            "voice_config": {
                                "prebuilt_voice_config": {
                                    "voice_name": "Fenrir"
                                }
                            }
                        },
                        "thinking_config": {
                            "thinking_budget": 0
                        }
                    },
                    "system_instruction": {
                        "parts": [{"text": "You are Arturo, the operator's AI Operations Commander and voice co-pilot in OrchestraOS. Speak in 1 natural spoken sentence max. NEVER speak internal thoughts or use markdown."}]
                    },
                    "tools": [
                        {
                            "function_declarations": [
                                {
                                    "name": "knowledge",
                                    "description": "Look up information about the system",
                                    "parameters": {
                                        "type": "OBJECT",
                                        "properties": {
                                            "query": {"type": "STRING", "description": "The search query"}
                                        },
                                        "required": ["query"]
                                    }
                                }
                            ]
                        }
                    ]
                }
            }
            await ws.send_str(json.dumps(setup_msg))
            
            # Wait for setup_complete
            msg = await ws.receive()
            raw_data = msg.data.decode('utf-8') if isinstance(msg.data, (bytes, bytearray)) else msg.data
            data = json.loads(raw_data)
            print("Setup response:", data)
            assert "setupComplete" in data

            # Send a prompt turn
            print("Sending initial greeting turn: 'Hello Arturo. the operator is connected on voice right now. Give a brief, natural 1-sentence opening greeting to the operator.'...")
            client_turn = {
                "client_content": {
                    "turns": [
                        {
                            "role": "user",
                            "parts": [{"text": "Hello Arturo. the operator is connected on voice right now. Give a brief, natural 1-sentence opening greeting to the operator."}]
                        }
                    ],
                    "turn_complete": True
                }
            }
            await ws.send_str(json.dumps(client_turn))

            # Receive audio response
            audio_bytes_total = 0
            text_response = ""
            while True:
                msg = await ws.receive()
                if msg.type in (aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY):
                    raw = msg.data.decode('utf-8') if isinstance(msg.data, (bytes, bytearray)) else msg.data
                    resp = json.loads(raw)
                    server_content = resp.get("serverContent", {})
                    model_turn = server_content.get("modelTurn", {})
                    for part in model_turn.get("parts", []):
                        if "text" in part:
                            text_response += part["text"]
                        if "inlineData" in part:
                            # Audio chunk (PCM 24kHz)
                            import base64
                            b = base64.b64decode(part["inlineData"].get("data", ""))
                            audio_bytes_total += len(b)
                    if server_content.get("turnComplete"):
                        print(f"Turn complete! Received {audio_bytes_total} bytes of 24kHz PCM audio.")
                        if text_response:
                            print(f"Model text: {text_response}")
                        break
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    print("WS closed:", msg)
                    break

            print("Gemini Live API test passed successfully!")
            return 0

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
