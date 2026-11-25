"""Jev (TypeSafe System One) for OrchestraOS: typed snap judgments at ~300 ms, fractions of a cent.

Port of disler/ten-levels-of-jev (core + levels 6-10) to stdlib Python.

  types / helpers / mock / client   wire contract, builders, offline mock, live transport
  guard     Level 6   PreToolUse bash + write gates, PostToolUse injection screen
  compact   Level 7   four-question turn-boundary evaluator -> silent/notice/recommend/request
  files     Level 8-9 ask_jev_file / ask_jev_files: judgments about files, never the files
  ask_jev   Level 10  ask_jev over agent state + paths + a read-only command

Entry points live outside the package (scripts/jev_hook.py, scripts/jev_cli.py) because
this package has a module named `types`; never execute a file in here directly.
Numbers, counting and string checks stay in Python; Jev only answers semantic questions.
"""
from .client import ContractError, JevClient, JevError, default_decide, shared_jev  # noqa: F401
from .helpers import choice, noul, score  # noqa: F401
from .mock import MockJev  # noqa: F401
from .types import LIMITS, QuestionValidationError, validate_questions, validate_request  # noqa: F401
