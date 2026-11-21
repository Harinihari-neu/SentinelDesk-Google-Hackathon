"""Language detection and PII masking. Owned by AI engineering."""
from __future__ import annotations

import re

_PII = [  # order matters: IPs before phone numbers
    ("IP", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}\b")),
    ("ACCOUNT", re.compile(r"\b\d{9,}\b")),
    ("PHONE", re.compile(r"(?<!\w)\+?\d[\d\s().-]{7,}\d\b")),
]


def mask_pii(text: str):
    """Return (masked_text, {kind: count})."""
    counts = {}
    for kind, rx in _PII:
        text, n = rx.subn(f"[{kind}]", text)
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
        from langdetect import DetectorFactory, detect

        DetectorFactory.seed = 0
        code = detect(text)
        return code if code in ("en", "de") else "other"
    except Exception:
        words = re.findall(r"[a-zäöüß]+", text.lower())
        de, en = sum(w in _DE for w in words), sum(w in _EN for w in words)
        if de == en == 0:
            return "other"
        return "de" if de > en else "en"
