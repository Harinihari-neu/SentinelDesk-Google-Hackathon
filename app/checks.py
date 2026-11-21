"""Deterministic trust checks on a draft. Owned by AI engineering.

Rule: code only, zero model tokens, milliseconds, and every failure names its reason.
Failure codes (the harness counts these by prefix):
  schema_invalid, llm_error, no_citations, invalid_citation:<id>, unsupported_number:<n>,
  unsupported_promise:<term>, language_mismatch:<lang>, pii_in_reply
"""
from __future__ import annotations

import re
from typing import Optional

from app.schemas import CheckResult, Draft, Ticket
from app.textproc import detect_language, mask_pii

PROMISE_TERMS = ["refund", "reimburse", "compensation", "credit", "discount", "free of charge", "guarantee",
                 "guaranteed", "sla", "uptime", "within 24 hours", "warranty", "legal",
                 "rückerstattung", "erstattung", "entschädigung", "gutschrift", "rabatt", "kostenlos",
                 "garantie", "garantiert", "gewährleistung"]
_ID = re.compile(r"TKT-\d{5}")
_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def run_checks(draft: Draft, ticket: Ticket, precedents: list, llm_error: Optional[str] = None) -> CheckResult:
    f = []
    if llm_error:
        f.append("llm_error" if not llm_error.startswith("parse_error") else "schema_invalid")
        return CheckResult(passed=False, failures=f)

    retrieved = {p.ticket_id: p for p in precedents}
    cited = [c for c in draft.cited_ticket_ids if c in retrieved]
    inline = set(_ID.findall(draft.reply))
    if not draft.cited_ticket_ids and not inline:
        f.append("no_citations")
    for cid in set(draft.cited_ticket_ids) | inline:
        if cid not in retrieved:
            f.append(f"invalid_citation:{cid}")

    evidence_src = [retrieved[c] for c in cited] or precedents
    evidence = " ".join(f"{p.subject or ''} {p.body} {p.answer}" for p in evidence_src).lower()
    evidence += " " + f"{ticket.subject or ''} {ticket.body}".lower()  # echoing the user's own numbers is fine
    reply = _ID.sub(" ", draft.reply).lower()

    ev_nums = {n.replace(",", ".") for n in _NUM.findall(evidence)}
    for n in _NUM.findall(reply):
        if n.replace(",", ".") not in ev_nums:
            f.append(f"unsupported_number:{n}")
    for term in PROMISE_TERMS:
        if re.search(rf"\b{re.escape(term)}\b", reply) and term not in evidence:
            f.append(f"unsupported_promise:{term}")

    lang = detect_language(draft.reply)
    if ticket.language in ("en", "de") and lang != ticket.language:
        f.append(f"language_mismatch:{lang}")
    if mask_pii(draft.reply)[1]:
        f.append("pii_in_reply")
    return CheckResult(passed=not f, failures=sorted(set(f)))
