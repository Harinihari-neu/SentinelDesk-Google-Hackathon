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
Interfaces every owner must follow: [docs/CONTRACTS.md](docs/CONTRACTS.md). Data shapes: `app/schemas.py`.

| Area | Files | Owner |
|---|---|---|
| Pipeline, LLM, checks, gate, contracts | `app/schemas.py`, `app/llm.py`, `app/draft.py`, `app/checks.py`, `app/gate.py`, `app/pipeline.py` | AI engineering |
| Eval harness, Trust Report | `eval/run.py`, `eval/metrics/`, `eval/reports/` | AI engineering |
| Triage + gate thresholds | `app/triage.py`, `app/config/gate.json` | DS1 |
| Split, data notes, retrieval | `data/`, `docs/DATA_NOTES.md`, `app/retrieval.py` | DS2 |
| Test sets, QA | `eval/sets/` | P4 |
| Console, pitch, README | `ui/`, `README.md`, `pitch/` | P5 |
