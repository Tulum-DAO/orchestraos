#!/usr/bin/env python3
"""quota_oracle.py — Multi-Account Quota & Adaptive Runtime Oracle for OrchestraOS.

Tracks quota, token limits, and reset timestamps across multiple LLM provider
accounts (Claude primary/secondary, Gemini Flash, OpenAI/Codex). Provides
adaptive fallback routing for agent spawners so throttled accounts never fail
spawns or burn inactive credits.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent                                   # the checkout (code)
# quotas live in the DATA dir (orchestra.toml [data] dir, exported as ORCHESTRA_DIR by
# `orchestra up` / orchestra-env.sh); the checkout is the fallback for a bare run.
_STATE_DIR = Path(os.environ.get("ORCHESTRA_DIR") or os.environ.get("ORCH_DIR") or _ROOT) / "state"
_QUOTA_FILE = _STATE_DIR / "provider_quotas.json"

DEFAULT_QUOTAS = {
    "accounts": {
        "claude_primary": {
            "provider": "claude",
            "runtime": "claude",
            "model": "claude-opus-4-8[1m]",
            "status": "healthy",
            "resets_at": None,
            "note": "Primary Claude Code account (resets Sunday 10pm EST)"
        },
        "claude_secondary": {
            "provider": "claude",
            "runtime": "claude",
            "model": "claude-opus-4-8[1m]",
            "status": "healthy",
            "resets_at": None,
            "note": "Secondary Claude Code account (resets daily 6am EST)"
        },
        "gemini_flash": {
            "provider": "gemini",
            "runtime": "gemini",
            "model": "gemini-3.7-flash",
            "status": "healthy",
            "resets_at": None,
            "unlimited": True,
            "note": "Google Antigravity / Gemini 3.7 Flash runtime"
        },
        "codex_astra": {
            "provider": "codex",
            "runtime": "codex",
            "model": "gpt-6-astra",
            "status": "healthy",
            "resets_at": None,
            "note": "Codex / OpenAI GPT-6 Astra runtime"
        }
    },
    "default_runtime_priority": ["claude", "gemini", "codex"]
}


def load_quotas() -> Dict[str, Any]:
    if not _QUOTA_FILE.exists():
        save_quotas(DEFAULT_QUOTAS)
        return DEFAULT_QUOTAS
    try:
        data = json.loads(_QUOTA_FILE.read_text())
        return data
    except Exception:
        return DEFAULT_QUOTAS


def save_quotas(data: Dict[str, Any]) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    _QUOTA_FILE.write_text(json.dumps(data, indent=2))


def is_account_healthy(account_data: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    status = account_data.get("status", "healthy")
    if status == "healthy":
        return True
    resets_at_str = account_data.get("resets_at")
    if resets_at_str:
        try:
            resets_at = datetime.fromisoformat(resets_at_str)
            now = now or datetime.now(timezone.utc)
            if now >= resets_at:
                return True
        except Exception:
            pass
    return False


def resolve_adaptive_runtime(
    requested_runtime: Optional[str] = None,
    requested_model: Optional[str] = None,
    tier: Optional[str] = "T2"
) -> Tuple[str, str, Dict[str, Any]]:
    """Resolves the best available runtime and model based on current quota health.
    
    Returns: (runtime, model, metadata)
    """
    quotas = load_quotas()
    accounts = quotas.get("accounts", {})
    
    # Check if requested runtime has any healthy account
    if requested_runtime:
        matching = [
            (k, acc) for k, acc in accounts.items()
            if acc.get("runtime") == requested_runtime
        ]
        healthy_matching = [
            (k, acc) for k, acc in matching
            if is_account_healthy(acc)
        ]
        if healthy_matching:
            best_k, best_acc = healthy_matching[0]
            model = requested_model or best_acc.get("model", "default")
            return requested_runtime, model, {"account": best_k, "adapted": False}

    # If requested is exhausted or unspecified, fallback adaptively
    for target_rt in quotas.get("default_runtime_priority", ["gemini", "codex", "claude"]):
        for k, acc in accounts.items():
            if acc.get("runtime") == target_rt and is_account_healthy(acc):
                return target_rt, acc.get("model", "default"), {
                    "account": k,
                    "adapted": (requested_runtime is not None and requested_runtime != target_rt),
                    "original_runtime": requested_runtime
                }
                
    # Default fallback to gemini if all else fails
    return "gemini", "gemini-3.7-flash", {"account": "gemini_flash", "adapted": True, "forced": True}


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "resolve":
        rt = sys.argv[2] if len(sys.argv) > 2 else None
        md = sys.argv[3] if len(sys.argv) > 3 else None
        resolved_rt, resolved_md, meta = resolve_adaptive_runtime(rt, md)
        print(json.dumps({"runtime": resolved_rt, "model": resolved_md, "meta": meta}, indent=2))
    elif len(sys.argv) > 1 and sys.argv[1] == "status":
        q = load_quotas()
        print(json.dumps(q, indent=2))
    else:
        print("Usage: quota_oracle.py [resolve <runtime> <model> | status]")
