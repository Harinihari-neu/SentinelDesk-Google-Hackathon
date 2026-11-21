"""Model / prompt / context benchmark for the drafting step (Task 2). Generation only: the gate is bypassed,
so every sampled ticket gets a draft and every row sees the same 40 tickets.

    python eval/bench_models.py sample                       # show the 40-ticket sample (no LLM)
    python eval/bench_models.py run --row base --name bench1 # one grid row (paced; resumable)
    python eval/bench_models.py touch --n 2000               # llm_touch_share: stub triage + real gate (no LLM)
    python eval/bench_models.py report --name bench1         # tables -> eval/runs/bench/<name>/report.md

Queries: 20 EN + 20 DE holdout tickets (seed 7, detect_language), KB twins excluded (data/holdout_kb_twins.txt).
Precedents: TEMPORARY TF-IDF retriever over core_kb.parquet split == "train" with an answer, because app/retrieval.py
does not exist yet. Dev rows (= data/kb_dev_ids.txt) stay out of the index so dev can tune thresholds honestly.
TF-IDF has no cross-lingual matching: German tickets mostly get German precedents, and weak ones. Report DE separately.
Outputs: eval/runs/bench/<name>/<row>.jsonl (gitignored). Labels are never read.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from app import llm  # noqa: E402  (importing app loads .env)
from app.checks import run_checks  # noqa: E402
from app.draft import draft as make_draft, reuse_draft  # noqa: E402
from app.schemas import Precedent, Ticket  # noqa: E402
from app.textproc import detect_language, mask_pii  # noqa: E402

SEED, N_PER_LANG = 7, 20
DATA, OUT = ROOT / "data", ROOT / "eval" / "runs" / "bench"
BASE = dict(model="gemini-3.5-flash-lite", prompt="v1.1", k=3, answer_chars=600, max_out=1024, thinking=None,
            drafter="llm", deadline=90)
# One change per row vs BASE. cap-* rows get max_out from the CLI (set from the baseline's longest output).
ROWS = {
    "base": {}, "base-rep": {},
    "reuse": dict(drafter="reuse"),
    "m-3.1-lite": dict(model="gemini-3.1-flash-lite"),
    "m-gemma-26b": dict(model="gemma-4-26b-a4b-it"),
    "m-gemma-31b": dict(model="gemma-4-31b-it"),
    "m-3.5-flash-min": dict(model="gemini-3.5-flash", thinking="minimal", deadline=300),
    "m-3.5-flash-default": dict(model="gemini-3.5-flash", deadline=300),
    "p-v2": dict(prompt="v2"),
    "k5": dict(k=5),
    "trunc300": dict(answer_chars=300),
    "cap-fit": {}, "cap-tight": {},
}
# Env that changes results. Pinned per row so nothing in .env leaks into a row silently.
PINNED = ["LLM_THINKING_LEVEL", "LLM_THINKING_BUDGET", "LLM_MODEL_FALLBACKS", "LLM_FALLBACK_MODEL"]


# ---------------- temporary retriever ----------------
_TOK = re.compile(r"\w{2,}")


class TfidfIndex:
    """Plain-Python TF-IDF + cosine (no sklearn in the venv). TEMPORARY until app/retrieval.py lands."""

    def __init__(self, df: pd.DataFrame):
        self.rows = df.reset_index(drop=True)
        docs = [Counter(_TOK.findall(t.lower())) for t in self.rows["text"].fillna("")]
        n = len(docs)
        dfc = Counter(w for d in docs for w in d)
        self.idf = {w: math.log((n + 1) / (c + 1)) + 1 for w, c in dfc.items()}
        self.post = defaultdict(list)
        for i, d in enumerate(docs):
            vec = {w: (1 + math.log(c)) * self.idf[w] for w, c in d.items()}
            norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
            for w, v in vec.items():
                self.post[w].append((i, v / norm))

    def search(self, text: str, k: int) -> list:
        q = Counter(_TOK.findall(text.lower()))
        vec = {w: (1 + math.log(c)) * self.idf[w] for w, c in q.items() if w in self.idf}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        acc = defaultdict(float)
        for w, v in vec.items():
            for i, dv in self.post[w]:
                acc[i] += v / norm * dv
        out = []
        for i, s in sorted(acc.items(), key=lambda x: -x[1])[:k]:
            r = self.rows.iloc[i]
            out.append(Precedent(ticket_id=r.ticket_id, score=round(s, 4), language=r.language,
                                 subject=r.subject if isinstance(r.subject, str) else None,
                                 body=r.body if isinstance(r.body, str) else "", answer=r.answer,
                                 queue=r.queue, type=r.type))
        return out


def load_index() -> TfidfIndex:
    kb = pd.read_parquet(DATA / "core_kb.parquet")
    kb = kb[(kb["split"] == "train") & kb["answer"].notna() & (kb["answer"].fillna("").str.strip() != "")]
    return TfidfIndex(kb)  # rows with no answer can't be precedents


# ---------------- sample ----------------
def load_sample() -> list:
    X = pd.read_parquet(DATA / "holdout_inputs.parquet")
    twins = set((DATA / "holdout_kb_twins.txt").read_text().split())
    X = X[~X.ticket_id.isin(twins)].sample(frac=1.0, random_state=SEED)
    picked = {"en": [], "de": []}
    for r in X.itertuples():
        t = Ticket(ticket_id=r.ticket_id, subject=r.subject if isinstance(r.subject, str) and r.subject else None,
                   body=r.body)
        lang = detect_language(f"{t.subject or ''} {t.body}")
        if lang in picked and len(picked[lang]) < N_PER_LANG:
            picked[lang].append(t.model_copy(update={"language": lang}))
        if all(len(v) == N_PER_LANG for v in picked.values()):
            break
    return picked["en"] + picked["de"]


# ---------------- run one row ----------------
def run_row(row: str, name: str, max_out: int | None, row_wall_s: float, limit: int | None):
    cfg = {**BASE, **ROWS[row]}
    if row.startswith("cap-"):
        if not max_out:
            sys.exit("cap-* rows need --max-out")
        cfg["max_out"] = max_out
    for k in PINNED:
        os.environ.pop(k, None)
    if cfg["thinking"]:
        os.environ["LLM_THINKING_LEVEL"] = cfg["thinking"]
    os.environ["LLM_DEADLINE_S"] = str(cfg["deadline"])
    os.environ["LLM_TIMEOUT_S"] = str(max(30, cfg["deadline"]))

    out_dir = OUT / name
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{row}.jsonl"
    done = {json.loads(line)["ticket_id"] for line in path.open()} if path.exists() else set()
    tickets = load_sample()[:limit] if limit else load_sample()
    index = load_index()
    t_row = time.time()
    with path.open("a") as f:
        for t in tickets:
            if t.ticket_id in done:
                continue
            if time.time() - t_row > row_wall_s:
                print(f"[{row}] stopped: row wall time {row_wall_s}s reached", flush=True)
                break
            prec = index.search(f"{t.subject or ''} {t.body}", cfg["k"])
            masked = t.model_copy(update={"subject": mask_pii(t.subject or "")[0] or None,
                                          "body": mask_pii(t.body)[0]})
            if cfg["drafter"] == "reuse":
                d, call = reuse_draft(masked, prec)
            else:
                d, call = make_draft(masked, prec, model=cfg["model"], prompt_version=cfg["prompt"],
                                     answer_chars=cfg["answer_chars"], max_output_tokens=cfg["max_out"])
            chk = run_checks(d, t, prec, llm_error=call.error)
            priced = cfg["drafter"] == "reuse" or call.model in llm.PRICES
            rec = dict(row=row, config=cfg, ticket_id=t.ticket_id, lang=t.language, model_used=call.model,
                       parse_ok=call.parse_ok, error_kind=call.error_kind, error=(call.error or "")[:200],
                       errors_seen=call.errors_seen, attempts=call.attempts, fallback_from=call.fallback_from,
                       in_tokens=call.in_tokens, out_tokens=call.out_tokens, latency_ms=round(call.latency_ms),
                       cost_usd=call.cost_usd if priced else None, passed=chk.passed, failures=chk.failures,
                       reply=d.reply, cited=d.cited_ticket_ids,
                       precedents=[(p.ticket_id, p.language, p.score) for p in prec])
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            print(f"[{row}] {t.ticket_id} {t.language} ok={chk.passed} err={call.error_kind} "
                  f"{call.latency_ms:.0f}ms in={call.in_tokens} out={call.out_tokens} {chk.failures[:3]}", flush=True)
    print(f"[{row}] done in {time.time() - t_row:.0f}s -> {path}", flush=True)


# ---------------- llm_touch_share (no LLM) ----------------
def touch(n: int):
    """Share of holdout tickets the real gate lets through to drafting, with stub triage and TF-IDF retrieval."""
    from app.gate import decide
    from app.pipeline import _triage

    X = pd.read_parquet(DATA / "holdout_inputs.parquet").sample(n=n, random_state=SEED)
    index = load_index()
    reasons, through, top1 = Counter(), 0, []
    for r in X.itertuples():
        t = Ticket(ticket_id=r.ticket_id, subject=r.subject if isinstance(r.subject, str) else None, body=r.body)
        t = t.model_copy(update={"language": detect_language(f"{t.subject or ''} {t.body}")})
        prec = index.search(f"{t.subject or ''} {t.body}", 3)
        top1.append(prec[0].score if prec else 0.0)
        g = decide(t, _triage(t), prec)
        through += not g.escalate
        reasons.update(x.split(":")[0] for x in g.reasons)
    res = dict(n=n, seed=SEED, llm_touch_share=round(through / n, 4), top1_similarity_median=round(
        statistics.median(top1), 4), escalation_reasons=dict(reasons.most_common()))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"touch-{n}.json").write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2))


# ---------------- report ----------------
def _pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else None


def summarize(recs: list) -> dict:
    n = len(recs)
    if not n:
        return {}
    drafts = [r for r in recs if r["parse_ok"]]
    nd = len(drafts) or 1

    def has(r, p):
        return any(f.startswith(p) for f in r["failures"])
    costs = [r["cost_usd"] for r in recs]
    priced = all(c is not None for c in costs)
    total = sum(costs) if priced else None
    passing = sum(r["passed"] for r in recs)
    seen = Counter(e for r in recs for e in r["errors_seen"])
    lat = [r["latency_ms"] for r in recs if r["parse_ok"]]
    return dict(
        n=n, parse=len(drafts) / n,
        lang_match=sum(not has(r, "language_mismatch") for r in drafts) / nd,
        unsupported=sum(has(r, "unsupported_") for r in drafts) / nd,
        citation=sum(not (has(r, "no_citations") or has(r, "invalid_citation")) for r in drafts) / nd,
        checks_pass=passing / n,
        p50=_pct(lat, 0.5), p95=_pct(lat, 0.95),
        tok_in=statistics.mean(r["in_tokens"] for r in recs), tok_out=statistics.mean(r["out_tokens"] for r in recs),
        tok_out_max=max(r["out_tokens"] for r in recs),
        cost_per_ticket=(total / n) if priced else None,
        cost_per_pass=(total / passing if passing else None) if priced else None,
        n429=seen.get("rate_limited", 0), n503=seen.get("unavailable", 0),
        errors=dict(Counter(r["error_kind"] for r in recs if r["error_kind"])),
        fallback=sum(bool(r["fallback_from"]) for r in recs),
        failures=dict(Counter(f.split(":")[0] for r in recs for f in r["failures"]).most_common(6)),
    )


def bar(s) -> bool:
    return bool(s) and s["parse"] == 1.0 and s["lang_match"] >= 0.95 and s["unsupported"] <= 0.05 and s["citation"] >= 0.95


def report(name: str, touch_share: float | None):
    d = OUT / name
    rows = {p.stem: [json.loads(x) for x in p.open()] for p in sorted(d.glob("*.jsonl"))}
    order = [r for r in ROWS if r in rows] + [r for r in rows if r not in ROWS]
    f = lambda v, fmt="{:.0%}": "n/a" if v is None else fmt.format(v)  # noqa: E731
    lines = []
    for scope in ("all", "en", "de"):
        lines += [f"\n### {scope.upper()}\n",
                  "| row | model | prompt | k | ans chars | max out | n | parse | lang | unsupp. | cite | pass | "
                  "p50/p95 s | tok in/out | $/1k tickets | $/passing draft | $/1M tickets | bar |",
                  "|" + "---|" * 18]
        for row in order:
            recs = [r for r in rows[row] if scope == "all" or r["lang"] == scope]
            s = summarize(recs)
            if not s:
                continue
            c = recs[0]["config"]
            per1m = s["cost_per_ticket"] * 1e6 * touch_share if s["cost_per_ticket"] is not None and touch_share else None
            lines.append(
                f"| {row} | {c['model'] if c['drafter'] == 'llm' else 'reuse (no LLM)'} | {c['prompt']} | {c['k']} | "
                f"{c['answer_chars']} | {c['max_out']} | {s['n']} | {f(s['parse'])} | {f(s['lang_match'])} | "
                f"{f(s['unsupported'])} | {f(s['citation'])} | {f(s['checks_pass'])} | "
                f"{f(s['p50'] and s['p50'] / 1000, '{:.1f}')}/{f(s['p95'] and s['p95'] / 1000, '{:.1f}')} | "
                f"{s['tok_in']:.0f}/{s['tok_out']:.0f} | {f(s['cost_per_ticket'] and s['cost_per_ticket'] * 1000, '${:.3f}')} | "
                f"{f(s['cost_per_pass'], '${:.5f}')} | {f(per1m, '${:,.0f}')} | {'yes' if bar(s) else 'no'} |")
    lines += ["\n### Errors and failures (all tickets)\n", "| row | 429 seen | 503 seen | final errors | fallback | "
              "max out tokens | top check failures |", "|---|---|---|---|---|---|---|"]
    for row in order:
        s = summarize(rows[row])
        lines.append(f"| {row} | {s['n429']} | {s['n503']} | {s['errors'] or '-'} | {s['fallback']} | {s['tok_out_max']} | "
                     f"{s['failures'] or '-'} |")
    md = "\n".join(lines)
    (d / "report.md").write_text(md)
    print(md)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sample")
    r = sub.add_parser("run")
    r.add_argument("--row", required=True, choices=list(ROWS))
    r.add_argument("--name", required=True)
    r.add_argument("--max-out", type=int)
    r.add_argument("--row-wall-s", type=float, default=3600)
    r.add_argument("--limit", type=int)
    t = sub.add_parser("touch")
    t.add_argument("--n", type=int, default=2000)
    p = sub.add_parser("report")
    p.add_argument("--name", required=True)
    p.add_argument("--touch-share", type=float)
    a = ap.parse_args()
    if a.cmd == "sample":
        for t in load_sample():
            print(t.ticket_id, t.language, (t.subject or "")[:60], "|", t.body[:80].replace("\n", " "))
    elif a.cmd == "run":
        run_row(a.row, a.name, a.max_out, a.row_wall_s, a.limit)
    elif a.cmd == "touch":
        touch(a.n)
    else:
        report(a.name, a.touch_share)


if __name__ == "__main__":
    main()
