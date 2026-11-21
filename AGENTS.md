# Team rules (for people and coding agents)

Goal: the smallest system that clears the bar, with every component justified by a metric. See docs/PLAN.md.

- Only edit folders you own (table below). Need a change elsewhere? Ask the owner.
- Keep `main` runnable. Small commits, pull before starting a task.
- No secrets in the repo. Keys go in `.env` (gitignored); `.env.example` lists the names.
- Every LLM call goes through one module (`app/llm.py`) so the model can be swapped in one place.
- Never read the holdout split's `answer`, `type`, `queue` or `priority` at inference time.
- Every metric must drive a decision. If it doesn't, cut it.
- Anything mocked goes in the README.
- Evaluation runs with one command: `python eval/run.py`.
- After feature freeze: fixes only.

## Owners
| Folder | Owner |
|---|---|
| `app/` | |
| `ui/` | |
| `data/` | |
| `eval/` | |
| `docs/`, README | |
