# Backup operator console

Streamlit fallback for the live demo. It calls only `app.pipeline.run(subject, body)` and reads the Trust Report, as `docs/CONTRACTS.md` §5 specifies.

```bash
streamlit run console_backup/app.py
```

Tabs: Console, Trust Report, Architecture, Why trust it, Approach.

- **Before the demo:** edit `console_backup/status.json` (the Approach columns, `as_of`, and the goals shown for metrics that aren't measured yet).
- **Trust Report path:** `eval/reports/latest.json` by default. Override with `TRUST_REPORT=/path/to/latest.json`.
- **Missing metrics** show "Not measured yet" plus the goal from `status.json`. A run on the smoke set or stub models is labelled "Not final".
- **Placeholder models:** while `app/triage.py` or `app/retrieval.py` don't exist, the pipeline uses stubs and the header shows "placeholder triage + retrieval". Stub retrieval scores are low, so most tickets escalate until the real models land.
