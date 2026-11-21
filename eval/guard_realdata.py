"""Guardrails on real data: how often each code detector fires on DS2's KB and holdout tickets.

No LLM calls, no labels. Outputs (gitignored) go to eval/runs/guard_realdata/<name>/:
  summary.json   every rate below
  samples_*.jsonl  seeded samples of flagged items, for hand-reading (false positive or not)

    python eval/guard_realdata.py --name guard-realdata-v0

Sections:
  1. precedent_signals() on KB rows (subject + body + answer, as the gate calls it), core_kb and kb
  2. gate guardrail reasons on holdout tickets (subject + body): injection, sensitive, unsupported_language,
     each RISKY_ASKS term (with the word forms that matched, to spot prefix false positives)
  3. ticket length (chars) and share above MAX_TICKET_CHARS
  4. link domains in KB answers -> proposed CONTACT_ALLOWLIST (not set here)
  5. KB answers treated as replies: credential_request, unsupported_links, mask_pii (= pii_in_reply).
     The reuse drafter sends exactly these answers, so this is its block rate on the output checks.
  6. mask_pii on holdout tickets: count per kind
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from app.draft import MAX_TICKET_CHARS  # noqa: E402
from app.gate import _RISKY_RX  # noqa: E402
from app.guard import _URL, credential_request, injection_signals, precedent_signals, sensitive_signals, \
    unsupported_links  # noqa: E402
from app.textproc import detect_language, fold, mask_pii  # noqa: E402

SEED = 7
DATA = ROOT / "data"


def _s(x) -> str:
    return x if isinstance(x, str) else ""


def _sample(rows: list, n: int) -> list:
    if len(rows) <= n:
        return rows
    return pd.DataFrame({"r": rows}).sample(n=n, random_state=SEED)["r"].tolist()


def _rate(k: int, n: int) -> dict:
    return {"n": k, "of": n, "rate": round(k / n, 5) if n else None}


def _ctx(text: str, start: int, end: int, w: int = 60) -> str:
    return text[max(0, start - w):end + w].replace("\n", " ")


def kb_precedent_scan(df: pd.DataFrame, label: str, out: Path) -> dict:
    hits, per = [], Counter()
    for r in df.itertuples():
        sig = precedent_signals(f"{_s(r.subject)} {_s(r.body)} {_s(r.answer)}")
        if sig:
            per.update(sig)
            hits.append({"ticket_id": r.ticket_id, "signals": sig, "subject": _s(r.subject), "body": _s(r.body),
                         "answer": _s(r.answer)})
    _dump(out / f"samples_precedent_{label}.jsonl", _sample(hits, 30))
    return {"rows": len(df), "flagged": _rate(len(hits), len(df)), "per_pattern": dict(per.most_common())}


def holdout_gate(X: pd.DataFrame, out: Path) -> dict:
    n = len(X)
    inj, sens, other, risky = [], [], [], {t: [] for t in _RISKY_RX}
    forms = {t: Counter() for t in _RISKY_RX}
    any_guard = 0
    for r in X.itertuples():
        raw = f"{_s(r.subject)} {_s(r.body)}"
        low = fold(raw).lower()
        item = {"ticket_id": r.ticket_id, "subject": _s(r.subject), "body": _s(r.body)}
        flagged = False
        sig = injection_signals(raw)
        if sig:
            inj.append({**item, "signals": sig}); flagged = True
        if sensitive_signals(raw):
            sens.append(item); flagged = True
        lang = detect_language(raw)
        if lang == "other":
            other.append(item); flagged = True
        any_guard += flagged
        for t, rx in _RISKY_RX.items():
            ms = list(rx.finditer(low))
            if ms:
                m = ms[0]
                word = re.match(r"\w*", low[m.start():]).group(0)
                forms[t][word] += 1
                risky[t].append({"ticket_id": r.ticket_id, "word": word, "context": _ctx(low, m.start(), m.end())})
    _dump(out / "samples_injection.jsonl", _sample(inj, 20))
    _dump(out / "samples_sensitive.jsonl", _sample(sens, 20))
    _dump(out / "samples_unsupported_language.jsonl", _sample(other, 20))
    _dump(out / "samples_risky.jsonl", [dict(term=t, **x) for t in risky for x in _sample(risky[t], 20)])
    any_risky = len({x["ticket_id"] for v in risky.values() for x in v})
    return {
        "tickets": n,
        "possible_injection": _rate(len(inj), n),
        "injection_per_pattern": dict(Counter(s for x in inj for s in x["signals"]).most_common()),
        "sensitive_content": _rate(len(sens), n),
        "unsupported_language": _rate(len(other), n),
        "any_guardrail_reason": _rate(any_guard, n),
        "risky_term_any": _rate(any_risky, n),
        "risky_terms": {t: {**_rate(len(v), n), "top_forms": dict(forms[t].most_common(8))}
                        for t, v in sorted(risky.items(), key=lambda kv: -len(kv[1]))},
    }


def lengths(X: pd.DataFrame) -> dict:
    body = X["body"].fillna("").str.len()
    full = (X["subject"].fillna("") + " " + X["body"].fillna("")).str.len()
    q = lambda s: {p: int(s.quantile(p / 100)) for p in (50, 95, 99)} | {"max": int(s.max())}  # noqa: E731
    return {"body_chars": q(body), "subject_plus_body_chars": q(full),
            "body_over_cap": _rate(int((body > MAX_TICKET_CHARS).sum()), len(X)), "cap": MAX_TICKET_CHARS}


def _host(u: str) -> str:
    return re.sub(r"^(?:https?://)?(?:www\.)?", "", u.lower()).split("/")[0]


def kb_as_reply(df: pd.DataFrame, label: str, out: Path) -> dict:
    n = len(df)
    domains, cred, links, pii, kinds = Counter(), [], [], [], Counter()
    for r in df.itertuples():
        a = _s(r.answer)
        for u in _URL.findall(fold(a)):
            domains[_host(u)] += 1
        item = {"ticket_id": r.ticket_id, "answer": a}
        if credential_request(a):
            cred.append(item)
        bad = unsupported_links(a)
        if bad:
            links.append({**item, "links": bad})
        masked, c = mask_pii(a)
        if c:
            kinds.update(c.keys())
            pii.append({**item, "kinds": c, "masked": masked})
    blocked = len({x["ticket_id"] for x in cred + links + pii})
    _dump(out / f"samples_reply_credential_{label}.jsonl", _sample(cred, 20))
    _dump(out / f"samples_reply_links_{label}.jsonl", _sample(links, 20))
    _dump(out / f"samples_reply_pii_{label}.jsonl", _sample(pii, 30))
    return {"answers": n, "credential_request": _rate(len(cred), n), "unsupported_contact": _rate(len(links), n),
            "pii_in_reply": _rate(len(pii), n), "pii_kinds_answers": dict(kinds.most_common()),
            "any_of_three": _rate(blocked, n), "link_domains": dict(domains.most_common())}


def holdout_pii(X: pd.DataFrame, out: Path) -> dict:
    kinds_t, kinds_n, samples = Counter(), Counter(), {}
    for r in X.itertuples():
        raw = f"{_s(r.subject)} {_s(r.body)}"
        masked, c = mask_pii(raw)
        for k, v in c.items():
            kinds_t[k] += 1
            kinds_n[k] += v
            i = masked.find(f"[{k}]")
            samples.setdefault(k, []).append({"ticket_id": r.ticket_id, "kind": k,
                                              "masked_context": _ctx(masked, i, i + len(k) + 2, 80),
                                              "original_context": _orig_ctx(raw, k)})
    _dump(out / "samples_pii_holdout.jsonl", [x for k in samples for x in _sample(samples[k], 20)])
    n = len(X)
    return {"tickets": n, "per_kind": {k: {"tickets": _rate(kinds_t[k], n), "matches": kinds_n[k]}
                                       for k, _ in kinds_t.most_common()},
            "any": _rate(sum(1 for r in X.itertuples() if mask_pii(f"{_s(r.subject)} {_s(r.body)}")[1]), n)}


def _orig_ctx(raw: str, kind: str) -> str:
    """The first original span that masks to [kind], for hand-reading."""
    from app.textproc import _PII
    text = raw
    for k, rx in _PII:  # apply earlier kinds first, exactly as mask_pii does
        repl = f"[{k}]"
        if isinstance(rx, tuple):
            rx, repl = rx
        if k == kind:
            for m in rx.finditer(text):
                if f"[{kind}]" in rx.sub(repl, m.group(0)):  # CARD only masks Luhn-valid numbers
                    return _ctx(text, m.start(), m.end(), 80)
            return ""
        text = rx.sub(repl, text)
    return ""


def _dump(path: Path, rows: list):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="guard-realdata")
    a = ap.parse_args()
    out = ROOT / "eval" / "runs" / "guard_realdata" / a.name
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    core = pd.read_parquet(DATA / "core_kb.parquet")
    kb = pd.read_parquet(DATA / "kb.parquet")
    X = pd.read_parquet(DATA / "holdout_inputs.parquet")
    res = {"name": a.name, "llm_calls": 0, "seed": SEED}
    res["1_precedent_scan"] = {"core_kb": kb_precedent_scan(core, "core_kb", out),
                               "kb": kb_precedent_scan(kb, "kb", out)}
    res["2_holdout_gate"] = holdout_gate(X, out)
    res["3_lengths"] = lengths(X)
    res["5_kb_as_reply"] = {"core_kb": kb_as_reply(core, "core_kb", out), "kb": kb_as_reply(kb, "kb", out)}
    res["4_link_domains_core_kb"] = res["5_kb_as_reply"]["core_kb"].pop("link_domains")
    res["4_link_domains_kb"] = res["5_kb_as_reply"]["kb"].pop("link_domains")
    res["6_holdout_pii"] = holdout_pii(X, out)
    res["seconds"] = round(time.time() - t0, 1)
    (out / "summary.json").write_text(json.dumps(res, indent=2, ensure_ascii=False))
    print(json.dumps(res, indent=2, ensure_ascii=False))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
