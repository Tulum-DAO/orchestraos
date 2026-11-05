"""attention_config.py — the `[attention]` section of orchestra.toml, with defaults. Pure loader.

    [attention]
    detector_enabled = true            # also: <data dir>/state/attention/DISARM present => detector off (rules too)
    detector_interval_s = 60           # floor 10
    clear_after_ticks = 2              # a condition must be absent this many SUCCESSFUL scans before it resolves
    failed_reasons = ["crashed", "usage_limit", "signed_out"]
    legacy_completion_cards = true     # `orchestra update done` also files a linked completion card

There are deliberately no "stuck after X minutes" thresholds here: how long is too long belongs to the
person, as timeout rules (attention_rules.py), not to the install.

legacy_completion_cards is TEMPORARY: it exists only until the phone and watch read /attention (part 5 of
the attention work). Until then the installed apps only know done notes as completion cards, so turning it
off early hides them there. Remove the setting, and the linked card, in part 5.

Every bad or missing value falls back to its default; nothing here ever raises.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

FAILED_REASONS = ("crashed", "usage_limit", "signed_out")


@dataclass(frozen=True)
class AttentionConfig:
    detector_enabled: bool = True
    detector_interval_s: int = 60
    clear_after_ticks: int = 2
    failed_reasons: tuple = FAILED_REASONS
    legacy_completion_cards: bool = True


def _int(v, default, floor):
    if isinstance(v, bool) or not isinstance(v, int):
        return default
    return max(floor, v)


def _bool(v, default):
    return v if isinstance(v, bool) else default


def load(raw: dict | None = None) -> AttentionConfig:
    """The config from `raw` (a parsed orchestra.toml), or from the install's file when None."""
    if raw is None:
        try:
            import notify_policy
            raw = notify_policy._config()
        except Exception as e:  # noqa: BLE001 — an unreadable config is the defaults, never a crash
            print(f"[attention_config] orchestra.toml unreadable, using defaults: {e}", file=sys.stderr)
            raw = {}
    sec = raw.get("attention") if isinstance(raw, dict) else None
    sec = sec if isinstance(sec, dict) else {}
    d = AttentionConfig()
    reasons = sec.get("failed_reasons")
    if isinstance(reasons, list):
        reasons = tuple(r for r in reasons if r in FAILED_REASONS)
    else:
        reasons = d.failed_reasons
    return AttentionConfig(
        detector_enabled=_bool(sec.get("detector_enabled"), d.detector_enabled),
        detector_interval_s=_int(sec.get("detector_interval_s"), d.detector_interval_s, 10),
        clear_after_ticks=_int(sec.get("clear_after_ticks"), d.clear_after_ticks, 1),
        failed_reasons=reasons,
        legacy_completion_cards=_bool(sec.get("legacy_completion_cards"), d.legacy_completion_cards),
    )


def detector_on(cfg: AttentionConfig, data_dir) -> bool:
    """The detector (and the timeout-rule engine it drives) runs only when enabled and not disarmed by file."""
    try:
        disarmed = (Path(data_dir) / "state" / "attention" / "DISARM").exists()
    except OSError:
        disarmed = True                  # cannot tell: stay off, the safe side for a background writer
    return bool(cfg.detector_enabled) and not disarmed
