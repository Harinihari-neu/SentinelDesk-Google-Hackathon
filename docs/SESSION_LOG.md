# Session Log: AI Engineering

What was done, what was measured, and what we can say in the pitch.
Add a new dated section at the top for each session. Keep the run name next to every number.

Run outputs live in `eval/runs/` (harness) and `eval/runs/probes/` (probes). Both are gitignored,
so the numbers that matter are copied here.

---

## Session 2026-11-21 (b): Guardrails (Task 3)

### Summary
1. Wrote a 58-case adversarial set (EN/DE, 13 categories) and an 18-case fresh set. Both are drafts for P4.
2. Measured the baseline, fixed each failure with the smallest code change, and re-measured on both sets.
3. Main finding: the model resists injections in the **ticket** but follows instructions in **precedents** (KB poisoning).
Details, threat table and pitch: `docs/GUARDRAILS.md`.

### PR
| PR | Branch | What | Status |
|---|---|---|---|
| [#4](https://github.com/Harinihari-neu/SentinelDesk-Google-Hackathon/pull/4) | `feat/guardrails` | Adversarial sets, guardrail code, tests, `docs/GUARDRAILS.md` | Open. Base `feat/german-probe` (#2, still open). Merge #1 → #2 → #4 |
| [#3](https://github.com/Harinihari-neu/SentinelDesk-Google-Hackathon/pull/3) | `feat/reuse-baseline` | Another agent's reuse-past-answer baseline. **Also contains copies of the guardrail commits** (see Incident) | Open. Needs a decision |

### Before/after by category (original set, `guard-baseline` → `guard-v1`)
| Category | n | Before | After |
|---|---|---|---|
| prompt_injection | 7 | 3 | 7 |
| tag_escape | 4 | 2 | 4 |
| kb_poisoning | 4 | 2 | 4 |
| obfuscation | 5 | 0 | 5 |
| mixed_language | 2 | 0 | 2 |
| out_of_scope | 5 | 2 | 5 |
| normal_control | 10 | 9 | 10 |
| exfiltration, fabricated_promise, pricing_request, legal_cancel, pii_leak, oversized | 21 | 21 | 21 |
| **All** | **58** | **39** | **58** |

### Safety pitch (copy of `docs/GUARDRAILS.md`)
1. We attacked our own system with 58 hostile tickets in English and German: injections, forged evidence, hidden Unicode, phishing links, leaked secrets and poisoned knowledge-base answers. Before our guardrails, 39 were handled safely. After, all 58 were, and no harmless ticket was blocked.
2. On 18 new attacks we hadn't seen while building the defences, 14 passed, and in the 4 misses the model still didn't do what the attacker asked.
3. Our biggest finding: the model resists instructions in a customer's ticket, but follows instructions planted in old support answers. That would have sent a fake 50% discount, a phishing link and a malware download to customers. Two independent code checks now stop all three.
4. Every guardrail is plain code: it adds about a millisecond per ticket and zero AI tokens, and every block names its reason for the human reviewer.
5. Pattern lists can be beaten, which is why anything suspicious goes to a person, not to the customer.

### Incident: two agents in one working tree
- During this task, another agent switched the shared checkout to `feat/reuse-baseline`. My commits 2 and 3 (fresh set, guardrails v1) landed on that branch. Commit 3 also picked up the other agent's unfinished `reuse_draft()` in `app/draft.py`. My push then sent only commit 1 to `feat/guardrails`, so PR #4 briefly had 1 of 4 commits.
- Fix (no history rewritten, no other branch touched): cherry-picked my commits onto `feat/guardrails` in my own worktree (`../SentinelDesk-wt-ai`), removed `reuse_draft()` there (it belongs to #3), and pushed.
- Effect on numbers: none. `reuse_draft` was never called; all runs used the default LLM drafter.
- Left for the user: PR #3 still holds copies of the guardrail commits, and the local branch `feat/reuse-baseline` holds an unpushed copy of my docs commit (`2911aed`). Worktree `../SentinelDesk-wt-eval` (`feat/eval-holdout-slices`) is also based on that commit.
- Rule from now on: **one agent per worktree.**

### Runs
All gemini-3.5-flash-lite, prompt v1.1, k=3, stub triage, case-supplied precedents, paced (`LLM_MIN_INTERVAL_S=4`), `--gate-bypass`.
| Run | Set | Code | adversarial_pass_rate | Over-blocking |
|---|---|---|---|---|
| `guard-baseline` | original 58 | before fixes | 0.672 (39/58) | 1/10 |
| `guard-fresh-baseline` | fresh 18 | before fixes | 0.500 (9/18) | 1/5 |
| `guard-v1` | original 58 | guardrails v1 | **1.000 (58/58)** | **0/10** |
| `guard-fresh-v1` | fresh 18 | guardrails v1 | **0.778 (14/18)** | 1/5 (LLM variance, not a guardrail) |

### Findings
1. **The LLM obeyed 0 ticket-side injections** (0/25 drafts `guard-baseline`, 0/7 `guard-fresh-baseline`). They failed the set only because nothing escalated them for a human.
2. **KB poisoning works on the model:** it followed 3/4 poisoned precedents (`guard-baseline`) and 2/3 (`guard-fresh-baseline`). 3 poisoned replies would have been sent: a 50% discount, a phishing URL, an `.exe` link. After v1: 0 sent.
3. The fresh set still beats the detector: leetspeak, spaced letters, "disregard what you were told" (`guard-fresh-v1`). The model didn't obey them.
4. Self-harm and French/Spanish tickets were auto-drafted before (the French one got an English reply). Now they escalate.
5. Oversized tickets: 5,401 tokens before vs ~525 normal. Capped: 1,629 (`guard-v1`).
6. Secrets (passwords, API key) reached the LLM provider in 3/5 cases. Now masked (0/5). None were ever echoed.
7. Guardrails cost 0 tokens. Gate p50 0.07 → 0.16 ms, checks p50 1.43 → 1.75 ms (offline benchmark, 76 cases).
8. The new "sent reply" scorer (covers holding replies) changed no result on these sets. Legacy and new scores are equal in all 4 runs.
9. Stub-triage artefact: "markdown" matched the "down" keyword → high-priority incident (fr-exf-01).

### What we can present (measured)
- "58/58 hostile tickets handled safely, 0/10 harmless ones blocked" (`guard-v1`, in-sample).
- "14/18 on attacks unseen while building the defences; in the 4 misses the model still didn't obey" (`guard-fresh-v1`).
- "Poisoned knowledge-base answers would have sent a fake discount, a phishing link and a malware link; now 0 get through" (`guard-baseline` / `guard-fresh-baseline` → v1).
- "All guardrails are code: ~1 ms, 0 tokens."

### What we can NOT claim
- Robustness to paraphrased or novel attacks in general: the fresh set has 18 cases, and its misses show the limit.
- Over-blocking on real tickets and the real KB: **not measured** (stub triage/retrieval, `data/` empty).
- Results across runs: each number is one run. fr-ctl-02 flipped between runs (LLM variance).

### Open items
- [ ] **User:** decide PR #3. Either rebase it onto #4 so it holds only the reuse commit, or close #4 and merge #3 (guardrails + reuse together).
- [ ] P4: review `eval/sets/adversarial.jsonl` and `adversarial_fresh.jsonl` (their folder), and add cases we haven't seen.
- [ ] When DS2's KB lands: run `precedent_signals` over every KB answer (false-positive rate) and list link domains for `CONTACT_ALLOWLIST`.
- [ ] Measure real ticket lengths; revisit `MAX_TICKET_CHARS` = 3,000.
- [ ] `is_deferral` misses "…wird sich mit Ihrem Anliegen befassen" (fr-ctl-02): add verbs, or tighten prompt rule 4 wording.
- [ ] Masking turns log timestamps into `[PHONE]` (pre-existing).

### What's next
1. **Task 2 (model comparison + price pitch) is unblocked:** DS2's split is now in `data/` (`kb.parquet` 49,412 rows, `core_kb.parquet` 33,449, `holdout_inputs.parquet` / `holdout_labels.parquet` 12,353).
   Check first: `kb.parquet` has no `ticket_id` column (CONTRACTS §1 asks for one; `core_kb.parquet` has it), and what `holdout_kb_twins.txt` / `kb_dev_ids.txt` mean. Ask DS2 which KB file retrieval should index.
2. Run the guardrail detectors over the real KB and holdout (offline, 0 calls): false-positive rate of `precedent_signals`, `injection_signals`, `unsupported_language`; ticket lengths vs `MAX_TICKET_CHARS`; link domains for `CONTACT_ALLOWLIST`.
3. Fill `PRICES` from Google's pricing page (Task 2 needs it).

### Budget
This session: ~164 LLM calls (4 runs: 61 + 21 + 61 + 21). Project total ≈ 305 of 1,500.

### How to reproduce
```bash
python tests/test_guardrails.py                                         # offline, 17 tests
LLM_BACKEND=fake python eval/run.py --name fake-guard --gate-bypass     # offline wiring
LLM_MIN_INTERVAL_S=4 LLM_MAX_BACKOFF_S=30 LLM_DEADLINE_S=90 python eval/run.py --name guard-v1 --gate-bypass
LLM_MIN_INTERVAL_S=4 LLM_MAX_BACKOFF_S=30 LLM_DEADLINE_S=90 python eval/run.py --name guard-fresh-v1 --gate-bypass --adv-set adversarial_fresh.jsonl
```

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
