"""Metric registry. Owned by AI engineering.

Every metric declares the stage it measures and the DECISION it drives. No decision, no metric.
A metric returns a float, or None when it can't be computed for this run (shown as n/a).

Each row passed to a metric is a dict:
  {"result": PipelineResult, "label": dict | None, "case": dict | None, "set": "holdout" | "adversarial" | ...}
"""
from __future__ import annotations

from typing import Callable, Optional

METRICS: list = []
HEADLINE = ["hp_incident_recall", "missed_critical_rate", "auto_draft_share", "unsupported_claim_rate",
            "llm_touch_share", "cost_per_1m_tickets_usd"]


def metric(name: str, stage: str, decision: str, higher_is_better: bool = True):
    def deco(fn: Callable):
        METRICS.append({"name": name, "stage": stage, "decision": decision,
                        "higher_is_better": higher_is_better, "fn": fn})
        return fn
    return deco


# ---------- helpers ----------
def _labeled(rows):
    return [r for r in rows if r.get("label")]


def _drafts(rows):
    return [r for r in rows if r["result"].draft is not None]


def _rate(num, den) -> Optional[float]:
    return None if den == 0 else num / den


def macro_f1(y_true, y_pred) -> Optional[float]:
    if not y_true:
        return None
    f1s = []
    for c in set(y_true):
        tp = sum(t == c and p == c for t, p in zip(y_true, y_pred))
        fp = sum(t != c and p == c for t, p in zip(y_true, y_pred))
        fn = sum(t == c and p != c for t, p in zip(y_true, y_pred))
        f1s.append(0.0 if tp == 0 else 2 * tp / (2 * tp + fp + fn))
    return sum(f1s) / len(f1s)


def _pct(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]


def _has_failure(r, prefix):
    c = r["result"].checks
    return bool(c and any(f.startswith(prefix) for f in c.failures))


# ---------- triage ----------
for _head in ("type", "queue", "priority"):
    def _f(rows, h=_head):
        L = _labeled(rows)
        return macro_f1([r["label"][h] for r in L], [getattr(r["result"].triage, h).label for r in L])
    metric(f"macro_f1_{_head}", "triage", f"Which {_head} classifier rung we keep")(_f)


def _is_hp_incident(label):
    return label["priority"] == "high" and label["type"] == "Incident"


@metric("hp_incident_recall", "triage", "Safety floor: gate thresholds are set to protect it")
def hp_incident_recall(rows):
    L = [r for r in _labeled(rows) if _is_hp_incident(r["label"])]
    return _rate(sum(r["result"].triage.priority.label == "high" for r in L), len(L))


@metric("ece_priority", "triage", "Can priority confidence drive the gate, or calibrate first?", False)
def ece_priority(rows, bins=10):
    L = _labeled(rows)
    if not L:
        return None
    total, err = len(L), 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        sel = [r for r in L if lo < r["result"].triage.priority.confidence <= hi or (b == 0 and r["result"].triage.priority.confidence == 0)]
        if sel:
            acc = sum(r["result"].triage.priority.label == r["label"]["priority"] for r in sel) / len(sel)
            conf = sum(r["result"].triage.priority.confidence for r in sel) / len(sel)
            err += len(sel) / total * abs(acc - conf)
    return err


@metric("queue_f1_gap_en_de", "triage", "Does German need separate handling?", False)
def queue_f1_gap(rows):
    by = {}
    for lang in ("en", "de"):
        L = [r for r in _labeled(rows) if r["label"].get("language") == lang]
        by[lang] = macro_f1([r["label"]["queue"] for r in L], [r["result"].triage.queue.label for r in L])
    return None if None in by.values() else abs(by["en"] - by["de"])


# ---------- gate ----------
@metric("missed_critical_rate", "gate", "Hard limit: must be ~0, else tighten the gate", False)
def missed_critical(rows):
    L = [r for r in _labeled(rows) if _is_hp_incident(r["label"])]
    return _rate(sum(r["result"].status == "auto_draft_ready" for r in L), len(L))


@metric("auto_draft_share", "gate", "Automation achieved at the chosen threshold (ROI)")
def auto_draft_share(rows):
    return _rate(sum(r["result"].status == "auto_draft_ready" for r in rows), len(rows))


@metric("llm_touch_share", "system", "Proves 'LLM last': lower = cheaper at scale", False)
def llm_touch_share(rows):
    return _rate(len(_drafts(rows)), len(rows))


# ---------- retrieval ----------
@metric("top1_similarity_median", "retrieval", "Sets the no-precedent escalation threshold")
def top1_sim(rows):
    return _pct([r["result"].precedents[0].score for r in rows if r["result"].precedents], 0.5)


@metric("topk_queue_agreement", "retrieval", "Is queue filtering / reranking worth it?")
def topk_queue_agreement(rows):
    L = _labeled(rows)
    pairs = [(p.queue, r["label"]["queue"]) for r in L for p in r["result"].precedents if p.queue]
    return _rate(sum(a == b for a, b in pairs), len(pairs))


# ---------- generation ----------
@metric("schema_parse_rate", "generation", "Is the output format reliable enough?")
def schema_parse(rows):
    D = _drafts(rows)
    return _rate(sum(not (_has_failure(r, "schema_invalid") or _has_failure(r, "llm_error")) for r in D), len(D))


@metric("citation_validity", "generation", "Pass/fail per draft; prompt or model change if low")
def citation_validity(rows):
    D = _drafts(rows)
    return _rate(sum(not (_has_failure(r, "no_citations") or _has_failure(r, "invalid_citation")) for r in D), len(D))


@metric("unsupported_claim_rate", "generation", "Which model size and k we keep; step-6 trigger", False)
def unsupported_claims(rows):
    D = _drafts(rows)
    return _rate(sum(_has_failure(r, "unsupported_") for r in D), len(D))


@metric("language_match_rate", "generation", "Pass/fail per draft; reply-language handling")
def language_match(rows):
    D = _drafts(rows)
    return _rate(sum(not _has_failure(r, "language_mismatch") for r in D), len(D))


@metric("checks_pass_rate", "generation", "Share of drafts shipped without human edit")
def checks_pass(rows):
    D = _drafts(rows)
    return _rate(sum(r["result"].checks.passed for r in D if r["result"].checks), len(D))


# ---------- robustness ----------
def case_passes(r) -> bool:
    exp, res = r["case"].get("expect", {}), r["result"]
    reply = (res.draft.reply if res.draft else "").lower()
    shipped = res.status == "auto_draft_ready"
    if "escalate" in exp and exp["escalate"] != (not shipped):
        return False
    if shipped:
        if any(s.lower() in reply for s in exp.get("must_not_contain", [])):
            return False
        if any(s.lower() not in reply for s in exp.get("must_contain", [])):
            return False
        if exp.get("reply_lang") and res.draft.language != exp["reply_lang"]:
            return False
    return True


@metric("adversarial_pass_rate", "robustness", "Are guardrails demo-ready?")
def adversarial(rows):
    A = [r for r in rows if r.get("case")]
    return _rate(sum(case_passes(r) for r in A), len(A))


# ---------- operations ----------
@metric("latency_p50_ms", "ops", "What we show live; where to optimize", False)
def p50(rows):
    return _pct([r["result"].timings_ms["total"] for r in rows], 0.5)


@metric("latency_p95_ms", "ops", "Tail latency budget", False)
def p95(rows):
    return _pct([r["result"].timings_ms["total"] for r in rows], 0.95)


@metric("tokens_per_ticket", "ops", "Prompt size / k decisions", False)
def tokens(rows):
    return _rate(sum(r["result"].llm_tokens for r in rows), len(rows))


@metric("cost_per_1m_tickets_usd", "ops", "Model choice; scale story", False)
def cost_1m(rows):
    c = _rate(sum(r["result"].llm_cost_usd for r in rows), len(rows))
    return None if c is None else c * 1e6
