"""Retrieval over DS2's Gemini embeddings of core_kb (data/emb/gemini/). Contract: docs/CONTRACTS.md section 3.

Index = core_kb.parquet rows with split == "train" and a non-empty answer (30,085 of 33,449).
KB-dev rows (data/kb_dev_ids.txt) stay OUT: DS2 reserved them to fit calibration, gate thresholds and the
similarity cutoff. If dev were indexed, every dev ticket would find itself at score 1.0.
Score = cosine on L2-normalized vectors. The query is embedded live (one API call, cached; see app/embed.py),
so the ticket must be PII-masked before it gets here. The embeddings are cross-lingual: German tickets can
return English precedents (KB-dev: 38% of a German ticket's top-5 neighbours are English).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from app.embed import embed, embed_texts, ticket_text
from app.schemas import Precedent, Ticket

ROOT = Path(__file__).resolve().parent.parent
EMB_DIR = ROOT / "data" / "emb" / "gemini"
KB_PATH = ROOT / "data" / "core_kb.parquet"
_INDEX = None


def _load():
    """(rows DataFrame, vectors) for the indexed rows. Loaded once, lazily."""
    global _INDEX
    if _INDEX is None:
        df = pd.read_parquet(KB_PATH)
        X = np.load(EMB_DIR / "core_kb_emb.npy")
        ids = pd.read_parquet(EMB_DIR / "core_kb_ids.parquet")["ticket_id"].to_numpy()
        if len(ids) != len(df) or not (ids == df["ticket_id"].to_numpy()).all():
            raise RuntimeError("core_kb_emb.npy rows don't line up with core_kb.parquet: re-run DS2's embed_kb.py")
        keep = ((df["split"] == "train") & (df["answer"].fillna("").str.strip() != "")).to_numpy()
        _INDEX = (df[keep].reset_index(drop=True), np.ascontiguousarray(X[keep]))
    return _INDEX


def _precedents(rows: pd.DataFrame, scores: np.ndarray, order: np.ndarray) -> list:
    out = []
    for i in order:
        r = rows.iloc[int(i)]
        out.append(Precedent(ticket_id=r.ticket_id, score=round(float(scores[i]), 4), language=r.language,
                             subject=r.subject if isinstance(r.subject, str) else None,
                             body=r.body if isinstance(r.body, str) else "", answer=r.answer,
                             queue=r.queue, type=r.type))
    return out


def search_vector(q: np.ndarray, k: int = 5, queue: Optional[str] = None) -> list:
    """Top-k precedents for an already-embedded (normalized) query vector."""
    rows, X = _load()
    s = X @ q
    if queue:
        s = np.where(rows["queue"].to_numpy() == queue, s, -np.inf)
    k = min(k, len(s))
    top = np.argpartition(-s, k - 1)[:k]
    top = top[np.argsort(-s[top])]
    return _precedents(rows, s, [i for i in top if np.isfinite(s[i])])


def retrieve(ticket: Ticket, k: int = 5, queue: Optional[str] = None) -> list:
    """Contract entry point. `ticket` must already be PII-masked (its text is sent to the embedding API)."""
    return search_vector(embed(ticket_text(ticket.subject, ticket.body)), k, queue)


def retrieve_batch(tickets: list, k: int = 5) -> list:
    """Eval helper: one embedding request per ~250 tickets instead of one per ticket."""
    Q = embed_texts([ticket_text(t.subject, t.body) for t in tickets])
    return [search_vector(q, k) for q in Q]
