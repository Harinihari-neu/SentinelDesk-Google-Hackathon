"""Deterministic trust checks on a draft. Owned by AI engineering.

Rule: code only, zero model tokens, milliseconds, and every failure names its reason.
Failure codes (the harness counts these by prefix):
  schema_invalid, llm_error:<kind>, no_citations, invalid_citation:<id>, unsupported_number:<n>,
  unsupported_promise:<term>, language_mismatch:<lang>, german_transliteration, pii_in_reply,
  credential_request (reply asks for a password/PIN/key), unsupported_contact (link to a non-allow-listed domain)
A pure deferral ("a specialist will follow up", no facts) needs no citation; see is_deferral().
"""
from __future__ import annotations

import re
from typing import Optional

from app.guard import credential_request, unsupported_links
from app.schemas import CheckResult, Draft, Ticket
from app.textproc import detect_language, mask_pii

PROMISE_TERMS = ["refund", "reimburse", "compensation", "credit", "discount", "free of charge", "guarantee",
                 "guaranteed", "sla", "uptime", "within 24 hours", "warranty", "legal",
                 "rückerstattung", "erstattung", "entschädigung", "gutschrift", "rabatt", "kostenlos",
                 "garantie", "garantiert", "gewährleistung"]
_ID = re.compile(r"TKT-\d{5}")
_NUM = re.compile(r"\d+(?:[.,]\d+)?")
_DEFER_WHO = re.compile(r"specialist|colleague|our team|support team|account manager|spezialist|kolleg|"
                        r"fachabteilung|team|ansprechpartner", re.I)
_DEFER_ACT = re.compile(r"follow up|follow-up|get back|contact you|be in touch|reach out|"
                        r"melden|zurückkommen|kontaktieren|in verbindung", re.I)
# German written without umlauts ("oeffnen", "fuer"): only flagged when the reply has no ä/ö/ü/ß at all
_TRANSLIT = re.compile(r"fuer|koenn|moecht|oeffn|waehl|pruef|ueber|zurueck|menue|muess|fueg|loesch|aender|"
                       r"bestaetig|gruess|haett|waere|moegl|naechst|ausfuehr|geaendert|gehoer|schoen", re.I)
_LIST_MARKER = re.compile(r"^\s*\d{1,2}[.)]\s", re.MULTILINE)  # "1. " / "2) " step numbers are not claims


def run_checks(draft: Draft, ticket: Ticket, precedents: list, llm_error: Optional[str] = None) -> CheckResult:
    f = []
    if llm_error:
        kind = llm_error.split(":", 1)[0].strip()
        f.append("schema_invalid" if kind in ("parse_error", "parse", "truncated") else f"llm_error:{kind}")
        return CheckResult(passed=False, failures=f)

    retrieved = {p.ticket_id: p for p in precedents}
    cited = [c for c in draft.cited_ticket_ids if c in retrieved]
    inline = set(_ID.findall(draft.reply))
    if not draft.cited_ticket_ids and not inline and not is_deferral(draft.reply):
        f.append("no_citations")
    for cid in set(draft.cited_ticket_ids) | inline:
        if cid not in retrieved:
            f.append(f"invalid_citation:{cid}")

    evidence_src = [retrieved[c] for c in cited] or precedents
    evidence = " ".join(f"{p.subject or ''} {p.body} {p.answer}" for p in evidence_src).lower()
    evidence += " " + f"{ticket.subject or ''} {ticket.body}".lower()  # echoing the user's own numbers is fine
    reply = _LIST_MARKER.sub(" ", _ID.sub(" ", draft.reply)).lower()

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
    if ticket.language == "de" and not re.search(r"[äöüßÄÖÜ]", draft.reply) and _TRANSLIT.search(draft.reply):
        f.append("german_transliteration")
    if mask_pii(draft.reply)[1]:
        f.append("pii_in_reply")
    if credential_request(draft.reply):
        f.append("credential_request")
    if unsupported_links(draft.reply):
        f.append("unsupported_contact")
    return CheckResult(passed=not f, failures=sorted(set(f)))


def is_deferral(reply: str) -> bool:
    """A short reply that only hands over to a human: no facts, so no citation needed.
    The pipeline sends it as a holding reply and still escalates the ticket."""
    return (len(reply.split()) <= 40 and not _NUM.search(_ID.sub(" ", reply))
            and bool(_DEFER_WHO.search(reply)) and bool(_DEFER_ACT.search(reply)))
