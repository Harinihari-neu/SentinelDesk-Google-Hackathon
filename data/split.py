"""data/split.py: lock the 80/20 split.

The shuffle / ticket_id / cut lines are copied verbatim from the problem statement.
Run once:  python data/split.py
"""
import hashlib
from pathlib import Path

import pandas as pd
from datasets import load_dataset
from sklearn.model_selection import train_test_split

OUT = Path("data")
OUT.mkdir(exist_ok=True)

# ---- Statement recipe (do not change) ----
ds = load_dataset("Tobi-Bueck/customer-support-tickets")
df = ds["train"].to_pandas()
print(f"rows loaded: {len(df):,}  (statement says ~61,800; tell the team if far off)")
print("columns:", list(df.columns))

df = df.sample(frac=1.0, random_state=42).reset_index(drop=True)
df["ticket_id"] = [f"TKT-{i:05d}" for i in range(len(df))]

split_idx = int(len(df) * 0.8)
kb_train_df = df.iloc[:split_idx].copy()
test_df = df.iloc[split_idx:].copy()
# ------------------------------------------

required = {"subject", "body", "type", "queue", "priority", "language", "answer"}
missing = required - set(df.columns)
assert not missing, f"missing columns: {missing}"

# Knowledge base: everything, including answers and labels
kb_train_df.to_parquet(OUT / "kb.parquet", index=False)

# Holdout split into inputs (pipeline) and labels (eval harness only).
# The holdout 'answer' column is never written to disk.
test_df[["ticket_id", "subject", "body"]].to_parquet(OUT / "holdout_inputs.parquet", index=False)
test_df[["ticket_id", "type", "queue", "priority", "language"]].to_parquet(
    OUT / "holdout_labels.parquet", index=False
)

# KB-dev: 10% of the KB, stratified on priority. Used to fit calibration,
# gate thresholds and the similarity cutoff. Never fit anything on the holdout.
_, kb_dev = train_test_split(
    kb_train_df,
    test_size=0.10,
    stratify=kb_train_df["priority"].fillna("NA"),
    random_state=42,
)
(OUT / "kb_dev_ids.txt").write_text("\n".join(sorted(kb_dev["ticket_id"])))

# Fingerprint: hashes IDs together with body text, so two teammates who loaded
# different data get different fingerprints even though IDs look identical.
key = (test_df["ticket_id"] + "|" + test_df["body"].fillna("").str[:80]).str.cat(sep="\n")
fingerprint = hashlib.md5(key.encode()).hexdigest()[:12]

print(f"KB: {len(kb_train_df):,}  (KB-dev: {len(kb_dev):,})  holdout: {len(test_df):,}")
print(f"holdout fingerprint: {fingerprint}")
