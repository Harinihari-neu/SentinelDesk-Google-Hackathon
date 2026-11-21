"""Escalation and safety gate. Logic owned by AI engineering; thresholds come from DS1 via app/config/gate.json.

The gate never asks the LLM whether it is confident: it uses calibrated classifier scores,
retrieval similarity and code rules.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from app.schemas import GateDecision, Ticket, TriageResult

CONFIG_PATH = Path(__file__).parent / "config" / "gate.json"
DEFAULTS = {"min_confidence": {"type": 0.5, "queue": 0.4, "priority": 0.5}, "min_similarity": 0.5}

# Requests that need an action/commitment. Escalate unless a precedent's answer covers the same term.
RISKY_ASKS = ["refund", "reimburse", "compensation", "credit", "discount", "guarantee", "sla", "price",
              "pricing", "contract", "legal", "lawyer", "lawsuit", "cancel", "delete my",
              "rückerstattung", "erstattung", "entschädigung", "gutschrift", "rabatt", "garantie",
              "preis", "vertrag", "anwalt", "kündig"]


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    if CONFIG_PATH.exists():
        user = json.loads(CONFIG_PATH.read_text())
        cfg["min_confidence"].update(user.get("min_confidence", {}))
        cfg["min_similarity"] = user.get("min_similarity", cfg["min_similarity"])
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
    text = f"{ticket.subject or ''} {ticket.body}".lower()
    evidence = " ".join(p.answer.lower() for p in precedents)
    for term in RISKY_ASKS:
        if re.search(rf"\b{re.escape(term)}", text) and term not in evidence:
            reasons.append(f"unsupported_request:{term}")
    return GateDecision(escalate=bool(reasons), reasons=reasons)
