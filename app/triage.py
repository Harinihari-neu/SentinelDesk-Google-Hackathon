"""Triage with DS1's trained models (models/triage/<version>/model.joblib). Contract: docs/CONTRACTS.md section 2.

TRIAGE_MODEL picks the version. Default tfidf-lr-v1: best on KB-dev for every head (DS1's kbdev_report.json:
macro-F1 type 0.848 / queue 0.516 / priority 0.590, vs <= 0.818 / 0.373 / 0.452 for the gemini-lr-* models),
and it needs no API call. gemini-lr-* models reuse the retrieval embedding (one shared, cached call).
Probabilities are the logistic regression's own; they are NOT separately calibrated yet (contract asks for it).
Models were pickled with scikit-learn 1.9.1: other versions warn and may give wrong results.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import numpy as np

from app.embed import embed, embed_texts, ticket_text
from app.schemas import HeadPrediction, Ticket, TriageResult

MODELS = Path(__file__).resolve().parent.parent / "models" / "triage"
HEADS = ("type", "queue", "priority")


@lru_cache(maxsize=4)
def _model(version: str) -> dict:
    import joblib

    return joblib.load(MODELS / version / "model.joblib")


def _version() -> str:
    return os.getenv("TRIAGE_MODEL", "tfidf-lr-v1")


def _features(m: dict, texts: list, single: bool):
    if "vectorizer" in m:
        return m["vectorizer"].transform(texts)
    return embed(texts[0])[None, :] if single else embed_texts(texts)


def _result(m: dict, X, row: int) -> TriageResult:
    heads = {}
    for h in HEADS:
        clf = m["heads"][h]
        p = clf.predict_proba(X[row:row + 1])[0]
        j = int(np.argmax(p))
        heads[h] = HeadPrediction(label=str(clf.classes_[j]), confidence=float(p[j]),
                                  probs={str(c): float(v) for c, v in zip(clf.classes_, p)})
    return TriageResult(**heads, model_version=m["model_version"])


def triage(ticket: Ticket) -> TriageResult:
    """`ticket` should be PII-masked: gemini-lr-* models send its text to the embedding API."""
    m = _model(_version())
    return _result(m, _features(m, [ticket_text(ticket.subject, ticket.body)], True), 0)


def triage_batch(tickets: list) -> list:
    m = _model(_version())
    X = _features(m, [ticket_text(t.subject, t.body) for t in tickets], False)
    return [_result(m, X, i) for i in range(len(tickets))]
