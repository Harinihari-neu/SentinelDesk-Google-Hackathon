"""data/prepare_data.py: build the clean core KB used for training, KB-dev and the index.

Run from ~/sentineldesk:  python3 data/prepare_data.py
"""
from pathlib import Path

import pandas as pd

D = Path("data")
kb = pd.read_parquet(D / "kb.parquet")
dev_ids = set((D / "kb_dev_ids.txt").read_text().split())

# Core = rows in the statement's schema. Drops the 10,593-row block with no type,
# fine-grained queues and 5-level priority.
core = kb[kb["type"].notna()].copy()

# Exact copies out. Because every body is now unique, no KB-train ticket can be
# a copy of a KB-dev ticket, so calibration isn't fitted on training duplicates.
core = core.drop_duplicates(subset="body", keep="first")

core["text"] = (
    core["subject"].fillna("").str.strip() + "\n" + core["body"].fillna("").str.strip()
).str.strip()
core["split"] = core["ticket_id"].isin(dev_ids).map({True: "dev", False: "train"})

tag_cols = [c for c in core.columns if c.startswith("tag_")]
cols = ["ticket_id", "subject", "body", "text", "answer",
        "type", "queue", "priority", "language", *tag_cols, "split"]
core[cols].reset_index(drop=True).to_parquet(D / "core_kb.parquet", index=False)

# Holdout tickets whose body appears verbatim in the KB.
# Uses holdout INPUTS only (no labels), so it's within the rules.
ho = pd.read_parquet(D / "holdout_inputs.parquet")
twins = ho.loc[ho["body"].isin(set(kb["body"])), "ticket_id"]
(D / "holdout_kb_twins.txt").write_text("\n".join(sorted(twins)))

print(f"core KB rows: {len(core):,}  "
      f"(train {(core.split == 'train').sum():,}, dev {(core.split == 'dev').sum():,})")
print("type:", core["type"].value_counts().to_dict())
print("priority:", core["priority"].value_counts().to_dict())
print("queues:", core["queue"].nunique())
print(f"holdout twins of KB: {len(twins):,} -> data/holdout_kb_twins.txt (give to the eval harness)")
