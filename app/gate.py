"""Escalation and safety gate. Logic owned by AI engineering; thresholds come from DS1 via app/config/gate.json.

The gate never asks the LLM whether it is confident: it uses calibrated classifier scores,
retrieval similarity and code rules.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from app.guard import injection_signals, precedent_signals, sensitive_signals
from app.schemas import GateDecision, Ticket, TriageResult
from app.textproc import fold

CONFIG_PATH = Path(__file__).parent / "config" / "gate.json"
# min_similarity 0.95: Gemini-embedding cosines are high (KB-dev top-1 median 0.976). Below 0.95 the top precedent
# shares the ticket's queue ~55% of the time vs 89% above (gate-dev-v1). DS1's app/config/gate.json overrides this.
DEFAULTS = {"min_confidence": {"type": 0.5, "queue": 0.4, "priority": 0.5}, "min_similarity": 0.95}

# Requests that need an action/commitment. Escalate unless a precedent's answer covers the same term.
RISKY_ASKS = ["refund", "reimburse", "compensation", "credit", "discount", "guarantee", "sla", "price",
              "pricing", "contract", "legal", "lawyer", "lawsuit", "cancel", "delete my",
              "rückerstattung", "erstattung", "entschädigung", "gutschrift", "rabatt", "garantie",
              "preis", "vertrag", "anwalt", "kündig"]
# Prefix match on the term, with exceptions where the prefix also starts a harmless phrase (case adv-ctl-05).
_RISKY_RX = {t: re.compile(rf"\b{re.escape(t)}" + (r"(?!\s*cards?\b)" if t == "credit" else "")) for t in RISKY_ASKS}


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    if CONFIG_PATH.exists():
        user = json.loads(CONFIG_PATH.read_text())
        cfg["min_confidence"].update(user.get("min_confidence", {}))
        cfg["min_similarity"] = user.get("min_similarity", cfg["min_similarity"])
    # Manual-testing / demo override. Hand-typed tickets score ~0.86-0.93 against this synthetic KB (live check,
    # 2026-11-21), below the 0.95 measured on KB-dev, so with the default nearly everything typed by hand escalates.
    if os.getenv("GATE_MIN_SIMILARITY"):
        cfg["min_similarity"] = float(os.environ["GATE_MIN_SIMILARITY"])
    return cfg


def decide(ticket: Ticket, triage: TriageResult, precedents: list, cfg=None) -> GateDecision:
    cfg = cfg or load_config()
    reasons = []
    if triage.priority.label == "high" and triage.type.label == "Incident":
        reasons.append("high_priority_incident")
    for head, thr in cfg["min_confidence"].items():
        if getattr(triage, head).confidence < thr:
            reasons.append(f"low_confidence_{head}")
    top = precedents[0].score if precedents else 0.0
    if top < cfg["min_similarity"]:
        reasons.append("no_close_precedent")
    raw = f"{ticket.subject or ''} {ticket.body}"
    text = fold(raw).lower()
    evidence = " ".join(p.answer.lower() for p in precedents)
    for term, rx in _RISKY_RX.items():
        if rx.search(text) and term not in evidence:
            reasons.append(f"unsupported_request:{term}")
    # guardrails (app/guard.py): code patterns, logged by name
    inj = injection_signals(raw)
    if inj:
        reasons.append("possible_injection:" + "+".join(inj))
    if sensitive_signals(raw):
        reasons.append("sensitive_content")
    if ticket.language == "other":
        reasons.append("unsupported_language")
    for p in precedents:
        sig = precedent_signals(f"{p.subject or ''} {p.body} {p.answer}")
        if sig:
            reasons.append(f"suspicious_precedent:{p.ticket_id}:" + "+".join(sig))
    return GateDecision(escalate=bool(reasons), reasons=reasons)
