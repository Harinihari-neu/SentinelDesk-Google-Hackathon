"""Shared data contracts. Owned by the AI engineering role.

Every component takes and returns these types. Do not change a field without
telling the owner; add new optional fields at the end if you need more.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

TicketType = Literal["Incident", "Request", "Problem", "Change"]
Priority = Literal["high", "medium", "low"]
Lang = Literal["en", "de", "other"]


class Ticket(BaseModel):
    ticket_id: str                      # "TKT-00042" from the split, or "LIVE-<uuid>" from the console
    subject: Optional[str] = None       # may be None or ""
    body: str
    language: Optional[Lang] = None     # filled by the pipeline's language detector, never by the caller


class HeadPrediction(BaseModel):
    label: str                          # argmax label
    confidence: float                   # calibrated probability of `label`, 0..1
    probs: dict[str, float]             # every class -> calibrated probability, sums to ~1


class TriageResult(BaseModel):
    type: HeadPrediction
    queue: HeadPrediction
    priority: HeadPrediction
    model_version: str                  # e.g. "tfidf-lr-v1", "e5-lr-cal-v2": used by the eval ladder


class Precedent(BaseModel):
    ticket_id: str                      # must exist in the KB split
    score: float                        # cosine similarity, higher = closer
    language: str
    subject: Optional[str] = None
    body: str
    answer: str                         # historical resolution (KB split only)
    queue: Optional[str] = None
    type: Optional[str] = None


class GateDecision(BaseModel):
    escalate: bool
    reasons: list[str] = Field(default_factory=list)   # e.g. ["high_priority_incident", "low_similarity"]


class Draft(BaseModel):
    reply: str
    cited_ticket_ids: list[str]
    language: str
    model: str                          # which LLM wrote it


class CheckResult(BaseModel):
    passed: bool
    failures: list[str] = Field(default_factory=list)  # e.g. ["uncited_number:99.99%", "promise_term:refund"]


class PipelineResult(BaseModel):
    ticket: Ticket
    triage: TriageResult
    precedents: list[Precedent]
    gate: GateDecision
    draft: Optional[Draft] = None       # None when escalated before drafting
    checks: Optional[CheckResult] = None
    status: Literal["auto_draft_ready", "escalated", "major_incident"]
    timings_ms: dict[str, float]        # step name -> latency, plus "total"
    llm_tokens: int = 0
    llm_cost_usd: float = 0.0
