"""JevClient: a provider and credential snapshot, with no runtime fallback.

Port of ten-levels-of-jev src/core/client.ts to stdlib Python.

Providers:
  typesafe    POST https://api.typesafe.ai/v1/systemone      key TYPESAFE_API_KEY    model jev-1.14.0 (pinned)
  openrouter  POST https://openrouter.ai/api/alpha/decisions  key OPENROUTER_API_KEY  model ~typesafe/jev-latest
  mock        deterministic offline MockJev (no network, cost 0)

Selection order: explicit provider= > test isolation (pytest -> mock unless JEV_LIVE=1)
> JEV_BACKEND > TYPESAFE_API_KEY > OPENROUTER_API_KEY. A missing live credential fails
closed (raises); nothing silently falls back to the mock.

Credentials come from the environment first, then from the gitignored orchestra .env
(JEV_DOTENV, else $ORCHESTRA_DIR/.env, else ~/.orchestra/.env). Seats do not
carry the key in their own env. Keys are never logged, printed, or put in results.
"""
from __future__ import annotations

import copy
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Dict, List, Optional

from .mock import MockJev
from .types import QuestionValidationError, Questions, State, validate_request

PROVIDERS = ("mock", "openrouter", "typesafe")

ENDPOINTS = {
    "openrouter": "https://openrouter.ai/api/alpha/decisions",
    "typesafe": "https://api.typesafe.ai/v1/systemone",
}
# The typesafe default is PINNED to the tested model id, not the moving "jev-latest" alias: a guard
# that gates tool calls must not change judgment because the vendor moved an alias. A response for
# an exact id must come from that id (see check_served_model). Moving the pin is a reviewed change.
PINNED_MODEL = "jev-1.14.0"
DEFAULT_MODELS = {"openrouter": "~typesafe/jev-latest", "typesafe": PINNED_MODEL}
KEY_ENV = {"openrouter": "OPENROUTER_API_KEY", "typesafe": "TYPESAFE_API_KEY"}

RETRY_STATUSES = {429, 502, 503, 529}
REQUEST_TIMEOUT_MS = 30_000
MAX_ATTEMPTS = 3
MAX_BACKOFF_MS = 8000


class JevError(Exception):
    """Base for every failure of a Jev call itself (config, transport, HTTP, contract, timeout).

    Hooks catch exactly this to decide fail-open vs fail-closed. A bug in caller code
    (KeyError, TypeError, QuestionValidationError) is NOT a JevError and must surface.
    """


class JevConfigError(JevError, RuntimeError):
    """No credentials, or an unknown backend was selected."""


class JevTransportError(JevError):
    """The endpoint could not be reached (DNS, refused, TLS, reset)."""


class ContractError(JevError):
    """The provider answered with something that breaks the wire contract."""


class ModelMismatchError(ContractError):
    """An exact model id was requested and a different one answered."""


def is_alias(model: str) -> bool:
    """A moving name ("jev-latest", "~typesafe/jev-latest") rather than one exact model id."""
    return model.startswith("~") or model.endswith("latest")


def check_served_model(requested: str, served: str) -> None:
    """Refuse an answer from a model other than the exact one asked for. An alias request has
    nothing to compare against and is accepted as served."""
    if not is_alias(requested) and served != requested:
        raise ModelMismatchError(f"Requested model {requested!r} but {served!r} answered; refused.")


class JevHTTPError(JevError):
    def __init__(self, provider: str, status: int, hint: str = ""):
        super().__init__(f"{provider} HTTP {status}.{hint}")
        self.provider, self.status = provider, status


class JevTimeout(JevError):
    """The total budget (including retries) ran out."""


def _isolated() -> bool:
    # Tests cannot accidentally use ambient live credentials or overrides.
    in_pytest = "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules
    return in_pytest and os.environ.get("JEV_LIVE") != "1"


def select_provider(explicit: Optional[str] = None) -> str:
    selected = explicit
    if selected is None:
        selected = "mock" if _isolated() else ((os.environ.get("JEV_BACKEND") or "").strip() or None)
    if selected is not None:
        if selected in PROVIDERS:
            return selected
        raise JevConfigError(f'Unknown JEV backend "{selected}"; use mock, openrouter, or typesafe.')
    if _env_or_dotenv("TYPESAFE_API_KEY"):
        return "typesafe"
    if _env_or_dotenv("OPENROUTER_API_KEY"):
        return "openrouter"
    raise JevConfigError(
        "No Jev credentials: set TYPESAFE_API_KEY or OPENROUTER_API_KEY, or select "
        "provider='mock' / JEV_BACKEND=mock for offline use.")


def dotenv_path() -> str:
    explicit = os.environ.get("JEV_DOTENV")
    if explicit:
        return explicit
    base = os.environ.get("ORCHESTRA_DIR") or os.path.expanduser("~/.orchestra")
    return os.path.join(base, ".env")


def _read_dotenv_key(name: str, path: str) -> Optional[str]:
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("export "):
                    line = line[len("export "):].lstrip()
                if not line.startswith(name + "="):
                    continue
                value = line.split("=", 1)[1].strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                return value.strip() or None
    except OSError:
        return None
    return None


def _env_or_dotenv(name: str) -> Optional[str]:
    value = (os.environ.get(name) or "").strip()
    if value:
        return value
    if _isolated():
        return None
    return _read_dotenv_key(name, dotenv_path())


def _nonneg(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x == x and x not in (
        float("inf"), float("-inf")) and x >= 0


def _unit(x: Any) -> bool:
    return _nonneg(x) and x <= 1


def _token_count(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool) and x >= 0


def validate_response(response: Any, questions: Questions) -> None:
    """Strict live contract; unknown payload fields are deliberately retained."""
    if not isinstance(response, dict) or not isinstance(response.get("answers"), dict) \
            or not isinstance(response.get("model"), str) or not response["model"].strip():
        raise ContractError("Invalid response envelope: expected model and answers.")
    usage = response.get("usage")
    if not isinstance(usage, dict) or not _token_count(usage.get("input_tokens")) \
            or not _token_count(usage.get("output_tokens")):
        raise ContractError("Invalid response usage: expected nonnegative integer input_tokens and output_tokens.")
    answers = response["answers"]
    for qid, q in questions.items():
        a = answers.get(qid)
        if qid not in answers or not isinstance(a, dict) or a.get("type") != q["type"]:
            raise ContractError(f"Missing or mismatched answer: {qid}")
        if q["type"] == "noul":
            if not _unit(a.get("noul")):
                raise ContractError(f"Invalid noul: {qid}")
            continue
        probs = a.get("probabilities")
        if not _unit(a.get("confidence")) or not isinstance(probs, dict):
            raise ContractError(f"Invalid distribution: {qid}")
        keys = list(q["criteria"].keys()) if q["type"] == "choice" else [str(i) for i in range(len(q["criteria"]))]
        if len(probs) != len(keys) or not all(k in probs and _unit(probs[k]) for k in keys):
            raise ContractError(f"Distribution keys must match the declared criteria: {qid}")
        total = sum(probs[k] for k in keys)
        if abs(total - 1) > 0.025:
            raise ContractError(f"Distribution does not sum to one: {qid} ({total})")
        if q["type"] == "choice" and (not isinstance(a.get("choice"), str) or a["choice"] not in keys):
            raise ContractError(f"Undeclared choice returned: {qid}")
        if q["type"] == "score":
            if not _nonneg(a.get("score")) or a["score"] > len(keys) - 1:
                raise ContractError(f"Score out of range: {qid}")
            legend = a.get("legend")
            if not isinstance(legend, dict) or len(legend) != len(keys) or not all(
                    k in legend and legend[k] == q["criteria"][i] for i, k in enumerate(keys)):
                raise ContractError(f"Score legend must match the declared criteria: {qid}")


def retry_after_ms(header: Optional[str]) -> float:
    """Ignore malformed/negative values instead of letting them erase the backoff."""
    if not header or not header.strip():
        return 0
    value = header.strip()
    try:
        if value.replace(".", "", 1).isdigit():
            return float(value) * 1000
    except ValueError:
        return 0
    if not value[:3].lower() in ("mon", "tue", "wed", "thu", "fri", "sat", "sun"):
        return 0
    try:
        when = parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError, IndexError):
        return 0
    return max(0.0, (when - time.time()) * 1000)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        raise urllib.error.HTTPError(req.full_url, code, f"redirect refused: {msg}", headers, fp)


_OPENER = urllib.request.build_opener(_NoRedirect)


def _cost(response: Dict[str, Any], provider: str, pricing: Optional[Dict[str, float]]) -> Dict[str, Any]:
    if provider == "mock":
        return {"amount": 0, "source": "mock"}
    usage = response["usage"]
    if _nonneg(usage.get("cost")):
        return {"amount": usage["cost"], "source": "reported"}
    if pricing:
        amount = usage["input_tokens"] / 1e6 * pricing["input_per_million"] + \
            usage["output_tokens"] / 1e6 * pricing["output_per_million"]
        if _nonneg(amount):
            return {"amount": amount, "source": "estimated"}
    return {"amount": None, "source": "unknown"}


class JevClient:
    """One provider, fixed at construction. system_one(state, questions) -> result dict.

    The result is the provider payload (model, answers, usage, any extensions) plus
    `raw` (request/response snapshots, no credentials) and `meta` (provider,
    requested/resolved model, elapsed_ms, attempts, cost).
    """

    def __init__(self, provider: Optional[str] = None, api_key: Optional[str] = None,
                 base_url: Optional[str] = None, model: Optional[str] = None,
                 timeout_ms: int = REQUEST_TIMEOUT_MS, retry_delay_ms: float = 500,
                 pricing: Optional[Dict[str, float]] = None, max_attempts: int = MAX_ATTEMPTS):
        if api_key is not None and provider not in ("typesafe", "openrouter"):
            raise ValueError("api_key requires an explicit live provider (typesafe or openrouter).")
        self.provider = select_provider(provider)
        self.model = model
        if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or not 0 < timeout_ms <= 2_147_483_647:
            raise ValueError("timeout_ms must be a positive integer no greater than 2147483647.")
        if not _nonneg(retry_delay_ms):
            raise ValueError("retry_delay_ms must be finite and nonnegative.")
        if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer.")
        self.timeout_ms = timeout_ms
        self.retry_delay_ms = retry_delay_ms
        self.max_attempts = max_attempts
        if pricing is not None:
            if not isinstance(pricing, dict) or not _nonneg(pricing.get("input_per_million")) \
                    or not _nonneg(pricing.get("output_per_million")):
                raise ValueError("pricing requires finite, nonnegative input_per_million and output_per_million USD rates.")
            pricing = dict(pricing)
        self.pricing = pricing
        self.calls = 0
        self._mock = MockJev()
        self._listeners: List[Callable[[Dict[str, Any]], None]] = []
        self._api_key: Optional[str] = None
        self.endpoint: Optional[str] = None
        if self.provider == "mock":
            return
        key = api_key if api_key is not None else _env_or_dotenv(KEY_ENV[self.provider])
        self._api_key = (key or "").strip() or None
        self.endpoint = base_url or ENDPOINTS[self.provider]
        if not self._api_key:
            raise JevConfigError(f'Provider "{self.provider}" needs a nonblank api_key or {KEY_ENV[self.provider]}.')

    def __repr__(self) -> str:  # never show the key
        return f"JevClient(provider={self.provider!r}, endpoint={self.endpoint!r}, model={self.model!r})"

    @property
    def is_live(self) -> bool:
        return self.provider != "mock"

    def on(self, listener: Callable[[Dict[str, Any]], None]) -> Callable[[], None]:
        """Observe every request and response. Returns an unsubscribe function."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener) if listener in self._listeners else None

    def _emit(self, event: Dict[str, Any]) -> None:
        for listener in list(self._listeners):
            listener(event)

    def system_one(self, state: State, questions: Questions, model: Optional[str] = None) -> Dict[str, Any]:
        requested = model or self.model or (self._mock.model if self.provider == "mock" else DEFAULT_MODELS[self.provider])
        body = {"model": requested, "state": state, "questions": questions}
        validate_request(body)
        try:
            request_text = json.dumps(body, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            raise QuestionValidationError("Request must be JSON-serializable.")
        request = json.loads(request_text)
        validate_request(request)
        self.calls += 1
        started = time.monotonic()
        self._emit({"kind": "request", "provider": self.provider, "model": requested,
                    "state": state, "questions": questions})

        if self.provider == "mock":
            response = self._mock.system_one(request)
            out = self._with_meta(request, request_text, response, json.dumps(response), requested, started, 1)
            self._emit({"kind": "response", "result": out})
            return out

        deadline = started + self.timeout_ms / 1000
        for attempt in range(1, self.max_attempts + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise JevTimeout("Jev request timed out.")
            req = urllib.request.Request(
                self.endpoint, data=request_text.encode("utf-8"), method="POST",
                headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"})
            status, headers, text = self._post(req, remaining)
            if status in RETRY_STATUSES and attempt < self.max_attempts:
                backoff = self.retry_delay_ms * 2 ** (attempt - 1) * (1 + random.random() * 0.2)
                delay = min(MAX_BACKOFF_MS, max(backoff, retry_after_ms(headers.get("retry-after"))))
                if time.monotonic() + delay / 1000 >= deadline:
                    raise JevTimeout("Jev request timed out.")
                time.sleep(delay / 1000)
                continue
            if not 200 <= status < 300:
                hint = f" Check the {KEY_ENV[self.provider]} key." if status == 401 else (
                    " Check account credits." if status == 402 else "")
                raise JevHTTPError(self.provider, status, hint)
            try:
                response = json.loads(text)
            except ValueError:
                raise ContractError("Invalid response JSON.")
            validate_response(response, request["questions"])
            check_served_model(requested, response["model"])
            out = self._with_meta(request, request_text, response, text, requested, started, attempt)
            self._emit({"kind": "response", "result": out})
            return out
        raise JevError(f"{self.provider} retries exhausted")

    def _post(self, req: urllib.request.Request, remaining_s: float):
        try:
            with _OPENER.open(req, timeout=remaining_s) as resp:
                return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as err:
            if 300 <= err.code < 400:
                raise JevHTTPError(self.provider, err.code, " Redirects are refused.")
            headers = {k.lower(): v for k, v in (err.headers.items() if err.headers else [])}
            return err.code, headers, ""
        except TimeoutError:
            raise JevTimeout("Jev request timed out.")
        except urllib.error.URLError as err:
            if isinstance(err.reason, TimeoutError):
                raise JevTimeout("Jev request timed out.")
            raise JevTransportError(f"{self.provider} unreachable: {err.reason}")
        except (ConnectionError, OSError) as err:
            raise JevTransportError(f"{self.provider} connection failed: {type(err).__name__}")

    def _with_meta(self, request, request_text, response, response_text, requested, started, attempts):
        out = copy.deepcopy(response)
        out["raw"] = {"request": request, "request_text": request_text,
                      "response": response, "response_text": response_text}
        out["meta"] = {
            "provider": self.provider,
            "requested_model": requested,
            "resolved_model": response["model"],
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "attempts": attempts,
            "cost": _cost(response, self.provider, self.pricing),
        }
        return out


Decide = Callable[[State, Questions], Dict[str, Any]]

_shared: Optional[JevClient] = None


def shared_jev() -> JevClient:
    """Lazily constructed; provider and credentials resolve on first use."""
    global _shared
    if _shared is None:
        _shared = JevClient()
    return _shared


def reset_shared() -> None:
    global _shared
    _shared = None


def default_decide(state: State, questions: Questions) -> Dict[str, Any]:
    return shared_jev().system_one(state, questions)
