import pytest
from datetime import datetime
from scripts.lineage_daemon.adapters.claude_adapter import ClaudeAdapter
from scripts.lineage_daemon.adapters.gemini_adapter import GeminiAdapter
from scripts.lineage_daemon.adapters.codex_adapter import CodexAdapter
from scripts.lineage_daemon.adapters.protocol import ProviderIdentity

def test_claude_adapter():
    adapter = ClaudeAdapter()
    
    obs = adapter.observe_context("seat1")
    assert obs.token_count == 100000
    
    identity = adapter.resolve_declared_identity("alias1")
    assert identity.alias == "alias1"
    
    cmd = adapter.resume_command(identity)
    assert "claude --resume" in cmd.command
    assert "--dangerously-skip-permissions" in cmd.command
    
    transcript = adapter.locate_transcript(identity)
    assert transcript.provider == "claude"
    assert "project/transcripts" in transcript.transcript_path

def test_gemini_adapter():
    adapter = GeminiAdapter()
    
    obs = adapter.observe_context("seat1")
    assert obs.token_count == 500000
    
    identity = adapter.resolve_declared_identity("alias1")
    assert identity.alias == "alias1"
    
    cmd = adapter.resume_command(identity)
    assert "agy --conversation" in cmd.command
    
    transcript = adapter.locate_transcript(identity)
    assert transcript.provider == "gemini"
    assert "antigravity-cli/brain" in transcript.transcript_path

def test_codex_adapter():
    adapter = CodexAdapter()
    
    obs = adapter.observe_context("seat1")
    assert obs.token_count == 8000
    
    identity = adapter.resolve_declared_identity("alias1")
    assert identity.alias == "alias1"
    
    cmd = adapter.resume_command(identity)
    assert "codex --yolo resume" in cmd.command
    
    transcript = adapter.locate_transcript(identity)
    assert transcript.provider == "codex"
    assert "sessions" in transcript.transcript_path

def test_common_protocol_methods():
    for AdapterClass in [ClaudeAdapter, GeminiAdapter, CodexAdapter]:
        adapter = AdapterClass()
        
        receipt = adapter.spawn_alias("seat", "succ")
        assert receipt.is_alive
        
        delivery = adapter.deliver("sid", "payload", "key")
        assert delivery.verified_ack
        
        proc = adapter.observe_process("sid")
        assert proc.is_alive
        
        ret = adapter.retire_process("arch")
        assert ret.postcondition_verified
