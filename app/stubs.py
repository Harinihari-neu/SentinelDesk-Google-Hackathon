"""Placeholder triage and retrieval with the contract shapes.

Used by the pipeline ONLY while app/triage.py or app/retrieval.py don't exist yet.
Deliberately crude: they exist so the pipeline, UI and harness run end to end from hour one.
"""
from __future__ import annotations

import re

from app.schemas import HeadPrediction, Precedent, Ticket, TriageResult

_KB = [
    Precedent(ticket_id="TKT-00001", score=0, language="en", subject="VPN not connecting",
              body="Our VPN drops every few minutes and users cannot reach internal systems.",
              answer="We restarted the VPN gateway service and pushed the updated client profile. "
                     "Please reconnect using the new profile.", queue="Technical Support", type="Incident"),
    Precedent(ticket_id="TKT-00002", score=0, language="en", subject="Password reset",
              body="I forgot my password and the reset email never arrives.",
              answer="We re-sent the reset link and whitelisted the sender domain. "
                     "Please check your spam folder.", queue="IT Support", type="Request"),
    Precedent(ticket_id="TKT-00003", score=0, language="de", subject="Rechnung doppelt",
              body="Wir wurden für denselben Monat zweimal belastet.",
              answer="Wir haben die doppelte Belastung geprüft und an die Buchhaltung zur Korrektur "
                     "weitergeleitet.", queue="Billing and Payments", type="Problem"),
    Precedent(ticket_id="TKT-00004", score=0, language="en", subject="Integration question",
              body="Does your analytics product integrate with our CRM?",
              answer="Our product supports CSV export and a REST API that can be connected to your CRM.",
              queue="Sales and Pre-Sales", type="Request"),
]


def _tokens(s):
    return set(re.findall(r"\w+", (s or "").lower()))


def _head(label, classes, conf):
    rest = (1 - conf) / (len(classes) - 1)
    return HeadPrediction(label=label, confidence=conf, probs={c: (conf if c == label else rest) for c in classes})


def triage(ticket: Ticket) -> TriageResult:
    text = f"{ticket.subject or ''} {ticket.body}".lower()
    urgent = bool(re.search(r"down|outage|ausfall|not working|funktioniert nicht|urgent|dringend", text))
    return TriageResult(
        type=_head("Incident" if urgent else "Request", ["Incident", "Request", "Problem", "Change"], 0.7),
        queue=_head("Technical Support", ["Technical Support", "Product Support", "Billing and Payments",
                                          "Customer Service", "IT Support", "Sales and Pre-Sales"], 0.5),
        priority=_head("high" if urgent else "medium", ["high", "medium", "low"], 0.7),
        model_version="stub-keywords-v0",
    )


def retrieve(ticket: Ticket, k: int = 5, queue=None) -> list:
    q = _tokens(f"{ticket.subject or ''} {ticket.body}")
    out = []
    for p in _KB:
        d = _tokens(f"{p.subject} {p.body}")
        out.append(p.model_copy(update={"score": len(q & d) / (len(q | d) or 1)}))
    return sorted(out, key=lambda p: p.score, reverse=True)[:k]
