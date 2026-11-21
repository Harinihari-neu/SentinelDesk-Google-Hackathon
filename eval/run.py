"""Eval harness: one command -> run record + Trust Report. Owned by AI engineering.

    python eval/run.py --name baseline                 # holdout sample (needs data split), else smoke set
    python eval/run.py --name smoke --smoke            # force the built-in smoke set
    LLM_BACKEND=fake python eval/run.py --smoke        # fully offline

Writes eval/runs/<name>.json, eval/reports/latest.json and eval/reports/latest.md.
Holdout labels are read ONLY here, after the pipeline has produced its predictions.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import pipeline  # noqa: E402
from app.schemas import Ticket  # noqa: E402
from eval.metrics import HEADLINE, METRICS  # noqa: E402

SETS = ROOT / "eval" / "sets"
RUNS = ROOT / "eval" / "runs"
REPORTS = ROOT / "eval" / "reports"
SAMPLE_SEED = 7  # fixed so every run scores the same tickets


def _jsonl(path):
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []


def load_holdout(n, smoke):
    inputs, labels = ROOT / "data" / "holdout_inputs.parquet", ROOT / "data" / "holdout_labels.parquet"
    if smoke or not inputs.exists():
        rows = _jsonl(SETS / "smoke.jsonl")
        return [(Ticket(ticket_id=r["id"], subject=r.get("subject"), body=r["body"]), r.get("label")) for r in rows], "smoke"
    import pandas as pd

    X = pd.read_parquet(inputs)
    X = X.sample(n=min(n, len(X)), random_state=SAMPLE_SEED)
    tickets = [Ticket(ticket_id=r.ticket_id, subject=(r.subject if isinstance(r.subject, str) else None),
                      body=r.body) for r in X.itertuples()]
    return tickets, "holdout"


def attach_labels(tickets):
    """Read holdout labels only after predictions exist."""
    p = ROOT / "data" / "holdout_labels.parquet"
    if not p.exists():
        return {}
    import pandas as pd

    Y = pd.read_parquet(p).set_index("ticket_id")
    ids = [t.ticket_id for t in tickets if t.ticket_id in Y.index]
    return {i: Y.loc[i].to_dict() for i in ids}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default=time.strftime("run-%H%M%S"))
    ap.add_argument("--n", type=int, default=200, help="holdout sample size")
    ap.add_argument("--k", type=int, default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--backend", default=None)
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--no-adversarial", action="store_true")
    a = ap.parse_args()

    loaded, source = load_holdout(a.n, a.smoke)
    rows = []
    for t, lab in loaded:
        rows.append({"set": source, "result": pipeline.run_ticket(t, k=a.k, model=a.model, backend=a.backend),
                     "label": lab, "case": None})
    if source == "holdout":
        labels = attach_labels([t for t, _ in loaded])
        for r in rows:
            r["label"] = labels.get(r["result"].ticket.ticket_id)
    if not a.no_adversarial:
        for c in _jsonl(SETS / "adversarial.jsonl"):
            t = Ticket(ticket_id=c["id"], subject=c.get("subject"), body=c["body"])
            rows.append({"set": "adversarial", "result": pipeline.run_ticket(t, k=a.k, model=a.model, backend=a.backend),
                         "label": None, "case": c})

    main_rows = [r for r in rows if r["set"] != "adversarial"]
    results = []
    for m in METRICS:
        scope = rows if m["name"] == "adversarial_pass_rate" else main_rows
        try:
            v = m["fn"](scope)
        except Exception as e:  # a broken metric must not kill the report
            v, m = None, {**m, "decision": f"{m['decision']} [ERROR {type(e).__name__}: {e}]"}
        results.append({k: m[k] for k in ("name", "stage", "decision", "higher_is_better")} | {"value": v})

    config = {"name": a.name, "source": source, "n": len(main_rows), "n_adversarial": len(rows) - len(main_rows),
              "k": a.k or int(os.getenv("RAG_K", "3")), "backend": a.backend or os.getenv("LLM_BACKEND", "gemini"),
              "model": a.model or os.getenv("LLM_MODEL", "gemini-3.5-flash-lite"),
              "triage": pipeline.TRIAGE_SOURCE, "retrieval": pipeline.RETRIEVAL_SOURCE,
              "triage_version": rows[0]["result"].triage.model_version if rows else None,
              "time": time.strftime("%Y-%m-%d %H:%M:%S")}
    records = [{"id": r["result"].ticket.ticket_id, "set": r["set"], "status": r["result"].status,
                "pred": {h: getattr(r["result"].triage, h).label for h in ("type", "queue", "priority")},
                "label": r["label"], "gate": r["result"].gate.reasons,
                "failures": r["result"].checks.failures if r["result"].checks else [],
                "reply": r["result"].draft.reply if r["result"].draft else None,
                "ms": round(r["result"].timings_ms["total"], 1)} for r in rows]
    run = {"config": config, "metrics": results, "records": records}

    RUNS.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    (RUNS / f"{a.name}.json").write_text(json.dumps(run, indent=1, default=str, ensure_ascii=False))
    report = {"config": config, "metrics": results, "ladder": ladder()}
    (REPORTS / "latest.json").write_text(json.dumps(report, indent=1, default=str, ensure_ascii=False))
    (REPORTS / "latest.md").write_text(to_markdown(report))
    print(to_markdown(report))


def ladder():
    """One row per saved run, headline metrics only: this IS the improvement ladder."""
    out = []
    for p in sorted(RUNS.glob("*.json"), key=lambda p: p.stat().st_mtime):
        r = json.loads(p.read_text())
        vals = {m["name"]: m["value"] for m in r["metrics"]}
        out.append({"run": r["config"]["name"], "model": r["config"]["model"], "k": r["config"]["k"],
                    "triage": r["config"].get("triage_version"), "source": r["config"]["source"],
                    **{h: vals.get(h) for h in HEADLINE}})
    return out


def _fmt(v):
    return "n/a" if v is None else (f"{v:.3f}" if isinstance(v, float) else str(v))


def to_markdown(rep):
    c = rep["config"]
    lines = [f"# Trust Report: {c['name']}", "",
             f"Source **{c['source']}** (n={c['n']}, adversarial={c['n_adversarial']}) · model `{c['model']}` "
             f"({c['backend']}) · k={c['k']} · triage `{c['triage']}` · retrieval `{c['retrieval']}` · {c['time']}", "",
             "## Headline", ""]
    vals = {m["name"]: m["value"] for m in rep["metrics"]}
    lines += [f"- **{h}**: {_fmt(vals.get(h))}" for h in HEADLINE]
    lines += ["", "## All metrics", "", "| Stage | Metric | Value | Decision it drives |", "|---|---|---|---|"]
    lines += [f"| {m['stage']} | {m['name']} | {_fmt(m['value'])} | {m['decision']} |" for m in rep["metrics"]]
    if rep["ladder"]:
        cols = ["run", "model", "k", "triage", "source"] + HEADLINE
        lines += ["", "## Ladder (all saved runs)", "", "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
        lines += ["| " + " | ".join(_fmt(row.get(k)) for k in cols) + " |" for row in rep["ladder"]]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
