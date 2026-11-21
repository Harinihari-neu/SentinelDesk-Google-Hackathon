"""Code-only guardrail detectors. Owned by AI engineering.

Rule: pattern lists, zero model tokens, milliseconds. They are a filter, not a proof: a determined attacker
can rephrase past them, which is why the gate escalates to a human and the output checks run after the LLM.
Every detector returns the NAMES of the patterns that matched, so each escalation is explainable and logged.

Used by:
  app/gate.py    injection_signals(ticket) -> "possible_injection:<names>"; sensitive_signals -> "sensitive_content";
                 precedent_signals(precedent) -> "suspicious_precedent:<id>" (KB poisoning)
  app/checks.py  credential_request(reply) -> "credential_request"; unsupported_links(reply) -> "unsupported_contact"
"""
from __future__ import annotations

import base64
import os
import re

from app.textproc import fold

# ---------- prompt injection in a ticket ----------
# (name, weight, pattern). A ticket escalates when the summed weight >= INJECTION_THRESHOLD.
# Patterns run on folded, lowercased text (NFKC, invisible characters removed, Cyrillic/Greek look-alikes mapped).
_OBJ_EN = r"(?:instructions?|rules|prompts?|directions|guidelines|system prompt)"
_OBJ_DE = r"(?:anweisungen|regeln|instruktionen|vorgaben|richtlinien|systemprompt)"
INJECTION_PATTERNS = [
    ("ignore_instructions", 1.0, re.compile(
        rf"\b(?:ignore|disregard|forget|override|bypass)\b(?:\W+\w+){{0,3}}?\W+{_OBJ_EN}\b")),
    ("ignore_instructions_de", 1.0, re.compile(
        rf"\b(?:ignorier\w*|vergiss|vergessen sie|missachte\w*|übergeh\w*)\b(?:\W+\w+){{0,3}}?\W+{_OBJ_DE}\b")),
    ("reveal_prompt", 1.0, re.compile(
        r"\b(?:print|show|reveal|repeat|output|display|tell me)\b(?:\W+\w+){0,4}?\W+"
        r"(?:system prompt|hidden instructions|your (?:instructions|rules))")),
    ("reveal_prompt_de", 1.0, re.compile(r"\b(?:systemprompt|system-prompt)\b|\bgib\b(?:\W+\w+){0,5}?\W+regeln\b")),
    ("tag_escape", 1.0, re.compile(r"</?\s*(?:ticket|precedent|system)\b")),
    ("fake_system", 1.0, re.compile(r"\b(?:systemhinweis|system override)\b|\bnew rule\b|\bdeveloper mode\b")),
    # bare "System:" is weak: real tickets write "System: Windows 11" (found by tests/test_guardrails.py)
    ("system_label", 0.5, re.compile(r"(?:^|\n|\s)system\s*:")),
    ("persona", 0.5, re.compile(r"\byou are now\b|\bdu bist (?:ab )?jetzt\b|\bpretend (?:to be|you are)\b|"
                                r"\blet'?s play a game\b|\brole-?play\b")),
    ("reply_steering", 0.5, re.compile(r"\b(?:start|begin|end) your (?:reply|answer|response) with\b|"
                                       r"\breply only with\b|\bantworte nur mit\b|\bam ende\b(?:\W+\w+){0,3}?\W+(?:schreib|antwort)|"
                                       r"\bschreib\w* am ende\b|\bnote for the assistant\b|\bhinweis für den assistenten\b")),
]
INJECTION_THRESHOLD = 1.0
_B64 = re.compile(r"[A-Za-z0-9+/]{24,}={0,2}")


def _decoded_payloads(text: str) -> list:
    """Base64 blobs that decode to readable text (an attacker hiding an instruction)."""
    out = []
    for blob in _B64.findall(text):
        try:
            s = base64.b64decode(blob + "=" * (-len(blob) % 4), validate=True).decode("utf-8")
        except Exception:
            continue
        if len(s) >= 8 and sum(c.isprintable() for c in s) / len(s) > 0.95 and " " in s:
            out.append(s)
    return out


def injection_signals(text: str) -> list:
    """Names of injection patterns found (incl. inside base64 payloads). Empty list = nothing found."""
    low = fold(text).lower()
    hits = [(n, w) for n, w, rx in INJECTION_PATTERNS if rx.search(low)]
    for payload in _decoded_payloads(fold(text)):
        hits += [(f"base64:{n}", w) for n, w, rx in INJECTION_PATTERNS if rx.search(payload.lower())]
    return [n for n, _ in hits] if sum(w for _, w in hits) >= INJECTION_THRESHOLD else []


# ---------- sensitive content: a human must see it, no auto-reply ----------
SENSITIVE = re.compile(
    r"\b(?:end my life|kill myself|suicid\w*|self[- ]harm|want to die|don'?t want to live|"
    r"selbstmord|suizid\w*|umbringen|nicht mehr leben|will sterben|mir (?:etwas|was) antun)\b")


def sensitive_signals(text: str) -> bool:
    return bool(SENSITIVE.search(fold(text).lower()))


# ---------- KB poisoning: a precedent that instructs the agent instead of describing a fix ----------
PRECEDENT_PATTERNS = INJECTION_PATTERNS[:4] + [
    ("agent_directive", 1.0, re.compile(r"\b(?:policy note|note|instruction)s? (?:for|to) (?:agents?|support staff|the assistant)\b|"
                                        r"\bhinweis für (?:agenten|mitarbeiter|den assistenten)\b")),
    ("always_offer", 1.0, re.compile(r"\balways (?:offer|give|promise|grant)\b|\bversprechen sie jedem\b")),
    ("credential_ask", 1.0, re.compile(
        r"\b(?:ask|tell|bitten sie)\b(?:\W+\w+){0,6}?\W+(?:email|e-mail|send|senden|share|enter)\b(?:\W+\w+){0,4}?\W+"
        r"(?:current |old |alte\w* |aktuelle\w* )?(?:password|passwort|pin|kennwort|credentials|zugangsdaten)\b|"
        r"\b(?:enter|eingeben)\b(?:\W+\w+){0,3}?\W+(?:old|current|alte\w*|aktuelle\w*) (?:password|passwort)\b")),
]


def precedent_signals(answer: str) -> list:
    low = fold(answer).lower()
    return [n for n, _, rx in PRECEDENT_PATTERNS if rx.search(low)]


# ---------- output: the reply asks for credentials ----------
_CRED = r"(?:password|passwort|kennwort|pin|api[- ]?key|api[- ]?token|login details|credentials|zugangsdaten)"
# The credential word used as a modifier is not a secret: "password resets", "password policies",
# "api key mismatch", "api key configuration" (KB answers: 9 of 20 hand-read hits in guard-realdata-v0, 3 in -v1).
_CRED_MOD = r"(?!\s+(?:resets?|polic\w*|management|settings|mismatch\w*|requirements?|config\w*|expir\w*|richtlinie\w*))"
_CRED_ASK = re.compile(
    rf"\b(?:send|email|e-mail|share|give us|tell us|reply with|provide|forward|schicken|senden|mailen|mitteilen|"
    rf"nennen|übermitteln)\b(?:\W+\w+){{0,6}}?\W+(?:your |ihr\w* )?(?:current |old |alte\w* |aktuelle\w* )?{_CRED}\b"
    rf"{_CRED_MOD}|\b(?:old|current|alte\w*|aktuelle\w*) (?:password|passwort|kennwort)\b{_CRED_MOD}")
_NEG = re.compile(r"\b(?:never|not|don'?t|do not|nie|niemals|nicht|keine\w*)\b")


def credential_request(reply: str) -> bool:
    """True if the reply asks the customer to hand over a secret. "Never send your password" is fine."""
    low = fold(reply).lower()
    for m in _CRED_ASK.finditer(low):
        before = low[max(0, m.start() - 30):m.start()]
        if not _NEG.search(before + m.group(0)):
            return True
    return False


# ---------- output: links ----------
_URL = re.compile(r"(?:https?://|www\.)[^\s)\]>\"']+|\b(?:[a-z0-9-]+\.)+[a-z]{2,}/[^\s)\]>\"']*", re.I)


def allowed_domains() -> set:
    """CONTACT_ALLOWLIST: comma-separated domains our replies may link to (e.g. "help.example.com"). Default: none."""
    return {d.strip().lower() for d in os.getenv("CONTACT_ALLOWLIST", "").split(",") if d.strip()}


def unsupported_links(reply: str) -> list:
    """Links in the reply whose host is not allow-listed. A link copied from a precedent is NOT trusted:
    the knowledge base itself can be poisoned (case adv-kb-03)."""
    allow, out = allowed_domains(), []
    for u in _URL.findall(fold(reply)):
        host = re.sub(r"^(?:https?://)?(?:www\.)?", "", u.lower()).split("/")[0]
        if not any(host == d or host.endswith("." + d) for d in allow):
            out.append(u)
    return out
