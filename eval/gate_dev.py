"""Gate on KB-dev with the real triage and real retrieval. No API calls (dev vectors are precomputed), no holdout.

    python eval/gate_dev.py --name gate-dev-v1 [--sims 0.90,0.93,0.95,0.96]

KB-dev (3,357 core_kb rows, split == "dev") is what DS2 reserved for fitting gate thresholds and the similarity
cutoff. For each min_similarity it reports: share escalated (= 1 - llm_touch_share), reasons, recall of true
high-priority Incidents (the safety floor), and how often the top-1 precedent shares the ticket's queue/type.
Output: eval/runs/gate_dev/<name>.json (gitignored).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from app import retrieval  # noqa: E402
from app.gate import decide, load_config  # noqa: E402
from app.schemas import Ticket  # noqa: E402
from app.textproc import detect_language, mask_pii  # noqa: E402
from app.triage import triage_batch  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="gate-dev")
    ap.add_argument("--sims", default="0.90,0.93,0.95,0.96,0.97")
    ap.add_argument("--k", type=int, default=3)
    a = ap.parse_args()

    df = pd.read_parquet(retrieval.KB_PATH)
    X = np.load(retrieval.EMB_DIR / "core_kb_emb.npy")
    dev = (df["split"] == "dev").to_numpy()
    V, Xd = df[dev].reset_index(drop=True), X[dev]
    tickets = []
    for r in V.itertuples():
        t = Ticket(ticket_id=r.ticket_id, subject=r.subject if isinstance(r.subject, str) and r.subject else None,
                   body=r.body if isinstance(r.body, str) else "")
        t = t.model_copy(update={"language": detect_language(f"{t.subject or ''} {t.body}")})
        tickets.append(t)
    masked = [t.model_copy(update={"subject": mask_pii(t.subject or "")[0] or None, "body": mask_pii(t.body)[0]})
              for t in tickets]
    tri = triage_batch(masked)
    prec = [retrieval.search_vector(q, a.k) for q in Xd]  # stored dev vectors: no API call
    hpi = ((V["type"] == "Incident") & (V["priority"] == "high")).to_numpy()
    top_q = np.array([p[0].queue == q for p, q in zip(prec, V["queue"])])
    top_t = np.array([p[0].type == y for p, y in zip(prec, V["type"])])

    cfg0 = load_config()
    res = {"name": a.name, "dev_tickets": len(V), "k": a.k, "triage_model": tri[0].model_version,
           "min_confidence": cfg0["min_confidence"], "rows": []}
    for sim in [float(x) for x in a.sims.split(",")]:
        cfg = {**cfg0, "min_similarity": sim}
        g = [decide(t, tr, p, cfg) for t, tr, p in zip(tickets, tri, prec)]
        esc = np.array([x.escalate for x in g])
        reasons = Counter(r.split(":")[0] for x in g for r in x.reasons)
        res["rows"].append(dict(
            min_similarity=sim, escalated=round(esc.mean(), 4), llm_touch_share=round(1 - esc.mean(), 4),
            hp_incident_recall=round(esc[hpi].mean(), 4), hp_incidents=int(hpi.sum()),
            drafted_top1_queue_agree=round(top_q[~esc].mean(), 4) if (~esc).any() else None,
            drafted_top1_type_agree=round(top_t[~esc].mean(), 4) if (~esc).any() else None,
            reasons=dict(reasons.most_common())))
    # what each triage head alone escalates (thresholds from load_config)
    res["low_confidence_share"] = {h: round(float(np.mean([getattr(t, h).confidence < thr for t in tri])), 4)
                                   for h, thr in cfg0["min_confidence"].items()}
    out = ROOT / "eval" / "runs" / "gate_dev"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{a.name}.json").write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
