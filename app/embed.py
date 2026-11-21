"""app/embed.py: one shared embedder for triage and retrieval (Gemini embeddings). Written by DS2.

Live tickets must be PII-masked BEFORE calling embed(): the text leaves for Google.
Auth: GEMINI_API_KEY if set, else Vertex AI (GOOGLE_CLOUD_PROJECT / GOOGLE_CLOUD_LOCATION + gcloud ADC).
Both give the same vectors: cosine 1.000 on 5 KB texts vs DS2's stored Vertex vectors (checked 2026-11-21).
Embedding calls are not generation calls, so they don't go through app/llm.py; they are counted in EMBED_CALLS.
"""
import os
import time
from functools import lru_cache

import numpy as np

MODEL = "gemini-embedding-001"
DIM = 768                  # truncated from 3072, so vectors are renormalized below
TASK = "SEMANTIC_SIMILARITY"  # one task type for both triage and retrieval
MAX_CHARS = 6000           # the model reads only the first 2,048 tokens anyway
BATCH_CHARS = 40_000       # keeps each request under the 20k-token limit
BATCH_MAX = 250            # max texts per request

_client = None
EMBED_CALLS = 0            # API requests made by this process (for budget tracking)


def _get_client():
    global _client
    if _client is None:
        from google import genai
        from google.genai import types

        http = types.HttpOptions(timeout=60_000)  # ms
        key = os.getenv("GEMINI_API_KEY")
        if key:
            _client = genai.Client(api_key=key, http_options=http)
        else:
            _client = genai.Client(vertexai=True, project=os.environ["GOOGLE_CLOUD_PROJECT"],
                                   location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
                                   http_options=http)
    return _client


def ticket_text(subject, body) -> str:
    """Same text recipe everywhere (matches core_kb.parquet `text`); handles subject=None."""
    return f"{(subject or '').strip()}\n{(body or '').strip()}".strip()


def _batches(texts):
    batch, size = [], 0
    for t in texts:
        if batch and (size + len(t) > BATCH_CHARS or len(batch) >= BATCH_MAX):
            yield batch
            batch, size = [], 0
        batch.append(t)
        size += len(t)
    if batch:
        yield batch


def _call(batch, retries=6):
    global EMBED_CALLS
    from google.genai import types

    for attempt in range(retries):
        try:
            EMBED_CALLS += 1
            r = _get_client().models.embed_content(
                model=MODEL,
                contents=batch,
                config=types.EmbedContentConfig(task_type=TASK, output_dimensionality=DIM),
            )
            return [e.values for e in r.embeddings]
        except Exception as e:  # rate limits / transient errors
            if attempt == retries - 1:
                raise
            wait = 2 ** attempt
            print(f"embed retry {attempt + 1} in {wait}s: {type(e).__name__}", flush=True)
            time.sleep(wait)


def embed_texts(texts: list[str]) -> np.ndarray:
    """Batch embed -> (n, DIM) float32, L2-normalized (dot product = cosine)."""
    texts = [((t or "").strip() or " ")[:MAX_CHARS] for t in texts]
    vecs = []
    for b in _batches(texts):
        vecs.extend(_call(b))
    x = np.asarray(vecs, dtype=np.float32)
    return x / np.clip(np.linalg.norm(x, axis=1, keepdims=True), 1e-12, None)


@lru_cache(maxsize=4096)
def _embed_cached(text: str) -> np.ndarray:
    return embed_texts([text])[0]


def embed(text: str) -> np.ndarray:
    """Single live ticket. Cached, so triage and retrieval share one API call."""
    return _embed_cached(text)
