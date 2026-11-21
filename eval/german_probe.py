"""German vs English drafting probe. Generation only: bypasses triage and the gate.

    python eval/german_probe.py --name german-probe

Uses eval/sets/translation_pairs.jsonl: 10 hand-written ticket pairs (same meaning in EN and DE),
each with one hand-written precedent in both languages. Three conditions per pair:
  en-en: English ticket, English precedents
  de-de: German ticket, German precedents
  de-en: German ticket, English precedents (cross-lingual: the likely real case)
Each ticket gets its own precedent plus the next two pairs' precedents as distractors (k=3).
Writes eval/runs/probes/<name>.json with every draft, check failures, tokens and latency.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.checks import run_checks  # noqa: E402
from app.draft import draft  # noqa: E402
from app.schemas import Precedent, Ticket  # noqa: E402
from app.textproc import detect_language  # noqa: E402

CONDITIONS = [("en-en", "en", "en"), ("de-de", "de", "de"), ("de-en", "de", "en")]


def precedent(pair, lang):
    p = pair["prec"][lang]
    return Precedent(ticket_id=pair["prec"]["id"], score=1.0, language=lang, **p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="german-probe")
    ap.add_argument("--model", default=None)
    ap.add_argument("--sleep", type=float, default=0, help="extra seconds between tickets (pacing is LLM_MIN_INTERVAL_S)")
    a = ap.parse_args()

    pairs = [json.loads(l) for l in (ROOT / "eval/sets/translation_pairs.jsonl").read_text().splitlines() if l.strip()]
    recs = []
    for cond, t_lang, p_lang in CONDITIONS:
        for i, pair in enumerate(pairs):
            src = pair[t_lang]
            detected = detect_language(f"{src['subject']} {src['body']}")
            t = Ticket(ticket_id=f"{pair['id']}-{t_lang}", subject=src["subject"], body=src["body"], language=detected)
            precs = [precedent(pairs[(i + j) % len(pairs)], p_lang) for j in range(3)]
            if a.sleep:
                time.sleep(a.sleep)
            d, call = draft(t, precs, model=a.model)
            chk = run_checks(d, t, precs, llm_error=call.error)
            recs.append(dict(cond=cond, id=pair["id"], trap=pair["trap"], ticket_lang=t_lang, detected=detected,
                             own_precedent=pair["prec"]["id"], cited=d.cited_ticket_ids, reply=d.reply,
                             parse_ok=call.parse_ok, error=call.error, failures=chk.failures, passed=chk.passed,
                             in_tokens=call.in_tokens, out_tokens=call.out_tokens, latency_ms=round(call.latency_ms, 1),
                             model=call.model, error_kind=call.error_kind, attempts=call.attempts,
                             errors_seen=call.errors_seen, fallback_from=call.fallback_from))
            print(f"{cond} {pair['id']} pass={chk.passed} {chk.failures} {call.latency_ms:.0f}ms", flush=True)

    summary = {}
    for cond, *_ in CONDITIONS:
        rs = [r for r in recs if r["cond"] == cond]
        lat = sorted(r["latency_ms"] for r in rs)
        summary[cond] = dict(
            n=len(rs), api_errors=sum(r['error_kind'] not in (None, 'parse') for r in rs), retried=sum(r['attempts'] > 1 for r in rs), parse=sum(r["parse_ok"] for r in rs), checks_pass=sum(r["passed"] for r in rs),
            lang_mismatch=sum(any(f.startswith("language_mismatch") for f in r["failures"]) for r in rs),
            cited_own=sum(r["own_precedent"] in r["cited"] for r in rs),
            trap_pass=f"{sum(r['passed'] for r in rs if r['trap'])}/{sum(r['trap'] for r in rs)}",
            in_tok_mean=round(statistics.mean(r["in_tokens"] for r in rs), 1),
            out_tok_mean=round(statistics.mean(r["out_tokens"] for r in rs), 1),
            reply_words_mean=round(statistics.mean(len(r["reply"].split()) for r in rs), 1),
            latency_p50_ms=round(statistics.median(lat), 1), latency_max_ms=lat[-1])
    out = ROOT / "eval/runs/probes" / f"{a.name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(dict(name=a.name, model=recs[0]["model"], summary=summary, records=recs),
                              indent=1, ensure_ascii=False))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
