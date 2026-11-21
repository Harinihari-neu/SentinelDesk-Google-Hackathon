# AI Engineering: Action Plan and Handoff

**Scope of this workstream:** LLM layer, pipeline, gate logic, deterministic checks, eval harness and Trust Report.
**Read first:** `docs/PLAN.md` (why), `docs/CONTRACTS.md` (interfaces), `app/schemas.py` (data shapes).
**Anyone picking this up** (person or agent): read the "State" and "Next tasks" sections, run the two commands in "How to run", then take the first unchecked task.

---

## Design rules (don't break these)

1. **The LLM goes last.** Triage, retrieval and the gate run without an LLM. Only tickets the gate marks safe get drafted.
2. **The LLM never decides its own safety.** The gate uses calibrated classifier scores, retrieval similarity and code rules. Checks are code, never another LLM.
3. **Every LLM call goes through `app/llm.py`.** That's where latency, tokens and cost get logged. Never call an SDK directly anywhere else.
4. **Every metric names the decision it drives** (`eval/metrics.py`, the `@metric(..., decision=...)` decorator). No decision, no metric.
5. **Holdout labels are read only in `eval/run.py`, after predictions.** Holdout answers are never on disk.
6. **Smallest change that moves a metric.** Each experiment is one run with one changed setting, saved under its own `--name`, so the improvement table builds itself.
7. **Prompt order is stable → volatile** (instructions, precedents, ticket, reply language). Don't put changing values in the system prompt; it breaks caching.

---

## How to run

```bash
python3.11 -m venv .venv && source .venv/bin/activate   # 3.10+ recommended; 3.9 works but Google libs warn
pip install -r requirements.txt
cp .env.example .env    # put GEMINI_API_KEY in .env (gitignored; app/__init__.py loads it)

LLM_BACKEND=fake python eval/run.py --name fake-smoke --smoke    # offline: proves the wiring
python eval/run.py --name gemini-smoke --smoke                   # real Gemini on 6 smoke tickets
python eval/run.py --name baseline --n 200                       # holdout sample, once data/ split exists
```
Outputs:
- `eval/runs/<name>.json`: config, metrics and per-ticket records. Gitignored.
- `eval/reports/latest.md` and `eval/reports/latest.json`: the Trust Report with the improvement table across all saved runs. The console reads the JSON.

---

## File map (AI engineering owns all of these)

| File | Does | Status |
|---|---|---|
| `app/schemas.py` | Shared data contracts | Done |
| `app/__init__.py` | Loads `.env` without extra packages | Done |
| `app/llm.py` | `generate_json(system, user, schema)`: backends `gemini` / `ollama` / `fake`, strict JSON, logs latency, tokens and cost to `CALL_LOG`, never raises | Done; **real Gemini call not yet tested** |
| `app/textproc.py` | `detect_language` (langdetect, stopword fallback), `mask_pii` (IP, email, IBAN, account, phone) | Done |
| `app/stubs.py` | Placeholder triage and retrieval, used only until teammates' modules exist | Done |
| `app/gate.py` | `decide()`: high-priority Incident, low confidence per head, no close precedent, unsupported risky request. Thresholds from `app/config/gate.json` (DS1) | Done; thresholds are placeholders |
| `app/draft.py` | Grounded prompt + `draft()` → `(Draft, LLMCall)` | v1 done |
| `app/checks.py` | Citations, numbers, promise terms, language, schema, PII | v1 done |
| `app/pipeline.py` | `run(subject, body)` / `run_ticket(ticket)` → `PipelineResult` with per-step timings; optional step-6 retry via `LLM_FALLBACK_MODEL` | Done |
| `eval/metrics.py` | Metric registry (triage, gate, retrieval, generation, robustness, ops) | v1 done (21 metrics) |
| `eval/run.py` | Harness CLI, Trust Report, improvement table | Done |
| `eval/sets/smoke.jsonl` | 6 labeled tickets (English, German, empty subject, refund/SLA trap) | Done |

---

## State (verified)

- [x] Offline end-to-end run (`LLM_BACKEND=fake`) produces a full Trust Report on the smoke set.
- [x] Missing API key: the pipeline escalates with `llm_error` and doesn't crash.
- [x] The language check catches the fake model replying in English to German tickets, which shows the check works.
- [ ] Real Gemini call. Blocked only on putting the key in `.env`.
- [ ] Real triage, retrieval and data split from teammates (stubs in use).

---

## Next tasks (in order; each has a "done when")

### Phase 1: Prove the real model (T+1:00 → T+1:30)
1. **Gemini smoke test.** Put the key in `.env`, run `python eval/run.py --name gemini-smoke --smoke`.
   *Done when* `schema_parse_rate = 1.0` and German smoke tickets get German replies (`language_match_rate = 1.0` on drafted tickets).
   - Model ID or SDK errors: check current model names in Google AI Studio and update `LLM_MODEL` / `PRICES` in `app/llm.py`.
   - On 2.5 Flash, measure once with `LLM_THINKING_BUDGET=0` and once unset. Hidden thinking tokens are billed as output.
2. **Verify `PRICES`** in `app/llm.py` against Google's current price page before any cost number goes in the pitch.
3. **Tell P5:** `from app.pipeline import run` works now; the UI can wire to it.

### Phase 2: Real data, first honest numbers (T+1:30 → T+2:30)
4. When DS2's split lands (`data/holdout_inputs.parquet`, `data/holdout_labels.parquet`), run `python eval/run.py --name baseline --n 200`.
   *Done when* triage metrics are no longer n/a and the run records `triage: stub`, so it's a fair baseline.
5. When `app/triage.py` and `app/retrieval.py` land, the pipeline picks them up automatically. Re-run as `--name real-v1`.
   Check that `config.triage` / `config.retrieval` in the report no longer say `stub`.
6. **Sample 20 drafts by hand** from `eval/runs/real-v1.json` (`records[].reply`, `failures`). Look for:
   - false positives in `unsupported_number` (e.g. version numbers, step numbers)
   - false negatives: invented claims the checks missed
   Fix checks or add terms; note each fix in the "Change log" section of this file.

### Phase 3: The improvement ladder (T+2:30 → T+4:00), one change per run
7. **k:** `--k 3` vs `--k 5` (name runs `k3`, `k5`). Keep the smaller k unless `unsupported_claim_rate` or `citation_validity` clearly improves.
8. **Model size:** `--model gemini-2.5-flash-lite` vs `--model gemini-2.5-flash`. Optional: `--backend ollama --model gemma3:4b` for the private/local story. Keep the smallest model that clears the bars: `unsupported_claim_rate ≤ 0.05`, `language_match_rate ≥ 0.95`, `schema_parse_rate = 1.0`.
9. **Gate:** after DS1 delivers `app/config/gate.json` from their curve, re-run. *Done when* `missed_critical_rate = 0` on the sample, and `auto_draft_share` is reported alongside it.
10. **Adversarial set:** when P4 adds `eval/sets/adversarial.jsonl`, `adversarial_pass_rate` fills in automatically. For each failure, fix with the smallest change (a gate term, a check term or a prompt line), then re-run.
11. **Step 6 (optional):** `LLM_FALLBACK_MODEL=gemini-2.5-flash`. Keep only if it raises `checks_pass_rate` at an acceptable cost per 1M tickets.

### Phase 4: Additions if time allows (T+3:30 → T+4:30)
12. **Translation consistency metric:** read `eval/sets/translation_pairs.jsonl`, run both sides, report the share with identical triage labels. Add it as `@metric("translation_consistency", "robustness", "Trust across languages")`. Needs a small change in `run.py` to load pairs.
13. **Outage/burst detection (step 0):** keep a rolling window of recent ticket embeddings or keyword sets in `app/burst.py`. When N tickets in M minutes are similar above a threshold, set `status="major_incident"`. Demo with `eval/sets/outage_burst.jsonl`. Coordinate with DS2 to reuse the embeddings.
14. **Gate curve plot** for the pitch: auto-draft share vs. high-priority recall across thresholds (with DS1).

### Phase 5: Freeze (T+4:30)
15. Final run `--name final --n 300` (or more if time allows). Copy the headline numbers and the improvement table into `pitch/`.
16. List anything mocked in the README ("Mocked for the demo").

---

## Known gaps and risks

- **`unsupported_number` may over-fire** on harmless numbers (step lists, version strings). Phase 2 task 6 checks this. Possible fix: ignore numbers of 1–2 digits followed by `.` or `)` at line start.
- **The promise-term check is a fixed list.** It catches the common cases; it isn't a full claim verifier. Say so honestly in Q&A.
- **Gate `RISKY_ASKS`** uses substring matching (`credit` also matches `credit card`). Tune using the adversarial set.
- **Stub triage gets queue badly wrong** (macro-F1 ~0.07 on smoke). That's expected; it's only there to wire things up.
- **`langdetect` is unreliable on very short text.** The stopword fallback only runs if langdetect is missing.
- **Escalated tickets get no draft.** That's deliberate: it keeps the "share of tickets touching an LLM" down. If judges want a draft for the human reviewer, add a flag to draft escalated tickets too, marked "for reviewer".
- **Python 3.9** works, but Google's libraries print end-of-life warnings. Use 3.10+ on the demo machine.

## Change log
- v1: llm gateway, pipeline, gate, checks, harness, smoke set, offline run verified.
