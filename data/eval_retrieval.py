"""eval_retrieval.py: KB-dev queries vs KB-train index. No API calls, no holdout."""
import argparse
from pathlib import Path
import numpy as np, pandas as pd
from sklearn.metrics import f1_score

ap = argparse.ArgumentParser(); ap.add_argument("--emb", required=True); ap.add_argument("--k", type=int, default=5)
a = ap.parse_args(); EMB, K = Path(a.emb), a.k

df = pd.read_parquet("data/core_kb.parquet"); X = np.load(EMB / "core_kb_emb.npy")
ids = pd.read_parquet(EMB / "core_kb_ids.parquet")["ticket_id"].to_numpy()
assert (ids == df["ticket_id"].to_numpy()).all(), "rows don't line up"
tr = (df["split"] == "train").to_numpy()
T, V = df[tr].reset_index(drop=True), df[~tr].reset_index(drop=True)
Xtr, Xdv = X[tr], X[~tr]

idx = np.zeros((len(V), K), int); sim = np.zeros((len(V), K), np.float32)
for s in range(0, len(V), 500):
    S = Xdv[s:s+500] @ Xtr.T
    p = np.argpartition(-S, K, axis=1)[:, :K]
    ps = np.take_along_axis(S, p, 1); o = np.argsort(-ps, 1)
    idx[s:s+500] = np.take_along_axis(p, o, 1); sim[s:s+500] = np.take_along_axis(ps, o, 1)

print(f"{EMB.name}, k={K}, {len(V):,} KB-dev queries vs {len(T):,} KB-train tickets")
for col in ["queue", "type", "priority"]:
    nb = T[col].to_numpy()[idx]; truth = V[col].to_numpy()
    vote = pd.DataFrame(nb).mode(axis=1)[0].to_numpy()
    print(f"{col:9s} top-1 agrees {np.mean(nb[:,0]==truth):.3f} | share of top-{K} agreeing {np.mean(nb==truth[:,None]):.3f} "
          f"| kNN-vote macro-F1 {f1_score(truth, vote, average='macro'):.3f}")

top1 = sim[:, 0]
print("\ntop-1 similarity percentiles (5/25/50/75/95):", np.round(np.percentile(top1, [5, 25, 50, 75, 95]), 3))
q_agree = T["queue"].to_numpy()[idx[:, 0]] == V["queue"].to_numpy()
bins = pd.qcut(top1, 5, labels=["lowest 20%", "20-40%", "40-60%", "60-80%", "top 20%"])
print("queue agreement of top-1 by similarity bin:")
print(pd.Series(q_agree).groupby(bins, observed=True).agg(["mean", "size"]).round(3).to_string())

lang_nb = T["language"].to_numpy()[idx]
for lang, other in [("de", "en"), ("en", "de")]:
    m = (V["language"] == lang).to_numpy()
    print(f"\n{lang} queries: share of top-{K} neighbors in {other}: {np.mean(lang_nb[m] == other):.3f}")
