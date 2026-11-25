"""Levels 8 and 9: a judgment about a file, never the file.

Port of ten-levels-of-jev src/levels/level08 + level09. Code reads the file into Jev's
state; the agent only ever receives the typed answer. Token burn for scouting 50 files
drops from "read 50 files into a 1M context" to 50 small answers.

  ask_jev_file_bool    one file, one yes/no                 -> {path, answer, noul}
  ask_jev_file_choice  one file, one pick of agent options  -> {path, choice, confidence, probabilities}
  ask_jev_file_score   one file, one position on levels     -> {path, score, top, nearest, confidence, legend}
  ask_jev_file         any of the three, by question type (the single MCP-facing entry)
  ask_jev_files        raw question JSON over globs/dirs/paths, one call per file, in parallel
  pick_first_file      one Choice over the survivors: which file to open first

Paths, globs, size, binary and the 255 cap are all decided in code before any call.
Nothing outside `cwd` (after resolving symlinks) is ever read.
"""
from __future__ import annotations

import glob as _glob
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

from .client import JevError, default_decide
from .helpers import choice, noul, score
from .types import LIMITS, Questions, State, validate_questions

Decide = Callable[[State, Questions], Dict[str, Any]]

# Roughly four characters per token; the budget is shared with the questions, so leave room.
MAX_FILE_CHARS = (LIMITS.TOTAL_TOKEN_BUDGET - 4000) * 4
SKIP_DIRS = {"node_modules", ".git", ".sessions", "dist", "build", "coverage", ".pi", "__pycache__", ".venv", "venv"}
_GLOB_CHARS = re.compile(r"[*?\[\]{}]")
_BINARY_EXT = re.compile(r"\.(png|jpe?g|gif|webp|ico|pdf|zip|gz|tgz|woff2?|ttf|mp[34]|mov|lock|pyc|so|db|sqlite3?)$", re.I)
DEFAULT_CONCURRENCY = 16


class FileStateError(Exception):
    def __init__(self, message: str, path: str):
        super().__init__(message)
        self.path = path


def _full(path: str, cwd: str) -> str:
    return path if os.path.isabs(path) else os.path.join(cwd, path)


def is_inside(path: str, cwd: str) -> bool:
    rel = os.path.relpath(os.path.realpath(_full(path, cwd)), os.path.realpath(cwd))
    return rel != "." and not rel.startswith("..") and not os.path.isabs(rel)


def read_file_state(path: str, cwd: str) -> Dict[str, str]:
    """Read one file as Jev state. Errors name the path and the reason, per file."""
    full = _full(path, cwd)
    if not is_inside(path, cwd):
        raise FileStateError(f"outside the workspace: {path}", path)
    try:
        info = os.stat(full)
    except OSError:
        raise FileStateError(f"not found: {path}", path)
    if not os.path.isfile(full):
        raise FileStateError(f"not a file: {path}", path)
    if info.st_size > MAX_FILE_CHARS:
        raise FileStateError(
            f"too large for one Jev call: {path} is {info.st_size} bytes, the limit is {MAX_FILE_CHARS}", path)
    with open(full, "rb") as fh:
        buf = fh.read()
    if b"\0" in buf[:8192]:
        raise FileStateError(f"binary: {path}", path)
    return {"path": path, "content": buf.decode("utf-8", errors="replace")}


# ---------------------------------------------------------------- Level 8

def ask_file_bool(path: str, question: str, cwd: str, yes: Optional[str] = None, no: Optional[str] = None,
                  decide: Decide = default_decide) -> Dict[str, Any]:
    state = read_file_state(path, cwd)
    q = {"answer": noul(question, {"true": yes, "false": no} if (yes or no) else None)}
    result = decide(state, q)
    p = result["answers"]["answer"]["noul"]
    return {"path": path, "answer": p > 0.5, "noul": p, "usage": result.get("usage")}


def with_exit(options: Dict[str, Optional[str]]) -> Dict[str, Optional[str]]:
    """An `other` exit is added when the agent leaves none, so the model is never forced."""
    criteria = dict(options)
    if not any(k in criteria for k in ("other", "none", "none_of_the_above")):
        criteria["other"] = "None of the above"
    return criteria


def ask_file_choice(path: str, question: str, options: Dict[str, Optional[str]], cwd: str,
                    decide: Decide = default_decide) -> Dict[str, Any]:
    state = read_file_state(path, cwd)
    result = decide(state, {"answer": choice(question, with_exit(options))})
    a = result["answers"]["answer"]
    return {"path": path, "choice": a["choice"], "confidence": a["confidence"],
            "probabilities": a["probabilities"], "usage": result.get("usage")}


def ask_file_score(path: str, question: str, levels: List[str], cwd: str,
                   decide: Decide = default_decide) -> Dict[str, Any]:
    state = read_file_state(path, cwd)
    result = decide(state, {"answer": score(question, levels)})
    a = result["answers"]["answer"]
    top = len(levels) - 1
    nearest = a["legend"].get(str(int(a["score"] + 0.5)), "")
    return {"path": path, "score": a["score"], "top": top, "nearest": nearest,
            "confidence": a["confidence"], "legend": a["legend"], "usage": result.get("usage")}


def ask_jev_file(path: str, question: str, cwd: str, kind: str = "bool",
                 options: Optional[Dict[str, Optional[str]]] = None, levels: Optional[List[str]] = None,
                 yes: Optional[str] = None, no: Optional[str] = None, decide: Decide = default_decide) -> Dict[str, Any]:
    """One tool over the three Level 8 shapes. kind: bool | choice | score."""
    if kind == "bool":
        return ask_file_bool(path, question, cwd, yes, no, decide)
    if kind == "choice":
        if not options:
            raise ValueError("kind=choice needs options: {name: description}")
        return ask_file_choice(path, question, options, cwd, decide)
    if kind == "score":
        if not levels:
            raise ValueError("kind=score needs levels: [low, ..., high]")
        return ask_file_score(path, question, levels, cwd, decide)
    raise ValueError(f"unknown kind {kind!r}; use bool, choice, or score")


# ---------------------------------------------------------------- Level 9

def expand_patterns(patterns: List[str], cwd: str, recursive: bool = False) -> List[str]:
    """Globs, files, or directories. A directory means its files, or everything below when recursive."""
    out = set()
    for raw in patterns:
        pattern = raw.strip()
        if not pattern:
            continue
        if _GLOB_CHARS.search(pattern):
            out.update(_glob.glob(pattern, root_dir=cwd, recursive=True))
            continue
        full = _full(pattern, cwd)
        if not os.path.exists(full):
            out.add(pattern)  # let prune report it
            continue
        if os.path.isfile(full):
            out.add(pattern)
            continue
        base = pattern.rstrip("/")
        sub = f"{base}/**/*" if recursive else f"{base}/*"
        out.update(_glob.glob(sub, root_dir=cwd, recursive=recursive))
    return sorted(out)


def prune_files(paths: List[str], cwd: str, cap: int = LIMITS.MAX_CHOICE_OPTIONS) -> Dict[str, List[Any]]:
    """What survives, and why each dropped file dropped. Nothing here calls Jev."""
    files: List[str] = []
    skipped: List[Dict[str, str]] = []
    for path in paths:
        full = _full(path, cwd)
        if not is_inside(path, cwd):
            skipped.append({"path": path, "reason": "outside the repo"})
            continue
        rel = os.path.relpath(full, cwd)
        if any(part in SKIP_DIRS for part in rel.split(os.sep)):
            skipped.append({"path": path, "reason": "skipped directory"})
            continue
        try:
            info = os.stat(full)
        except OSError:
            skipped.append({"path": path, "reason": "not found"})
            continue
        if not os.path.isfile(full):
            continue
        if info.st_size == 0:
            skipped.append({"path": path, "reason": "empty"})
            continue
        if info.st_size > MAX_FILE_CHARS:
            skipped.append({"path": path, "reason": f"too large, {info.st_size} bytes"})
            continue
        if _BINARY_EXT.search(path):
            skipped.append({"path": path, "reason": "binary or lock file"})
            continue
        if len(files) >= cap:
            skipped.append({"path": path, "reason": f"over the {cap} file cap; narrow the pattern"})
            continue
        files.append(path)
    return {"files": files, "skipped": skipped}


def parse_questions(questions_json: Any) -> Questions:
    """The agent writes Jev's question shape as JSON. Malformed blocks fail here, before any read."""
    if isinstance(questions_json, str):
        try:
            parsed = json.loads(questions_json)
        except ValueError as err:
            raise ValueError(f"questions_json is not valid JSON: {err}")
    else:
        parsed = questions_json
    if not isinstance(parsed, dict):
        raise ValueError("questions_json must be an object keyed by question id")
    validate_questions(parsed)
    return parsed


def ask_files(patterns: List[str], questions_json: Any, cwd: str, recursive: bool = False,
              concurrency: int = DEFAULT_CONCURRENCY, decide: Decide = default_decide,
              on_result: Optional[Callable[[Dict[str, Any]], None]] = None) -> Dict[str, Any]:
    """One call per file, every question in each, in parallel with a cap. Answers per path."""
    questions = parse_questions(questions_json)
    pruned = prune_files(expand_patterns(patterns, cwd, recursive), cwd)
    skipped = list(pruned["skipped"])

    def one(path: str):
        try:
            state = read_file_state(path, cwd)
        except FileStateError as err:
            return None, {"path": path, "reason": str(err)}
        try:
            result = decide(state, questions)
        except JevError as err:  # one failed call drops one file, with the reason
            return None, {"path": path, "reason": f"call failed: {err}"}
        r = {"path": path, "answers": result["answers"], "usage": result.get("usage")}
        if on_result:
            on_result(r)
        return r, None

    results = []
    if pruned["files"]:
        with ThreadPoolExecutor(max_workers=max(1, min(concurrency, len(pruned["files"])))) as pool:
            for r, skip in pool.map(one, pruned["files"]):
                if r:
                    results.append(r)
                else:
                    skipped.append(skip)
    results.sort(key=lambda r: r["path"])
    return {"results": results, "skipped": skipped, "calls": len(results)}


def pick_question(question: str, candidates: List[Dict[str, Any]]) -> Questions:
    """Keys are the paths themselves, so the pick is always a real file. `none` is the exit."""
    criteria: Dict[str, Optional[str]] = {}
    for c in candidates[: LIMITS.MAX_CHOICE_OPTIONS - 1]:
        criteria[c["path"]] = c.get("note")
    criteria["none"] = "No file in the list fits"
    return {"pick": choice(question, criteria)}


def pick_first_file(question: str, candidates: List[Dict[str, Any]], decide: Decide = default_decide,
                    floor: float = 0.3) -> Dict[str, Any]:
    if not candidates:
        return {"path": None, "confidence": 0, "probabilities": {}}
    state = {"question": question, "files": [c["path"] for c in candidates]}
    a = decide(state, pick_question(question, candidates))["answers"]["pick"]
    path = None if a["choice"] == "none" or a["confidence"] < floor else a["choice"]
    return {"path": path, "confidence": a["confidence"], "probabilities": a["probabilities"]}
