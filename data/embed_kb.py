"""data/embed_kb.py: embed data/core_kb.parquet with Gemini. Resumable by shard.

  python3 data/embed_kb.py --limit 200   # smoke test first
  python3 data/embed_kb.py               # full run; rerun the same command if it stops
"""
import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.embed import DIM, MODEL, embed_texts  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--limit", type=int, help="embed only the first N rows (smoke test)")
ap.add_argument("--shard", type=int, default=2000, help="rows per saved shard")
args = ap.parse_args()

D = Path("data")
OUT = D / "emb" / "gemini"
df = pd.read_parquet(D / "core_kb.parquet", columns=["ticket_id", "text"])
if args.limit:
    df = df.head(args.limit)
    OUT = OUT / f"smoke_{args.limit}"
OUT.mkdir(parents=True, exist_ok=True)

texts = df["text"].tolist()
est_tokens = sum(len(t) for t in texts) / 4   # rough: ~4 chars per token
print(f"{MODEL} dim={DIM}: {len(texts):,} texts, ~{est_tokens / 1e6:.2f}M tokens, "
      f"est. ${est_tokens / 1e6 * 0.15:.2f} at $0.15/1M")

t0 = time.time()
for start in range(0, len(texts), args.shard):
    path = OUT / f"shard_{start // args.shard:04d}.npy"
    if path.exists():
        continue                       # already done on an earlier run
    vecs = embed_texts(texts[start:start + args.shard])
    tmp = OUT / f"tmp_{path.name}"
    np.save(tmp, vecs)
    tmp.rename(path)                   # a shard only counts once fully written
    done = min(start + args.shard, len(texts))
    print(f"  {done:,}/{len(texts):,} rows  {time.time() - t0:.0f}s", flush=True)

emb = np.concatenate([np.load(p) for p in sorted(OUT.glob("shard_*.npy"))])
assert len(emb) == len(df), f"{len(emb)} vectors for {len(df)} rows; delete {OUT} shards and rerun"
np.save(OUT / "core_kb_emb.npy", emb)
df[["ticket_id"]].to_parquet(OUT / "core_kb_ids.parquet", index=False)
print(f"saved {emb.shape} -> {OUT / 'core_kb_emb.npy'}  ({time.time() - t0:.0f}s)")
