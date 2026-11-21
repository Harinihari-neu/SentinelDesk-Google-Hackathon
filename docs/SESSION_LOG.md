# Session Log: AI Engineering

What was done, what was measured, and what we can say in the pitch.
Add a new dated section at the top for each session. Keep the run name next to every number.

Run outputs live in `eval/runs/` (harness) and `eval/runs/probes/` (probes). Both are gitignored,
so the numbers that matter are copied here.

---

## Session 2026-11-21

### Summary
1. Moved to Python 3.12 and added a thinking control for Gemini 3.x.
2. Ran the first real Gemini smoke test and read every draft by hand.
3. Fixed a false positive in the checks (step numbers).
4. Tested German vs English drafting, found 4 problems, fixed them and re-measured.
5. Added error handling for 429, 5xx, 404, auth, timeouts and broken output, with offline tests.

### PRs
| PR | Branch | What | Status |
|---|---|---|---|
| [#1](https://github.com/Harinihari-neu/SentinelDesk-Google-Hackathon/pull/1) | `chore/py312-smoke` | Python 3.12, `LLM_THINKING_LEVEL`, step-number check fix, smoke results | Open, needs review |
| [#2](https://github.com/Harinihari-neu/SentinelDesk-Google-Hackathon/pull/2) | `feat/german-probe` | Error handling and fallbacks, German fixes, prompt v1.1, German test, offline tests, this log | Open, merge after #1 |

### Changes by file
| File | Change | PR |
|---|---|---|
| `README.md` | "Requires Python 3.10+", uv setup | #1 |
| `app/llm.py` | `LLM_THINKING_LEVEL` (minimal/low/medium/high) | #1 |
| `app/llm.py` | Retries, backoff, fallback models, deadline, timeout, pacing; `LLMCall` records `error_kind`, `attempts`, `fallback_from`, `errors_seen`; billed tokens from failed attempts counted | #2 |
| `app/checks.py` | Step numbers ("1. ", "2) ") no longer flagged as `unsupported_number` | #1 |
| `app/checks.py` | `llm_error:<kind>`; deferral needs no citation; `german_transliteration` | #2 |
| `app/pipeline.py` | Citation-free deferral = holding reply, ticket stays escalated; no step-6 retry on auth errors | #2 |
| `app/draft.py` | Prompt v1.1: no past-action claims, translate precedents, use ä/ö/ü/ß. `PROMPT_VERSION` | #2 |
| `eval/run.py` | Records prompt version and fallbacks; improvement table skips non-harness files | #2 |
| `eval/german_probe.py` | New: EN/DE drafting test, gate bypassed | #2 |
| `eval/sets/translation_pairs.jsonl` | New: 10 hand-written EN/DE ticket pairs with precedents (needs P4 review) | #2 |
| `tests/test_fallbacks.py` | New: 15 offline fault-injection tests | #2 |
| `.env.example` | Documents the new settings | #2 |
| `docs/AI_ENG_ACTION_PLAN.md` | Change log v1.2, v1.3 | #1, #2 |

### Runs
All on 6 smoke tickets or 10 ticket pairs. Triage and retrieval are still stubs; `data/` is empty.

| Run | Model | Prompt | What it tested | Result |
|---|---|---|---|---|
| `fake-smoke` | fake | v1 | Offline wiring | Works end to end. Language miss expected (fake replies in English). |
| `gemini-smoke` | gemini-3.5-flash-lite | v1 | First real run | 3/6 drafted (gate escalated 3). Parse 3/3, language 3/3, checks 3/3. 0.8–1.4 s per call. |
| `flash-think-minimal-smoke` | gemini-3.5-flash, thinking minimal | v1 | Thinking control | Output no longer empty. 3/3 pass. 17–98 s per call (server-side, not thinking: 228 tokens/ticket vs 234 on flash-lite). |
| `flash-think-low-smoke` | gemini-3.5-flash, thinking low | v1 | Thinking control | 3/3 pass. 32–195 s per call. |
| `german-probe` | flash-lite | v1 | German test, no pacing | Discarded: 10× 429, 1× 503. Showed the per-minute quota. |
| `german-probe-v2` | flash-lite | v1 | German test, paced | See table below. |
| `german-probe-v3` | flash-lite | v1.1 | After fixes | 29/30 pass. Latency invalid (pacing counted; fixed after). |
| `german-probe-v4` | flash-lite | v1.1 | After fixes, final code | 30/30 pass. See below. |
| `gemini-smoke-v2` | flash-lite | v1.1 | Smoke on final code | 3/3 drafts pass. |
| (no name) | gemini-2.5-flash → flash-lite | v1.1 | Real 404 fallback | Fell back to flash-lite, answered in 1.1 s. |
| (no name) | fake, fault injection | n/a | `tests/test_fallbacks.py` | 15/15 pass. |

**German test, before vs after** (checks pass / drafts returned; gemini-3.5-flash-lite):
| Condition | `german-probe-v2` (v1) | `german-probe-v4` (v1.1) |
|---|---|---|
| English ticket, English precedents | 8/9 | 10/10 |
| German ticket, German precedents | 9/10 | 10/10 |
| German ticket, English precedents | 7/9 | 10/10 |
| Trap tickets (refund, price, guaranteed response time) | no promises made; deferrals failed `no_citations` | 9/9 pass |
| API errors lost | 2 (503) | 0 (2 recovered by retry) |
| Latency p50 (en-en / de-de / de-en) | 0.74 / 0.88 / 0.67 s | 0.98 / 0.89 / 0.82 s |
| Input tokens en-en | 368 | 494 |

### Findings
1. **Default model works.** gemini-3.5-flash-lite: valid JSON, right language, ~1 s per call (`gemini-smoke`, `german-probe-v4`).
2. **gemini-3.5-flash is not usable live today.** The thinking cap fixes empty output, but calls took 17–195 s (`flash-think-*-smoke`).
3. **German works as well as English** after the fixes: 10/10 in every condition (`german-probe-v4`). The German drafts read naturally and use the formal "Sie". A native speaker should spot-check.
4. **German costs ~20% more tokens** than English for the same reply length (`german-probe-v2`: input +22%, output +19%).
5. **The model did not make promises on any trap ticket** (refund, discount, guaranteed response time) in any language (`german-probe-v2`, `v4`).
6. **Prompt v1 copied past actions as if done for this customer** ("We have unlocked it"). Prompt v1.1 removed this in `german-probe-v4`.
7. **German ticket + English precedents was the weak spot** (`german-probe-v2`: one English reply, one reply without umlauts). Fixed by prompt v1.1; the umlaut issue still appeared once in `german-probe-v3`, and the new check catches it.
8. **Prompt v1.1 costs +34% input tokens** (en-en 368 → 494).
9. **Free-tier quota is per minute.** About 19 quick calls, then 429. Batch evals need `LLM_MIN_INTERVAL_S=4`.
10. **Gemini has a latency tail.** Most calls ~1 s, some 2–10 s without any error (`german-probe-v4`, `gemini-smoke-v2`).
11. **Stub retrieval is not cross-lingual.** The German password ticket escalated while the English one drafted (`gemini-smoke`). Waiting on real retrieval.

### What we can present (measured)
- "Every LLM call goes through one gateway that retries rate limits and outages, falls back to a second model, and escalates to a human instead of crashing." Proof: 15 offline tests, real 404 fallback in 1.1 s, 2 real errors recovered in `german-probe-v4`.
- "German and English replies pass the same checks: 30/30 drafts in our EN/DE test, including German tickets answered from English precedents." (`german-probe-v4`, 10 pairs: small sample, say so.)
- "On refund, price and SLA demands the model makes no promises; it hands over to a specialist and the ticket stays with a human." (`german-probe-v4`, 3 trap tickets × 3 conditions.)
- "Our checks caught real problems we then fixed: replies in the wrong language, made-up past actions, German without umlauts." (`german-probe-v2` → `v4`.)
- "We measured the cost of German: ~20% more tokens." (`german-probe-v2`.)
- "We don't use the bigger model live: it took 17–195 s per call today." (`flash-think-*-smoke`.)

### What we can NOT claim yet
- Any cost in dollars: `PRICES` is empty, cost shows n/a. **Not measured.**
- Triage, gate or retrieval quality: all stubs. **Not measured.**
- Anything on the real ticket data: `data/` is empty. **Not measured.**
- Model comparison (Gemma, 3.1-flash-lite): Task 2, waiting for data. **Not measured.**
- Results on more than 10 ticket pairs: small samples, treat as direction only.

### Open items
- [ ] Review and merge PR #1, then PR #2.
- [ ] P4: review `eval/sets/translation_pairs.jsonl` (their folder).
- [ ] Native German speaker: spot-check German drafts in `eval/runs/probes/german-probe-v4.json`.
- [ ] Decide: is "wir werden … prüfen" (a future-action promise) acceptable? Prompt v1.1 allows it.
- [ ] Nit: "We can re-sent the reset link" (grammar copied from precedent wording) (`gemini-smoke-v2`).
- [ ] `schema_parse_rate` still counts API errors as parse failures; consider a separate API-error metric.
- [ ] Task 2 (model comparison + price pitch) when the data split lands. Use `LLM_MIN_INTERVAL_S=4`.

### Budget
About 140 of 1,500 LLM calls used.

### How to reproduce
```bash
source .venv/bin/activate
python tests/test_fallbacks.py                                     # offline, no key
LLM_BACKEND=fake python eval/run.py --name fake-smoke --smoke       # offline
python eval/run.py --name gemini-smoke --smoke                      # real, ~3 calls
LLM_MIN_INTERVAL_S=4 python eval/german_probe.py --name german-probe-vN   # real, 30 calls
```
