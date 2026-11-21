"""The only place LLM calls happen. Owned by AI engineering.

Backends (env LLM_BACKEND): "gemini" (default), "ollama" (local models), "fake" (offline, deterministic).
Model (env LLM_MODEL). Every call is timed, token-counted, costed and appended to CALL_LOG.
"""
from __future__ import annotations

import json
import os
import random
import re
import time
from dataclasses import dataclass, field
from typing import Optional, Type

from pydantic import BaseModel, ValidationError

# USD per 1M tokens (input, output). Fill in from Google's current pricing page before quoting cost.
# A model missing here -> cost reported as n/a (never a fake $0). Gemini 2.5 is retired for new keys.
# Source: https://aistudio.google.com/docs/pricing (ai.google.dev/gemini-api/docs/pricing redirects there),
# accessed 2026-11-21. Standard (paid) tier, text input. Output price includes thinking tokens. Batch API = 50% off.
# Not listed on that page: gemini-3.5-flash. Gemma 4: free tier only, no paid price (unverified) -> left out (n/a).
PRICES: dict = {
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.1-flash-lite": (0.25, 1.50),
}


@dataclass
class LLMCall:
    backend: str
    model: str               # the model that produced the answer (a fallback model if one was used)
    text: str
    parsed: Optional[BaseModel]
    parse_ok: bool
    latency_ms: float        # wall time including retries and backoff waits, excluding client-side pacing
    in_tokens: int
    out_tokens: int          # includes hidden "thinking" tokens: they are billed as output
    cost_usd: float
    error: Optional[str] = None
    error_kind: Optional[str] = None     # see ERROR_KINDS; None on success
    attempts: int = 1                    # API attempts across all models
    retry_wait_ms: float = 0.0           # time spent sleeping in backoff
    fallback_from: Optional[str] = None  # the requested model, when a fallback model answered
    errors_seen: list = field(default_factory=list)  # error_kind of every failed attempt, in order


CALL_LOG: list = []          # the eval harness reads and clears this

# Error kinds. TRANSIENT ones are retried with backoff on the same model, then the fallback models are tried.
# MODEL_LEVEL ones skip retries and go straight to the fallback models. FATAL ones stop immediately.
TRANSIENT = {"rate_limited", "unavailable", "timeout", "network"}
MODEL_LEVEL = {"model_not_found", "bad_request", "truncated", "blocked", "empty"}
FATAL = {"auth", "config"}
ERROR_KINDS = TRANSIENT | MODEL_LEVEL | FATAL | {"parse", "unknown"}


class LLMError(Exception):
    def __init__(self, kind: str, msg: str, retry_after: Optional[float] = None, in_tokens: int = 0,
                 out_tokens: int = 0):
        super().__init__(msg)
        self.kind, self.retry_after = kind, retry_after
        self.in_tokens, self.out_tokens = in_tokens, out_tokens  # billed even though the call failed


def _env_f(name, default):
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return float(default)


def default_backend() -> str:
    return os.getenv("LLM_BACKEND", "gemini")


def default_model() -> str:
    return os.getenv("LLM_MODEL", "gemini-3.5-flash-lite")


def fallback_models() -> list:
    """LLM_MODEL_FALLBACKS: comma-separated models tried, in order, when the main model fails."""
    return [m.strip() for m in os.getenv("LLM_MODEL_FALLBACKS", "").split(",") if m.strip()]


def classify(e: Exception) -> LLMError:
    """Map any backend exception to an LLMError with a kind. Never raises."""
    if isinstance(e, LLMError):
        return e
    code = getattr(e, "code", None) or getattr(e, "status_code", None)
    msg = f"{type(e).__name__}: {str(e)[:300]}"
    name, low = type(e).__name__.lower(), str(e).lower()
    if code == 429 or "resource_exhausted" in low:
        m = re.search(r"retry(?:delay|_delay)?['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)s", str(e), re.I)
        return LLMError("rate_limited", msg, float(m.group(1)) if m else None)
    if code in (500, 502, 503, 504):
        return LLMError("unavailable", msg)
    if code == 404:
        return LLMError("model_not_found", msg)
    if code in (401, 403):
        return LLMError("auth", msg)
    if code in (400, 422):
        return LLMError("bad_request", msg)
    if "timeout" in name or "timed out" in low or "deadline" in low:
        return LLMError("timeout", msg)
    if any(w in name for w in ("connect", "network", "urlerror", "remoteprotocol", "readerror")):
        return LLMError("network", msg)
    if "api key" in low or "credentials" in low:
        return LLMError("auth", msg)
    return LLMError("unknown", msg)


_last_call_at = 0.0


def _pace() -> float:
    """Client-side pacing: LLM_MIN_INTERVAL_S seconds between API calls (free-tier per-minute quotas).
    Returns seconds slept; that time is NOT counted as call latency."""
    global _last_call_at
    gap = _env_f("LLM_MIN_INTERVAL_S", 0) - (time.monotonic() - _last_call_at)
    if gap > 0:
        time.sleep(gap)
    _last_call_at = time.monotonic()
    return max(gap, 0.0)


def _call_backend(backend, system, user, schema, model, max_tokens, temperature):
    if backend == "gemini":
        return _gemini(system, user, schema, model, max_tokens, temperature)
    if backend == "ollama":
        return _ollama(system, user, schema, model, max_tokens, temperature)
    if backend == "fake":
        return _fake(system, user)
    raise LLMError("config", f"unknown backend {backend}")


def generate_json(system: str, user: str, schema: Type[BaseModel], model: Optional[str] = None,
                  backend: Optional[str] = None, max_output_tokens: int = 1024,
                  temperature: float = 0.0) -> LLMCall:
    """Call a model with a strict JSON schema. Never raises: errors come back in LLMCall.error / error_kind.

    Fallback ladder (env):
      LLM_MAX_RETRIES (default 2)     retries per model on transient errors (429, 5xx, timeout, network)
      LLM_BACKOFF_S (default 1)       first backoff; doubles each retry, plus jitter; honours a 429 retryDelay
      LLM_MAX_BACKOFF_S (default 8)   cap on any single wait
      LLM_DEADLINE_S (default 20)     stop retrying once this much wall time is spent (a live user is waiting)
      LLM_MODEL_FALLBACKS             models tried in order after the main model fails (e.g. a sibling model)
      LLM_TIMEOUT_S (default 30)      per-request timeout (gemini)
      LLM_MIN_INTERVAL_S (default 0)  client-side pacing between calls
    A schema parse failure on the main model also moves to the next fallback model.
    """
    backend = backend or default_backend()
    requested = model or default_model()
    models = [requested] + [m for m in fallback_models() if m != requested]
    max_retries = int(_env_f("LLM_MAX_RETRIES", 2))
    base, cap, deadline = _env_f("LLM_BACKOFF_S", 1), _env_f("LLM_MAX_BACKOFF_S", 8), _env_f("LLM_DEADLINE_S", 20)

    t0 = time.perf_counter()
    attempts, waited, paced, seen, cost = 0, 0.0, 0.0, [], 0.0
    text, n_in, n_out, err, kind, parsed, used = "", 0, 0, None, None, None, requested

    def bill(m, i, o):
        nonlocal n_in, n_out, cost
        p_in, p_out = PRICES.get(m, (0.0, 0.0)) if backend == "gemini" else (0.0, 0.0)
        n_in, n_out, cost = n_in + i, n_out + o, cost + (i * p_in + o * p_out) / 1e6
    for used in models:
        for retry in range(max_retries + 1):
            attempts += 1
            paced += _pace()
            try:
                text, i, o = _call_backend(backend, system, user, schema, used, max_output_tokens, temperature)
                bill(used, i, o)
                err = kind = None
            except Exception as e:  # network, quota, auth, missing model: report, don't crash the pipeline
                le = classify(e)
                bill(used, le.in_tokens, le.out_tokens)
                err, kind = f"{le.kind}: {str(le)[:300]}", le.kind
                seen.append(kind)
                elapsed = time.perf_counter() - t0 - paced
                if kind in TRANSIENT and retry < max_retries and elapsed < deadline:
                    wait = le.retry_after if le.retry_after is not None else base * 2 ** retry
                    wait = min(cap, wait) * (1 + 0.25 * random.random())
                    wait = min(wait, max(0.0, deadline - elapsed))
                    time.sleep(wait)
                    waited += wait
                    continue
                break  # model-level / fatal error, or retries exhausted: leave this model
            try:
                parsed = schema.model_validate_json(_strip_fences(text))
            except ValidationError as e:
                err, kind = f"parse_error: {str(e)[:200]}", "parse"
                seen.append(kind)
            break
        if parsed is not None or kind in FATAL or time.perf_counter() - t0 - paced >= deadline:
            break

    call = LLMCall(backend, used, text, parsed, parsed is not None, (time.perf_counter() - t0 - paced) * 1000, n_in,
                   n_out, cost, err if parsed is None else None,
                   kind if parsed is None else None, attempts, waited * 1000,
                   requested if used != requested else None, seen)
    CALL_LOG.append(call)
    return call


# ---------------- backends ----------------
_gemini_client = None


def _gemini(system, user, schema, model, max_tokens, temperature):
    global _gemini_client
    from google import genai
    from google.genai import types

    if _gemini_client is None:
        key = os.getenv("GEMINI_API_KEY")
        # With no key, the SDK reads Vertex AI settings from env:
        # GOOGLE_GENAI_USE_VERTEXAI=true, GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION (+ gcloud ADC login)
        http = types.HttpOptions(timeout=int(_env_f("LLM_TIMEOUT_S", 30) * 1000))
        _gemini_client = genai.Client(api_key=key, http_options=http) if key else genai.Client(http_options=http)
    cfg = dict(system_instruction=system, temperature=temperature, max_output_tokens=max_tokens,
               response_mime_type="application/json", response_schema=schema)
    # Thinking control. Hidden thinking tokens are billed as output and count against max_output_tokens.
    # LLM_THINKING_LEVEL: minimal | low | medium | high (Gemini 3.x). LLM_THINKING_BUDGET: int (older models).
    # Unset = model default. Only one of the two is sent; level wins if both are set.
    level, budget = os.getenv("LLM_THINKING_LEVEL"), os.getenv("LLM_THINKING_BUDGET")
    if level:
        cfg["thinking_config"] = types.ThinkingConfig(thinking_level=level.upper())
    elif budget is not None:
        cfg["thinking_config"] = types.ThinkingConfig(thinking_budget=int(budget))
    r = _gemini_client.models.generate_content(model=model, contents=user,
                                               config=types.GenerateContentConfig(**cfg))
    u = r.usage_metadata
    n_in = (u.prompt_token_count or 0) if u else 0
    n_out = ((u.candidates_token_count or 0) + (getattr(u, "thoughts_token_count", 0) or 0)) if u else 0
    finish = str(getattr(r.candidates[0], "finish_reason", "") or "") if r.candidates else ""
    block = getattr(getattr(r, "prompt_feedback", None), "block_reason", None)
    if "MAX_TOKENS" in finish:  # output cap hit (often by hidden thinking): JSON is cut off
        raise LLMError("truncated", f"finish_reason={finish}, out_tokens={n_out}", None, n_in, n_out)
    if block or any(w in finish for w in ("SAFETY", "PROHIBITED", "BLOCKLIST", "RECITATION", "SPII")):
        raise LLMError("blocked", f"finish_reason={finish}, block_reason={block}", None, n_in, n_out)
    if not (r.text or "").strip():
        raise LLMError("empty", f"empty output, finish_reason={finish}", None, n_in, n_out)
    return r.text, n_in, n_out


def _ollama(system, user, schema, model, max_tokens, temperature):
    import urllib.request

    payload = {"model": model, "stream": False, "format": schema.model_json_schema(),
               "options": {"temperature": temperature, "num_predict": max_tokens},
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    req = urllib.request.Request(os.getenv("OLLAMA_URL", "http://localhost:11434") + "/api/chat",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as f:
        d = json.load(f)
    return d["message"]["content"], d.get("prompt_eval_count", 0), d.get("eval_count", 0)


class _FakeAPIError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


_fake_i = 0
_FAKE_FAULTS = {"429": (429, "RESOURCE_EXHAUSTED. retryDelay: '1s'"), "503": (503, "UNAVAILABLE high demand"),
                "500": (500, "INTERNAL"), "404": (404, "NOT_FOUND model"), "400": (400, "INVALID_ARGUMENT"),
                "401": (401, "UNAUTHENTICATED")}


def _fake(system, user):
    """Offline stand-in for the draft schema: cites the first two ticket IDs it sees.

    Fault injection for tests: LLM_FAKE_ERRORS="429,503,ok" makes call 1 fail with 429, call 2 with 503,
    call 3 succeed (one entry per API attempt, cycling). Also: timeout, network, truncated, blocked, empty, badjson.
    """
    global _fake_i
    seq = [x.strip() for x in os.getenv("LLM_FAKE_ERRORS", "").split(",") if x.strip()]
    if seq:
        f = seq[_fake_i % len(seq)]
        _fake_i += 1
        if f in _FAKE_FAULTS:
            raise _FakeAPIError(*_FAKE_FAULTS[f])
        if f == "timeout":
            raise TimeoutError("read timed out")
        if f == "network":
            raise ConnectionError("connection refused")
        if f in ("truncated", "blocked", "empty"):
            raise LLMError(f, f"fake {f}", None, 100, 50)
        if f == "badjson":
            return '{"reply": "cut off', 100, 50
    ids = list(dict.fromkeys(re.findall(r"TKT-\d{5}", user)))[:2]
    out = {"reply": f"Thank you for your message. Based on similar past cases {', '.join(ids)}, "
                    "please follow the documented steps. A specialist will follow up if needed.",
           "cited_ticket_ids": ids}
    return json.dumps(out), len(system + user) // 4, 40


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    return t
