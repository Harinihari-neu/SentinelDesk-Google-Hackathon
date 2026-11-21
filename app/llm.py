"""The only place LLM calls happen. Owned by AI engineering.

Backends (env LLM_BACKEND): "gemini" (default), "ollama" (local models), "fake" (offline, deterministic).
Model (env LLM_MODEL). Every call is timed, token-counted, costed and appended to CALL_LOG.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Optional, Type

from pydantic import BaseModel, ValidationError

# USD per 1M tokens (input, output). Fill in from Google's current pricing page before quoting cost.
# A model missing here -> cost reported as n/a (never a fake $0). Gemini 2.5 is retired for new keys.
PRICES: dict = {
    # "gemini-3.5-flash-lite": (IN, OUT),   # TODO verify
    # "gemini-3.5-flash": (IN, OUT),        # TODO verify; needs thinking capped (SDK on Python 3.10+)
}


@dataclass
class LLMCall:
    backend: str
    model: str
    text: str
    parsed: Optional[BaseModel]
    parse_ok: bool
    latency_ms: float
    in_tokens: int
    out_tokens: int          # includes hidden "thinking" tokens: they are billed as output
    cost_usd: float
    error: Optional[str] = None


CALL_LOG: list = []          # the eval harness reads and clears this


def default_backend() -> str:
    return os.getenv("LLM_BACKEND", "gemini")


def default_model() -> str:
    return os.getenv("LLM_MODEL", "gemini-3.5-flash-lite")


def generate_json(system: str, user: str, schema: Type[BaseModel], model: Optional[str] = None,
                  backend: Optional[str] = None, max_output_tokens: int = 1024,
                  temperature: float = 0.0) -> LLMCall:
    """Call a model with a strict JSON schema. Never raises: errors come back in LLMCall.error."""
    backend = backend or default_backend()
    model = model or default_model()
    t0 = time.perf_counter()
    text, n_in, n_out, err = "", 0, 0, None
    try:
        if backend == "gemini":
            text, n_in, n_out = _gemini(system, user, schema, model, max_output_tokens, temperature)
        elif backend == "ollama":
            text, n_in, n_out = _ollama(system, user, schema, model, max_output_tokens, temperature)
        elif backend == "fake":
            text, n_in, n_out = _fake(system, user)
        else:
            raise ValueError(f"unknown backend {backend}")
    except Exception as e:  # network, quota, auth: report, don't crash the pipeline
        err = f"{type(e).__name__}: {str(e)[:200]}"
    latency_ms = (time.perf_counter() - t0) * 1000

    parsed = None
    if err is None:
        try:
            parsed = schema.model_validate_json(_strip_fences(text))
        except ValidationError as e:
            err = f"parse_error: {str(e)[:200]}"
    p_in, p_out = PRICES.get(model, (0.0, 0.0)) if backend == "gemini" else (0.0, 0.0)
    call = LLMCall(backend, model, text, parsed, parsed is not None, latency_ms, n_in, n_out,
                   (n_in * p_in + n_out * p_out) / 1e6, err)
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
        _gemini_client = genai.Client(api_key=key) if key else genai.Client()
    cfg = dict(system_instruction=system, temperature=temperature, max_output_tokens=max_tokens,
               response_mime_type="application/json", response_schema=schema)
    budget = os.getenv("LLM_THINKING_BUDGET")  # e.g. "0" to switch thinking off on 2.5 Flash
    if budget is not None:
        cfg["thinking_config"] = types.ThinkingConfig(thinking_budget=int(budget))
    r = _gemini_client.models.generate_content(model=model, contents=user,
                                               config=types.GenerateContentConfig(**cfg))
    u = r.usage_metadata
    n_out = (u.candidates_token_count or 0) + (getattr(u, "thoughts_token_count", 0) or 0)
    return r.text or "", u.prompt_token_count or 0, n_out


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


def _fake(system, user):
    """Offline stand-in for the draft schema: cites the first two ticket IDs it sees."""
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
