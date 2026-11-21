"""End-to-end pipeline. Owned by AI engineering.

    from app.pipeline import run
    result = run(subject, body)   # -> PipelineResult

Uses teammates' app/triage.py and app/retrieval.py when present, stubs otherwise.
Env: LLM_BACKEND, LLM_MODEL, LLM_FALLBACK_MODEL (optional step-6 retry), RAG_K (default 3).
"""
from __future__ import annotations

import os
import time
import uuid
from typing import Optional

from app.checks import is_deferral, run_checks
from app.draft import draft as make_draft
from app.gate import decide
from app.schemas import PipelineResult, Ticket
from app.textproc import detect_language, mask_pii

try:
    from app.triage import triage as _triage
    TRIAGE_SOURCE = "app.triage"
except ImportError:
    from app.stubs import triage as _triage
    TRIAGE_SOURCE = "stub"
try:
    from app.retrieval import retrieve as _retrieve
    RETRIEVAL_SOURCE = "app.retrieval"
except ImportError:
    from app.stubs import retrieve as _retrieve
    RETRIEVAL_SOURCE = "stub"


def run_ticket(ticket: Ticket, k: Optional[int] = None, model: Optional[str] = None,
               backend: Optional[str] = None, precedents: Optional[list] = None) -> PipelineResult:
    """precedents: use these instead of calling retrieval (eval only: adversarial cases bring their own evidence)."""
    k = k or int(os.getenv("RAG_K", "3"))
    t = {}
    t0 = time.perf_counter()

    def lap(name, start):
        t[name] = (time.perf_counter() - start) * 1000
        return time.perf_counter()

    s = t0
    ticket = ticket.model_copy(update={"language": detect_language(f"{ticket.subject or ''} {ticket.body}")})
    s = lap("preprocess", s)
    tri = _triage(ticket)
    s = lap("triage", s)
    prec = precedents[:k] if precedents is not None else _retrieve(ticket, k=k)
    s = lap("retrieval", s)
    gate = decide(ticket, tri, prec)
    s = lap("gate", s)

    draft, checks, tokens, cost, status = None, None, 0, 0.0, "escalated"
    if not gate.escalate:
        masked = ticket.model_copy(update={"subject": mask_pii(ticket.subject or "")[0] or None,
                                           "body": mask_pii(ticket.body)[0]})
        draft, call = make_draft(masked, prec, model=model, backend=backend)
        checks = run_checks(draft, ticket, prec, llm_error=call.error)
        tokens, cost = call.in_tokens + call.out_tokens, call.cost_usd
        fb = os.getenv("LLM_FALLBACK_MODEL")
        # step 6: one retry on a larger model when checks fail (not on auth/config errors: they fail everywhere)
        if not checks.passed and fb and call.error_kind not in ("auth", "config"):
            draft, call = make_draft(masked, prec, model=fb, backend=backend)
            checks = run_checks(draft, ticket, prec, llm_error=call.error)
            tokens += call.in_tokens + call.out_tokens
            cost += call.cost_usd
        s = lap("draft_and_checks", s)
        # a passing deferral ("a specialist will follow up") is a holding reply: send it, but a human owns the ticket
        holding = not draft.cited_ticket_ids and is_deferral(draft.reply)
        status = "auto_draft_ready" if checks.passed and not holding else "escalated"
    t["total"] = (time.perf_counter() - t0) * 1000
    return PipelineResult(ticket=ticket, triage=tri, precedents=prec, gate=gate, draft=draft, checks=checks,
                          status=status, timings_ms=t, llm_tokens=tokens, llm_cost_usd=cost)


def run(subject: Optional[str], body: str, **kw) -> PipelineResult:
    return run_ticket(Ticket(ticket_id=f"LIVE-{uuid.uuid4().hex[:8]}", subject=subject or None, body=body), **kw)
