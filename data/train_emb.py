"""train_emb.py: triage rung on precomputed embeddings (LR per head). Trains on KB-train, scores on KB-dev."""
import argparse, json, time
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score

ap = argparse.ArgumentParser()
ap.add_argument("--emb", required=True)
ap.add_argument("--version", required=True)
ap.add_argument("--balanced", action="store_true")
ap.add_argument("--C", type=float, default=4.0)
args = ap.parse_args()

HEADS = ["type", "queue", "priority"]
EMB = Path(args.emb); OUT = Path("models/triage") / args.version
OUT.mkdir(parents=True, exist_ok=True)

df = pd.read_parquet("data/core_kb.parquet")
X = np.load(EMB / "core_kb_emb.npy")
ids = pd.read_parquet(EMB / "core_kb_ids.parquet")["ticket_id"]
assert len(ids) == len(df) and (ids.to_numpy() == df["ticket_id"].to_numpy()).all(), "rows don't line up"

is_tr = (df["split"] == "train").to_numpy()
tr, dv = df[is_tr], df[~is_tr]
Xtr, Xdv = X[is_tr], X[~is_tr]
print(f"{args.version}: train {len(tr):,}  dev {len(dv):,}  dim {X.shape[1]}")

t0 = time.time()
heads, preds, proba = {}, {}, {}
report = {"model_version": args.version, "embeddings": EMB.name, "eval_split": "kb-dev", "balanced": args.balanced}
for h in HEADS:
    clf = LogisticRegression(max_iter=2000, C=args.C, class_weight="balanced" if args.balanced else None)
    clf.fit(Xtr, tr[h])
    heads[h] = clf
    preds[h] = clf.predict(Xdv); proba[h] = clf.predict_proba(Xdv)
    report[h] = {"macro_f1": round(f1_score(dv[h], preds[h], average="macro"), 4)}
    for lang in ["en", "de"]:
        m = (dv["language"] == lang).to_numpy()
        report[h][f"macro_f1_{lang}"] = round(f1_score(dv[h][m], preds[h][m], average="macro"), 4)
    print(f"{h:9s} {report[h]}")

hpi = ((dv["type"] == "Incident") & (dv["priority"] == "high")).to_numpy()
p_high = proba["priority"][:, list(heads["priority"].classes_).index("high")]
p_inc = proba["type"][:, list(heads["type"].classes_).index("Incident")]
pred_hpi = (preds["type"] == "Incident") & (preds["priority"] == "high")
report["high_priority_incident"] = {"support": int(hpi.sum()), "recall_argmax": round(float(pred_hpi[hpi].mean()), 4)}
print("high-priority Incidents, argmax recall:", report["high_priority_incident"]["recall_argmax"])

report["gate_curve"] = {}
for name, s in [("p_high", p_high), ("p_high_x_p_incident", p_high * p_inc)]:
    for target in [0.90, 0.95, 0.98]:
        thr = float(np.quantile(s[hpi], 1 - target)); esc = float((s >= thr).mean())
        report["gate_curve"][f"{name}@{target}"] = {"threshold": round(thr, 4), "escalated": round(esc, 4)}
        print(f"  {name:20s} recall {target:.2f} -> escalate {esc:.3f} (threshold {thr:.3f})")

joblib.dump({"heads": heads, "model_version": args.version, "embeddings": EMB.name}, OUT / "model.joblib")
(OUT / "kbdev_report.json").write_text(json.dumps(report, indent=2))
print(f"saved -> {OUT}  ({time.time() - t0:.0f}s)")
