"""data/train_tfidf.py: triage rung 1, tfidf-lr-v1. Trains on core KB-train, scores on KB-dev."""
import json, time
from pathlib import Path
import joblib
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score
from sklearn.pipeline import make_union

VERSION = "tfidf-lr-v1"
HEADS = ["type", "queue", "priority"]
D = Path("data"); OUT = Path("models/triage") / VERSION
OUT.mkdir(parents=True, exist_ok=True)

df = pd.read_parquet(D / "core_kb.parquet")
tr, dv = df[df.split == "train"], df[df.split == "dev"]
print(f"train {len(tr):,}  dev {len(dv):,}")

t0 = time.time()
vec = make_union(
    TfidfVectorizer(analyzer="word", ngram_range=(1, 2), min_df=2, max_features=200_000, sublinear_tf=True),
    TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=3, max_features=300_000, sublinear_tf=True),
)
Xtr = vec.fit_transform(tr["text"]); Xdv = vec.transform(dv["text"])
Xdv_nosubj = vec.transform(dv["body"].fillna(""))
print(f"vectorized in {time.time() - t0:.0f}s, {Xtr.shape[1]:,} features")

heads, preds, report = {}, {}, {"model_version": VERSION, "eval_split": "kb-dev"}
for h in HEADS:
    t1 = time.time()
    clf = LogisticRegression(max_iter=1000, C=4.0).fit(Xtr, tr[h])
    heads[h] = clf
    preds[h] = clf.predict(Xdv)
    report[h] = {
        "macro_f1": round(f1_score(dv[h], preds[h], average="macro"), 4),
        "macro_f1_no_subject": round(f1_score(dv[h], clf.predict(Xdv_nosubj), average="macro"), 4),
    }
    for lang in ["en", "de"]:
        m = (dv["language"] == lang).to_numpy()
        report[h][f"macro_f1_{lang}"] = round(f1_score(dv[h][m], preds[h][m], average="macro"), 4)
    print(f"{h:9s} {report[h]}  ({time.time() - t1:.0f}s)")

true_hpi = ((dv["type"] == "Incident") & (dv["priority"] == "high")).to_numpy()
pred_hpi = (preds["type"] == "Incident") & (preds["priority"] == "high")
pred_high = preds["priority"] == "high"
report["high_priority_incident"] = {
    "support": int(true_hpi.sum()),
    "recall_exact": round(float(pred_hpi[true_hpi].mean()), 4),
    "recall_priority_high": round(float(pred_high[true_hpi].mean()), 4),
}
print("high-priority Incidents:", report["high_priority_incident"])

joblib.dump({"vectorizer": vec, "heads": heads, "model_version": VERSION}, OUT / "model.joblib")
(OUT / "kbdev_report.json").write_text(json.dumps(report, indent=2))
print(f"saved -> {OUT}  ({time.time() - t0:.0f}s total)")
