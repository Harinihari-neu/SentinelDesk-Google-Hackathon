"""Language detection and PII masking. Owned by AI engineering."""
from __future__ import annotations

import re

def _luhn(digits: str) -> bool:
    d = [int(c) for c in digits][::-1]
    return sum(x if i % 2 == 0 else (x * 2 - 9 if x * 2 > 9 else x * 2) for i, x in enumerate(d)) % 10 == 0


def _card(m):
    digits = re.sub(r"\D", "", m.group(0))
    return "[CARD]" if 13 <= len(digits) <= 19 and _luhn(digits) else m.group(0)


# A value after "password is / Passwort: / api key =" that looks like a secret (has a digit or symbol),
# so "your password is valid for 30 minutes" is not masked.
_SECRET_KEY = r"(?:password|passwort|kennwort|pwd|api[- ]?key|api[- ]?token|access token|token|secret)"
_PII = [  # order matters: secrets and cards before IBAN/account/phone, IPs before phone numbers
    ("SECRET", re.compile(r"\b(?:sk|pk|rk)[-_](?:live|test)[-_][A-Za-z0-9]{8,}|\bgh[pousr]_[A-Za-z0-9]{16,}|"
                          r"\bAKIA[0-9A-Z]{16}\b|\bAIza[0-9A-Za-z_-]{30,}")),
    ("PASSWORD", (re.compile(rf"(?i)(\b{_SECRET_KEY}(?:\s+(?:is|ist|lautet)\s+|\s*[:=]\s*))"
                             r"(?=\S*[\d!@#$%^&*?_~+-])\S+"), r"\1[PASSWORD]")),
    ("CARD", (re.compile(r"\b\d(?:[ -]?\d){12,18}\b"), _card)),
    ("IP", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}\b")),
    ("ACCOUNT", re.compile(r"\b\d{9,}\b")),
    ("PHONE", (re.compile(r"(?<!\w)\+?\d[\d\s().-]{7,}\d\b"), lambda m: _phone(m))),
]
# Shapes the PHONE pattern also matches that are not phone numbers (guard-realdata-v0, holdout + adv-big-01):
# ISO dates and log timestamps "2023-02-20", "2026-11-20 10"; dotted versions and dates "90.0.4430.212", "20.02.2023".
_NOT_PHONE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[ T]\d{1,2})?|\d+(?:\.\d+)+")


def _phone(m):
    return m.group(0) if _NOT_PHONE.fullmatch(m.group(0)) else "[PHONE]"


def mask_pii(text: str):
    """Return (masked_text, {kind: count})."""
    counts = {}
    for kind, rx in _PII:
        repl = f"[{kind}]"
        if isinstance(rx, tuple):
            rx, repl = rx
        new, n = rx.subn(repl, text)
        if kind in ("CARD", "PHONE"):  # only real matches were replaced (Luhn-valid / not a date or version)
            n = new.count(f"[{kind}]") - text.count(f"[{kind}]")
        text = new
        if n:
            counts[kind] = n
    return text, counts


_DE = {"der", "die", "das", "und", "ist", "nicht", "ich", "sie", "wir", "mit", "für", "auf", "bitte",
       "danke", "ein", "eine", "unser", "unsere", "wird", "haben", "können", "sehr", "geehrte"}
_EN = {"the", "and", "is", "not", "i", "you", "we", "with", "for", "on", "please", "thanks", "a",
       "an", "our", "will", "have", "can", "very", "dear", "this", "that", "to"}


def detect_language(text: str) -> str:
    """'en', 'de' or 'other'. Uses langdetect if installed, else a stopword vote."""
    text = (text or "").strip()
    if not text:
        return "other"
    try:
        from langdetect import DetectorFactory, detect_langs

        DetectorFactory.seed = 0
        top = detect_langs(text)[0]
    except Exception:
        return _stopword_vote(text)
    if top.lang in ("en", "de"):
        return top.lang
    # langdetect is unsure on short text: "Brand engagement is currently low" -> nl 0.43 (guard-realdata-v0).
    # Below 0.9 the stopword vote decides; real FR/ES/IT tickets score >= 0.99 and stay "other".
    return "other" if top.prob >= 0.9 else _stopword_vote(text)


def _stopword_vote(text: str) -> str:
    words = re.findall(r"[a-zäöüß]+", text.lower())
    de, en = sum(w in _DE for w in words), sum(w in _EN for w in words)
    if de == en == 0:
        return "other"
    return "de" if de > en else "en"


# ---------- normalisation (used by the guard and by the adversarial scorer) ----------
import unicodedata  # noqa: E402

# Invisible characters that can split words ("ig​nore"): zero-width, joiners, bidi controls, soft hyphen, BOM
_INVISIBLE = re.compile(r"[­᠎​-‏‪-‮⁠-⁤⁦-⁩﻿]")
# Cyrillic/Greek letters that look like Latin ones. NFKC does not map these, so we do.
_CONFUSABLE = str.maketrans("аеіјорсуххАВЕІЈКМНОРСТХУԁһԛѕԝαβεικνορτυχΑΒΕΗΙΚΜΝΟΡΤΥΧΖ",
                            "aeijopcyxxABEIJKMHOPCTXYdhqswabeiknoptuxABEHIKMNOPTYXZ")


def fold(text: str) -> str:
    """NFKC (fullwidth -> ASCII), drop invisible characters, map Cyrillic/Greek look-alikes to Latin.
    For DETECTION and scoring only; the customer's text is never rewritten with this."""
    return unicodedata.normalize("NFKC", _INVISIBLE.sub("", text or "")).translate(_CONFUSABLE)
