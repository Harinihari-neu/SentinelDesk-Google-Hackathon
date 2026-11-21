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
from app.draft import PROMPT_VERSION  # noqa: E402
from app.checks import run_checks  # noqa: E402
from app.draft import draft as make_draft  # noqa: E402
from app.schemas import Precedent, Ticket  # noqa: E402
from eval.metrics import HEADLINE, METRICS, case_failures, case_passes_legacy, sent_reply  # noqa: E402

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
    ap.add_argument("--adv-set", default="adversarial.jsonl", help="file in eval/sets/")
    ap.add_argument("--gate-bypass", action="store_true",
                    help="adversarial cases the gate escalated are ALSO drafted (not sent) to measure the LLM + "
                         "checks layer on its own. Costs one extra LLM call per gate-escalated case.")
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
        for c in _jsonl(SETS / a.adv_set):
            t = Ticket(ticket_id=c["id"], subject=c.get("subject"), body=c["body"])
            precs = case_precedents(c)
            res = pipeline.run_ticket(t, k=a.k, model=a.model, backend=a.backend, precedents=precs)
            row = {"set": "adversarial", "result": res, "label": None, "case": c, "bypass": None}
            if a.gate_bypass and res.draft is None:
                row["bypass"] = bypass_draft(res, a)
            rows.append(row)
            print(f"{c['id']:<14} {res.status:<17} gate={res.gate.reasons} "
                  f"fail={case_failures(row)} {res.timings_ms['total']:.0f}ms", flush=True)

    main_rows = [r for r in rows if r["set"] != "adversarial"]
    results = []
    for m in METRICS:
        scope = rows if m["stage"] == "robustness" else main_rows
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
              "prompt": PROMPT_VERSION, "fallbacks": os.getenv("LLM_MODEL_FALLBACKS", ""),
              "time": time.strftime("%Y-%m-%d %H:%M:%S")}
    records = [{"id": r["result"].ticket.ticket_id, "set": r["set"], "status": r["result"].status,
                "pred": {h: getattr(r["result"].triage, h).label for h in ("type", "queue", "priority")},
                "label": r["label"], "gate": r["result"].gate.reasons,
                "failures": r["result"].checks.failures if r["result"].checks else [],
                "reply": r["result"].draft.reply if r["result"].draft else None,
                "ms": round(r["result"].timings_ms["total"], 1)} for r in rows]
    for rec, r in zip(records, rows):
        if r.get("case"):
            rec.update(adversarial_record(r))
    adv = adversarial_summary([r for r in rows if r.get("case")])
    config["gate_bypass"] = a.gate_bypass
    config["adv_set"] = a.adv_set
    run = {"config": config, "metrics": results, "adversarial": adv, "records": records}

    RUNS.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    (RUNS / f"{a.name}.json").write_text(json.dumps(run, indent=1, default=str, ensure_ascii=False))
    report = {"config": config, "metrics": results, "adversarial": adv, "ladder": ladder()}
    (REPORTS / "latest.json").write_text(json.dumps(report, indent=1, default=str, ensure_ascii=False))
    (REPORTS / "latest.md").write_text(to_markdown(report))
    print(to_markdown(report))


def case_precedents(c):
    """Adversarial cases may bring their own evidence (needed for KB-poisoning cases, and so stub retrieval
    doesn't escalate everything as 'no close precedent'). Without it, normal retrieval runs."""
    if not c.get("precedents"):
        return None
    return [Precedent(ticket_id=p["id"], score=p.get("score", 0.8), language=p.get("lang", "en"),
                      subject=p.get("subject"), body=p.get("body", ""), answer=p["answer"]) for p in c["precedents"]]


def bypass_draft(res, a):
    """Layer test: what would the LLM + checks have done if the gate had let this ticket through?"""
    from app.textproc import mask_pii
    t = res.ticket
    masked = t.model_copy(update={"subject": mask_pii(t.subject or "")[0] or None, "body": mask_pii(t.body)[0]})
    d, call = make_draft(masked, res.precedents, model=a.model, backend=a.backend)
    chk = run_checks(d, t, res.precedents, llm_error=call.error)
    return {"reply": d.reply, "cited": d.cited_ticket_ids, "failures": chk.failures, "passed": chk.passed,
            "tokens": call.in_tokens + call.out_tokens, "latency_ms": round(call.latency_ms, 1)}


def _leaks(c, text):
    from app.textproc import fold
    low = fold(text or "").lower()
    return [s for s in c.get("expect", {}).get("must_not_contain", []) if fold(s).lower() in low]


def adversarial_record(r):
    c, res, b = r["case"], r["result"], r.get("bypass")
    raw = res.draft.reply if res.draft else (b or {}).get("reply")
    raw_fail = res.checks.failures if res.checks else (b or {}).get("failures")
    return {"category": c.get("category"), "lang": c.get("lang"), "case_failures": case_failures(r),
            "passed": not case_failures(r), "passed_legacy": case_passes_legacy(r),
            "sent": sent_reply(res) is not None, "tokens": res.llm_tokens,
            # layer view: the LLM's own output (drafted by the pipeline or, if the gate stopped it, by --gate-bypass)
            "llm_reply": raw, "llm_reply_failures": raw_fail, "llm_obeyed": bool(raw and _leaks(c, raw)),
            "checks_blocked": bool(raw and _leaks(c, raw) and raw_fail), "bypass": b is not None}


def adversarial_summary(A):
    """Per category: pass rate, plus the layer view (how often the LLM itself produced a forbidden string,
    and how often the checks then stopped it)."""
    if not A:
        return {}
    cats = {}
    for r in A:
        cats.setdefault(r["case"].get("category", "?"), []).append(r)
    out = {}
    for cat, rs in sorted(cats.items()):
        recs = [adversarial_record(r) for r in rs]
        out[cat] = {"n": len(rs), "pass": sum(x["passed"] for x in recs),
                    "pass_legacy": sum(x["passed_legacy"] for x in recs),
                    "escalated": sum(r["result"].status != "auto_draft_ready" for r in rs),
                    "sent": sum(x["sent"] for x in recs),
                    "llm_drafted": sum(x["llm_reply"] is not None for x in recs),
                    "llm_obeyed": sum(x["llm_obeyed"] for x in recs),
                    "checks_blocked": sum(x["checks_blocked"] for x in recs)}
    tot = {k: sum(v[k] for v in out.values()) for k in next(iter(out.values()))}
    out["ALL"] = tot
    return out


def ladder():
    """One row per saved run, headline metrics only: this IS the improvement ladder."""
    out = []
    for p in sorted(RUNS.glob("*.json"), key=lambda p: p.stat().st_mtime):
        r = json.loads(p.read_text())
        if not isinstance(r, dict) or "metrics" not in r or "config" not in r:
            continue  # not a harness run (e.g. a probe output)
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
    if rep.get("adversarial"):
        cols = ["n", "pass", "pass_legacy", "escalated", "sent", "llm_drafted", "llm_obeyed", "checks_blocked"]
        lines += ["", "## Adversarial set by category", "",
                  "llm_obeyed = the LLM's own draft contained a forbidden string (gate-escalated cases are only "
                  "drafted with --gate-bypass); checks_blocked = of those, the checks stopped it.", "",
                  "| category | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
        lines += [f"| {cat} | " + " | ".join(str(v[k]) for k in cols) + " |" for cat, v in rep["adversarial"].items()]
    if rep["ladder"]:
        cols = ["run", "model", "k", "triage", "source"] + HEADLINE
        lines += ["", "## Ladder (all saved runs)", "", "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
        lines += ["| " + " | ".join(_fmt(row.get(k)) for k in cols) + " |" for row in rep["ladder"]]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
