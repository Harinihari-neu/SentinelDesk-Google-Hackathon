"""SentinelDesk backup operator console (Streamlit).

    streamlit run console_backup/app.py

Calls only app.pipeline.run(subject, body) and reads the Trust Report JSON (docs/CONTRACTS.md §5).
Approach-tab status and metric goals live in console_backup/status.json: edit it before the demo.
Env: TRUST_REPORT (default eval/reports/latest.json).
"""
from __future__ import annotations

import base64
import html
import json
import os
import re
import sys
from pathlib import Path

import streamlit as st

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))


def _load_dotenv(path: Path) -> None:
    """Minimal .env reader so the console picks up the same LLM settings as the pipeline."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")

from app.pipeline import RETRIEVAL_SOURCE, TRIAGE_SOURCE, run  # noqa: E402

REPORT_PATH = Path(os.getenv("TRUST_REPORT", ROOT / "eval" / "reports" / "latest.json"))
STATUS_PATH = HERE / "status.json"

SAMPLES = {
    "VPN outage": ("VPN down: telemedicine and EMR unreachable",
                   "Since this morning clinic staff cannot connect through the VPN. Telemedicine sessions are "
                   "failing and the EMR integration is unreachable from all three sites. Patients are waiting."),
    "Passwort-Reset": ("Passwort-Reset funktioniert nicht",
                       "Seit gestern kann ich mein Passwort für das Kundenportal nicht zurücksetzen. Der Link in "
                       "der E-Mail läuft sofort ab. Können Sie mir helfen?"),
    "Refund request": ("Refund for last month",
                       "The outage cost us money. I want a full refund and an SLA credit for this month."),
    "No subject": ("",
                   "Since your last update the analytics dashboard takes over a minute to load for our whole "
                   "team. When will this be fixed?"),
}

CSS = """
<style>
#MainMenu, footer, header[data-testid="stHeader"] {visibility: hidden; height: 0}
.block-container {max-width: 1080px; padding-top: 1.6rem; padding-bottom: 4rem}
html, body, [class*="css"] {font-family: Inter, system-ui, -apple-system, sans-serif}
div.stButton > button[kind="primary"] {background: #16181D; border-color: #16181D; color: #fff; min-height: 44px; padding: 0 24px; font-weight: 600}
div.stButton > button[kind="primary"]:hover {background: #2A2E36; border-color: #2A2E36}
div.stButton > button[kind="secondary"] {border-radius: 999px; min-height: 34px; padding: 0 12px; font-size: 13px; color: #3A3F4A; border-color: #D6DAE1}
.sd-top {display: flex; align-items: center; gap: 12px; margin-bottom: 4px}
.sd-brand {font-weight: 700; font-size: 20px; letter-spacing: -.01em}
.sd-pill {font-size: 12px; color: #5B6170; background: #F1F3F6; border-radius: 999px; padding: 3px 10px}
.sd-k {font-size: 12px; letter-spacing: .06em; text-transform: uppercase; color: #5B6170; font-weight: 600; margin: 0 0 10px}
.sd-muted {color: #5B6170}
.sd-mono {font-family: "JetBrains Mono", ui-monospace, Menlo, monospace; font-size: 12.5px}
.sd-status {display: flex; align-items: center; gap: 12px; flex-wrap: wrap; font-size: 15px; margin: 18px 0 14px}
.sd-dot {width: 10px; height: 10px; border-radius: 50%; display: inline-block}
.sd-status b {font-size: 18px}
.sd-lat {margin-left: auto; font-family: "JetBrains Mono", ui-monospace, Menlo, monospace; font-size: 13px; color: #3A3F4A}
.sd-trail {display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 20px}
.sd-step {flex: 1 1 120px; display: flex; flex-direction: column; gap: 2px; padding: 10px 12px; border-radius: 8px; background: #F1F3F6; font-size: 13px; font-weight: 600; color: #16181D}
.sd-step span {font-weight: 400; font-family: "JetBrains Mono", ui-monospace, Menlo, monospace; font-size: 12px; color: #5B6170}
.sd-step.llm {background: #EAF0FD; color: #183C94}
.sd-step.off {background: #fff; border: 1px dashed #D6DAE1; color: #8A909C}
.sd-step.stop {background: #FDF1E7; color: #8A3A0A}
.sd-step.bad {background: #FDECEA; color: #9B1C14}
.sd-labels {display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 24px; padding: 16px 0; border-top: 1px solid #ECEEF2; border-bottom: 1px solid #ECEEF2; margin-bottom: 24px}
.sd-v {display: flex; align-items: baseline; justify-content: space-between; gap: 8px; margin: 4px 0 6px}
.sd-v b {font-size: 20px}
.sd-v span {font-family: "JetBrains Mono", ui-monospace, Menlo, monospace; font-size: 13px; color: #3A3F4A}
.sd-bar {height: 4px; background: #ECEEF2; border-radius: 2px; overflow: hidden}
.sd-fill {height: 100%; background: #16181D}
.sd-fill.hot {background: #B4470F}
.sd-grid2 {display: grid; grid-template-columns: minmax(0, 3fr) minmax(0, 2fr); gap: 40px}
.sd-draft {font-size: 15px; line-height: 1.7}
.sd-cite {font-family: "JetBrains Mono", ui-monospace, Menlo, monospace; font-size: 12px; font-weight: 500; color: #183C94; background: #EAF0FD; border-radius: 4px; padding: 1px 6px; white-space: nowrap}
.sd-flag {background: #FDECEA; color: #9B1C14; border-radius: 3px; padding: 0 3px; text-decoration: underline wavy #C8321F; text-underline-offset: 3px}
.sd-fail {font-family: "JetBrains Mono", ui-monospace, Menlo, monospace; font-size: 12.5px; color: #9B1C14; margin-top: 4px}
.sd-p {padding: 12px 0; border-bottom: 1px solid #ECEEF2}
.sd-p:first-child {padding-top: 0}
.sd-ph {display: flex; justify-content: space-between; gap: 8px}
.sd-snip {font-size: 13.5px; line-height: 1.5; color: #3A3F4A; margin: 4px 0 0}
.sd-tiles {display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); border-top: 1px solid #ECEEF2; margin-bottom: 32px}
.sd-tile {display: flex; flex-direction: column; gap: 6px; padding: 20px 24px 20px 0; border-bottom: 1px solid #ECEEF2}
.sd-tile .v {font-size: 38px; font-weight: 700; letter-spacing: -.02em; font-variant-numeric: tabular-nums; line-height: 1.1}
.sd-tile .v.pend {font-size: 18px; font-weight: 600; color: #8A909C; letter-spacing: 0; padding: 12px 0 6px}
.sd-tile .n {font-size: 14px; font-weight: 600}
.sd-tile .d {font-size: 13px; color: #5B6170; line-height: 1.45}
.sd-tile .g {font-size: 13px; color: #16181D; font-weight: 500}
.sd-rows {border-top: 1px solid #ECEEF2; margin-bottom: 36px}
.sd-r {display: grid; grid-template-columns: minmax(0, 5fr) minmax(0, 7fr) minmax(0, 4fr); gap: 24px; padding: 16px 0; border-bottom: 1px solid #ECEEF2; align-items: baseline}
.sd-r b {font-size: 16px; line-height: 1.4}
.sd-r .how {font-size: 14px; line-height: 1.55; color: #3A3F4A}
.sd-r .proof {font-family: "JetBrains Mono", ui-monospace, Menlo, monospace; font-size: 12.5px; line-height: 1.6; color: #183C94}
.sd-r .proof .pl {color: #8A909C}
.sd-3 {display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 28px; margin-bottom: 36px}
.sd-col {border-top: 2px solid #16181D}
.sd-col.prog {border-top-color: #8A909C}
.sd-col.next {border-top: 2px dashed #D6DAE1}
.sd-ch {font-size: 14px; font-weight: 700; padding: 12px 0}
.sd-it {font-size: 14px; line-height: 1.5; padding: 10px 0; border-top: 1px solid #ECEEF2; color: #2A2E36}
.sd-it small {display: block; font-size: 12.5px; color: #5B6170; margin-top: 2px}
.sd-legend {display: flex; flex-wrap: wrap; gap: 24px; font-size: 13px; color: #5B6170; margin: 8px 0 16px}
.sd-legend span {display: inline-flex; align-items: center; gap: 8px}
@media (max-width: 800px) {.sd-labels, .sd-grid2, .sd-tiles, .sd-r, .sd-3 {grid-template-columns: 1fr; gap: 10px}}
</style>
"""


def H(s: str) -> None:
    """Render one line of HTML (no newlines, so Markdown never turns it into a code block)."""
    st.markdown(s, unsafe_allow_html=True)


def esc(s) -> str:
    return html.escape(str(s or ""))


@st.cache_data(ttl=5)
def load_json(path: str):
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return None


def status_cfg() -> dict:
    return load_json(str(STATUS_PATH)) or {}


def metrics() -> tuple[dict, dict | None]:
    rep = load_json(str(REPORT_PATH))
    if not rep:
        return {}, None
    return {m["name"]: m.get("value") for m in rep.get("metrics", [])}, rep.get("config", {})


PCT = {"missed_critical_rate", "unsupported_claim_rate", "llm_touch_share", "auto_draft_share",
       "adversarial_pass_rate", "translation_consistency", "citation_validity", "checks_pass_rate",
       "language_match_rate", "schema_parse_rate", "over_blocking_rate"}


def fmt_ms(v: float) -> str:
    if v < 1:
        return "<1 ms"
    return f"{v:,.0f} ms" if v < 1000 else f"{v / 1000:.2f} s"


def fmt_metric(key: str, v):
    if v is None:
        return None
    if key.startswith("latency"):
        return fmt_ms(v)
    if key.startswith("cost"):
        return f"${v:,.2f}"
    if key in PCT:
        return f"{v * 100:.0f}%"
    return f"{v:.2f}"


# ---------------------------------------------------------------- console helpers

REASONS = {
    "high_priority_incident": "High-priority incident",
    "no_close_precedent": "No close precedent to ground a reply",
    "sensitive_content": "Sensitive content",
    "unsupported_language": "Language not supported (English and German only)",
}


def reason_text(r: str) -> str:
    if r in REASONS:
        return REASONS[r]
    if r.startswith("low_confidence_"):
        return f"Low confidence on {r.removeprefix('low_confidence_')}"
    if r.startswith("unsupported_request:"):
        return f"Asks for “{r.split(':', 1)[1]}”, which no precedent supports"
    if r.startswith("possible_injection"):
        return "Possible prompt injection"
    if r.startswith("suspicious_precedent:"):
        return f"Suspicious precedent {r.split(':')[1]}"
    return r


def status_line(res) -> str:
    total = fmt_ms(res.timings_ms.get("total", 0))
    if res.status == "auto_draft_ready":
        color, title, sub = "#1B7F4B", ("#14603A", "Auto-Draft Ready"), "Confident labels, close precedent, all checks passed"
    elif res.status == "major_incident":
        color, title, sub = "#B42318", ("#9B1C14", "Major Incident"), "Part of an active outage cluster"
    else:
        color = "#B4470F"
        if res.gate.escalate:
            on_call = "high_priority_incident" in res.gate.reasons
            title = ("#8A3A0A", "Escalate to On-Call Engineer" if on_call else "Escalate to Human Review")
            sub = " · ".join(reason_text(r) for r in res.gate.reasons[:3])
        elif res.checks and not res.checks.passed:
            n = len(res.checks.failures)
            title, sub = ("#8A3A0A", "Escalate to Human Review"), f"Draft failed {n} code check{'s' if n != 1 else ''}"
        else:
            title, sub = ("#8A3A0A", "Holding reply, human owns it"), "No precedent cited, so a specialist follows up"
    return (f'<div class="sd-status"><span class="sd-dot" style="background:{color}"></span>'
            f'<b style="color:{title[0]}">{title[1]}</b><span class="sd-muted">{esc(sub)}</span>'
            f'<span class="sd-lat">{total} total</span></div>')


def trail(res) -> str:
    t = res.timings_ms
    steps = [("", "Triage", fmt_ms(t.get("triage", 0))), ("", "Retrieve", fmt_ms(t.get("retrieval", 0)))]
    steps.append(("stop", "Gate", "escalate") if res.gate.escalate else ("", "Gate", "pass"))
    if res.draft is None:
        steps += [("off", "LLM draft", "not called"), ("off", "Code checks", "skipped")]
    else:
        steps.append(("llm", "LLM draft", fmt_ms(t.get("draft_and_checks", 0))))
        if res.checks and not res.checks.passed:
            n = len(res.checks.failures)
            steps.append(("bad", "Code checks", f"{n} failed"))
        else:
            steps.append(("", "Code checks", "passed"))
        fb = os.getenv("LLM_FALLBACK_MODEL")
        if fb and res.draft.model == fb:
            steps.append(("bad" if res.checks and not res.checks.passed else "", "Larger model",
                          "still failed" if res.checks and not res.checks.passed else "passed"))
    return '<div class="sd-trail">' + "".join(
        f'<div class="sd-step {c}">{esc(name)}<span>{esc(val)}</span></div>' for c, name, val in steps) + "</div>"


def labels(res) -> str:
    hot = res.gate.escalate and "high_priority_incident" in res.gate.reasons
    out = []
    for key, head in (("Type", res.triage.type), ("Queue", res.triage.queue), ("Priority", res.triage.priority)):
        cls = "hot" if hot and key in ("Type", "Priority") else ""
        out.append(f'<div><div class="sd-k" style="margin:0">{key}</div><div class="sd-v"><b>{esc(head.label)}</b>'
                   f'<span>{head.confidence:.2f}</span></div><div class="sd-bar"><div class="sd-fill {cls}" '
                   f'style="width:{max(0, min(1, head.confidence)) * 100:.0f}%"></div></div></div>')
    return '<div class="sd-labels">' + "".join(out) + "</div>"


def draft_html(res) -> str:
    d = res.draft
    if d is None:
        who = "on-call" if "high_priority_incident" in res.gate.reasons else "a human"
        return f'<p class="sd-draft sd-muted" style="margin:0">No AI reply. Routed to {who} with the precedents.</p>'
    text = esc(d.reply)
    fails = res.checks.failures if res.checks else []
    for f in fails:
        kind, _, val = f.partition(":")
        if kind in ("unsupported_number", "unsupported_promise") and val:
            # not inside a longer word or number (e.g. "24" inside TKT-00241)
            text = re.sub(r"(?<![\w-])" + re.escape(esc(val)) + r"(?!\w)",
                          lambda m: f'<span class="sd-flag">{m.group(0)}</span>', text, flags=re.IGNORECASE)
    text = re.sub(r"\[?(TKT-\d+)\]?", r'<span class="sd-cite">\1</span>', text)
    text = text.replace("\n\n", "<br><br>").replace("\n", "<br>")
    out = f'<div class="sd-draft">{text}</div>'
    if fails:
        out += "".join(f'<div class="sd-fail">{esc(f.replace(":", ": ", 1))}</div>' for f in fails)
    return out


def precedents_html(res) -> str:
    if not res.precedents:
        return '<p class="sd-muted">No precedents found.</p>'
    rows = []
    for p in res.precedents[:3]:
        lang = f" · {esc(p.language)}" if p.language and p.language != res.ticket.language else ""
        snip = p.answer if len(p.answer) <= 140 else p.answer[:140].rsplit(" ", 1)[0] + " …"
        rows.append(f'<div class="sd-p"><div class="sd-ph"><span class="sd-mono" style="font-weight:500">'
                    f'{esc(p.ticket_id)}{lang}</span><span class="sd-mono">{p.score:.2f}</span></div>'
                    f'<p class="sd-snip">{esc(snip)}</p></div>')
    return "".join(rows)


def set_sample(name: str) -> None:
    st.session_state.subject, st.session_state.body = SAMPLES[name]
    st.session_state.pop("result", None)


# ---------------------------------------------------------------- tabs

def tab_console() -> None:
    st.text_input("Subject (optional)", key="subject")
    st.text_area("Body", key="body", height=110)
    cols = st.columns([1.1, 1.3, 1.3, 1.1, 2.2, 0.8])
    for col, name in zip(cols, SAMPLES):
        col.button(name, on_click=set_sample, args=(name,), use_container_width=True)
    go = cols[-1].button("Run", type="primary", use_container_width=True)
    if go:
        body = (st.session_state.get("body") or "").strip()
        if not body:
            st.warning("Paste a ticket body first.")
        else:
            with st.spinner("Triaging…"):
                try:
                    st.session_state.result = run(st.session_state.get("subject"), body)
                    st.session_state.error = None
                except Exception as e:  # keep the live demo alive
                    st.session_state.result, st.session_state.error = None, f"{type(e).__name__}: {e}"
    if st.session_state.get("error"):
        st.error(f"Pipeline error: {st.session_state.error}")
    res = st.session_state.get("result")
    if not res:
        return
    H(status_line(res))
    H(trail(res))
    H(labels(res))
    lang = res.draft.language if res.draft else res.ticket.language
    H(f'<div class="sd-grid2"><div><div class="sd-k">Draft · {esc(lang)}</div>{draft_html(res)}</div>'
      f'<div><div class="sd-k">Precedents</div>{precedents_html(res)}</div></div>')


def tile(m: dict, goals: dict, key: str, name: str, desc: str = "") -> str:
    v = fmt_metric(key, m.get(key))
    d = f'<span class="d">{esc(desc)}</span>' if desc else ""
    if v is None:
        g = f'<span class="g">Goal: {esc(goals.get(key, "set by the team"))}</span>'
        return f'<div class="sd-tile"><span class="v pend">Not measured yet</span><span class="n">{esc(name)}</span>{d}{g}</div>'
    return f'<div class="sd-tile"><span class="v">{v}</span><span class="n">{esc(name)}</span>{d}</div>'


def tab_trust_report() -> None:
    m, cfg = metrics()
    goals = status_cfg().get("goals", {})
    st.markdown("## Trust Report")
    if cfg is None:
        H(f'<p class="sd-muted">No report yet. Run <span class="sd-mono">python eval/run.py</span> '
          f'(looked for <span class="sd-mono">{esc(REPORT_PATH)}</span>). Goals are shown until then.</p>')
    else:
        src = cfg.get("source", "?")
        line = f'source: {esc(src)} · n = {esc(cfg.get("n", "?"))} · {esc(cfg.get("model", ""))} · triage {esc(cfg.get("triage_version", cfg.get("triage", "")))}'
        H(f'<p class="sd-muted">{line} · <span class="sd-mono">python eval/run.py</span></p>')
        if src != "holdout" or cfg.get("triage") == "stub":
            H('<p class="sd-muted" style="font-size:13px">Not final: this run uses a small test set or placeholder models.</p>')
    H('<div class="sd-k" style="margin-top:20px">Required by the brief</div><div class="sd-tiles">'
      + tile(m, goals, "macro_f1_type", "Macro-F1 · type") + tile(m, goals, "macro_f1_queue", "Macro-F1 · queue")
      + tile(m, goals, "macro_f1_priority", "Macro-F1 · priority")
      + tile(m, goals, "hp_incident_recall", "High-priority incident recall")
      + tile(m, goals, "latency_p50_ms", "Latency p50") + "</div>")
    H('<div class="sd-k">Why it’s safe and cheap</div><div class="sd-tiles">'
      + tile(m, goals, "missed_critical_rate", "Missed critical tickets", "High-priority incidents that were auto-drafted.")
      + tile(m, goals, "unsupported_claim_rate", "Unsupported claims", "Shipped drafts with a number or promise not in a precedent.")
      + tile(m, goals, "llm_touch_share", "Tickets that reach the LLM", "Everything else is settled by triage and the gate.")
      + "</div>")


def architecture_svg() -> str:
    G, A, GR, B = "#8A909C", "#B4470F", "#1B7F4B", "#6F8FE0"
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="30 186 1380 560" font-family="Inter, system-ui, -apple-system, Helvetica, Arial, sans-serif">',
             "<defs>"]
    for name, c in (("g", G), ("a", A), ("gr", GR)):
        parts.append(f'<marker id="m{name}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
                     f'orient="auto-start-reverse" markerUnits="userSpaceOnUse"><path d="M0 0 L10 5 L0 10 z" fill="{c}"/></marker>')
    parts.append("</defs>")
    parts.append('<rect x="40" y="196" width="1360" height="180" rx="14" fill="#F6F7F9"/>')
    parts.append('<rect x="40" y="416" width="1360" height="336" rx="14" fill="#F6F7F9"/>')
    for x, y, t in ((60, 220, "OFFLINE · BUILT ONCE"), (60, 440, "LIVE · PER TICKET")):
        parts.append(f'<text x="{x}" y="{y}" font-size="12" font-weight="600" letter-spacing="1" fill="#5B6170">{t}</text>')

    def line(pts, color, marker, dashed=False):
        d = " ".join(f"{x},{y}" for x, y in pts)
        dash = ' stroke-dasharray="6 6"' if dashed else ""
        mk = f' marker-end="url(#m{marker})"' if marker else ""
        parts.append(f'<polyline points="{d}" fill="none" stroke="{color}" stroke-width="2"{dash}{mk}/>')

    line([(208, 308), (255, 308)], G, "g")
    line([(400, 308), (447, 308)], G, "g")
    line([(328, 264), (328, 232), (1200, 232), (1200, 263)], G, "g", True)
    line([(520, 352), (520, 463)], G, "g", True)
    line([(712, 352), (712, 463)], G, "g", True)
    for x in (208, 400, 592, 784, 976, 1168):
        line([(x, 512), (x + 47, 512)], G, "g")
    line([(904, 560), (904, 639)], A, "a")
    line([(1264, 560), (1264, 600), (984, 600), (984, 639)], A, "a")
    line([(1312, 560), (1312, 639)], GR, "gr")

    def card(x, y, w, h, title, sub, fill="#fff", stroke="#D6DAE1", color="#16181D", subc="#5B6170", sw=1):
        cx, cy = x + w / 2, y + h / 2
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>')
        parts.append(f'<text x="{cx}" y="{cy - 4}" text-anchor="middle" font-size="15" font-weight="700" fill="{color}">{esc(title)}</text>')
        parts.append(f'<text x="{cx}" y="{cy + 16}" text-anchor="middle" font-size="12.5" fill="{subc}">{esc(sub)}</text>')

    card(64, 264, 144, 88, "Dataset", "61.8k · en / de")
    card(256, 264, 144, 88, "80 / 20 split", "locked · seed 42")
    card(448, 264, 336, 88, "Classifiers + vector index", "built from the 80% knowledge base only")
    card(1024, 264, 352, 88, "Trust Report", "holdout runs through the live pipeline", stroke="#16181D")
    card(64, 464, 144, 96, "Ticket", "subject + body", fill="#16181D", stroke="#16181D", color="#fff", subc="#C9CDD5")
    card(256, 464, 144, 96, "Language + PII", "detect · mask")
    card(448, 464, 144, 96, "1 · Triage", "type · queue · priority")
    card(640, 464, 144, 96, "2 · Retrieve", "top-k precedents")
    card(832, 464, 144, 96, "3 · Gate", "rules, not a model")
    card(1024, 464, 144, 96, "4 · LLM draft", "the only paid step", fill="#EAF0FD", stroke=B, color="#183C94", subc="#183C94", sw=1.5)
    card(1216, 464, 144, 96, "5 · Code checks", "code, no tokens")
    card(824, 640, 240, 80, "Escalate to human", "with reason + precedents", fill="#FDF1E7", stroke="#F0C3A3", color="#8A3A0A", subc="#8A3A0A")
    card(1240, 640, 136, 80, "Auto-Draft Ready", "cited · en / de", fill="#E8F5EE", stroke="#B5DCC5", color="#14603A", subc="#14603A")

    for x, y, t, c, anchor in ((894, 220, "20% holdout · never indexed", "#5B6170", "middle"),
                               (916, 604, "escalate", "#8A3A0A", "start"),
                               (1324, 604, "pass", "#14603A", "start"),
                               (1120, 624, "fail · retried once on a larger model", "#8A3A0A", "middle")):
        parts.append(f'<text x="{x}" y="{y}" text-anchor="{anchor}" font-size="13" fill="{c}">{esc(t)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def tab_architecture() -> None:
    st.markdown("## How a ticket flows")
    H('<p class="sd-muted" style="font-size:15px">Each step passes on only what it couldn’t settle. '
      'Only one step calls an LLM, and it is the fourth step.</p>')
    b64 = base64.b64encode(architecture_svg().encode()).decode()
    H(f'<img alt="SentinelDesk architecture: offline build of classifiers and index from the 80% split, '
      f'live pipeline Ticket, Language and PII, Triage, Retrieve, Gate, LLM draft, Code checks, ending in '
      f'Auto-Draft Ready or Escalate to human" src="data:image/svg+xml;base64,{b64}" style="width:100%;height:auto">')
    H('<div class="sd-legend"><span><i style="width:14px;height:14px;border-radius:4px;background:#EAF0FD;'
      'border:1.5px solid #6F8FE0;display:inline-block"></i>calls an LLM</span><span><i style="width:24px;'
      'border-top:2px dashed #8A909C;display:inline-block"></i>built offline</span><span><i style="width:14px;'
      'height:14px;border-radius:4px;border:1px solid #D6DAE1;display:inline-block"></i>code or classical ML, '
      'no LLM tokens</span></div>')
    H('<p style="font-size:20px;font-weight:600;letter-spacing:-.01em;margin-top:8px">Most tickets never touch '
      'an LLM, and the LLM never decides whether its own answer is safe.</p>')


def proof(m: dict, items) -> str:
    out = []
    for label, key in items:
        if key is None:
            out.append(esc(label))
            continue
        v = fmt_metric(key, m.get(key))
        out.append(f"{esc(label)} {v}" if v is not None else f'<span class="pl">{esc(label)} · planned</span>')
    return "<br>".join(out)


def tab_why_trust() -> None:
    m, _ = metrics()
    goals = status_cfg().get("goals", {})
    H('<p style="font-size:30px;line-height:1.2;font-weight:700;letter-spacing:-.02em;margin:4px 0 10px">'
      'Accuracy shows it works on average.<br>This shows it’s safe when it’s wrong.</p>')
    H('<p class="sd-muted" style="font-size:15px;line-height:1.55;margin-bottom:28px">Every team will show F1 '
      'and a dashboard. A missed hospital outage or a made-up refund promise costs far more than a wrong '
      'label, so each claim below has a number that proves it.</p>')
    rows = [
        ("Confidence is measured, not claimed",
         "Calibrated classifier scores and retrieval similarity drive the gate. The LLM’s opinion of itself is never used.",
         [("calibration error", "ece_priority"), ("reliability curve", "reliability_curve")]),
        ("When unsure, it hands off to a human",
         "The threshold comes from a cost ratio: a missed outage counts as many times worse than an extra escalation.",
         [("missed critical", "missed_critical_rate"), ("auto-drafted", "auto_draft_share")]),
        ("It can’t make up policy",
         "Code checks every number, promise and citation against the retrieved tickets. No second LLM grading the first.",
         [("unsupported claims", "unsupported_claim_rate"), ("adversarial pass", "adversarial_pass_rate")]),
        ("English and German get equal treatment",
         "The same ticket in both languages must get the same triage.",
         [("EN–DE F1 gap", "queue_f1_gap_en_de"), ("translation consistency", "translation_consistency")]),
        ("Every decision can be traced",
         "Each step logs retrieved IDs, scores, the gate reason and any failed check.",
         [("reason shown on every ticket", None)]),
        ("The test is honest",
         "The holdout is locked before training and never indexed. Draft metrics need no reference answers.",
         [("one command:", None), ("python eval/run.py", None)]),
    ]
    H('<div class="sd-k">Why the decisions can be trusted</div><div class="sd-rows">' + "".join(
        f'<div class="sd-r"><b>{esc(a)}</b><span class="how">{esc(b)}</span><span class="proof">{proof(m, c)}</span></div>'
        for a, b, c in rows) + "</div>")
    H('<div class="sd-k">Why it holds at scale</div><div class="sd-tiles">'
      + tile(m, goals, "llm_touch_share", "Tickets that reach the LLM", "The rest are settled by triage and the gate in milliseconds.")
      + tile(m, goals, "cost_per_1m_tickets_usd", "Cost per 1M tickets", "One small model, short prompts, no LLM judge.")
      + tile(m, goals, "latency_p95_ms", "Latency p95", "Measured per step, retries excluded.") + "</div>")
    tenx = [("Outage storms", "Similar tickets merge into one major incident."),
            ("Larger knowledge base", "Filter by predicted queue first, then shard the index."),
            ("Topics drift", "Watch calibration and re-tune the threshold on a schedule.")]
    H('<div class="sd-k">At 10× volume</div><div class="sd-3">' + "".join(
        f'<div><b style="font-size:14px">{esc(a)}</b><div class="sd-muted" style="font-size:14px;line-height:1.5">{esc(b)}</div></div>'
        for a, b in tenx) + "</div>")


def tab_approach() -> None:
    s = status_cfg()
    H('<div class="sd-k">Goal</div>')
    H(f'<p style="font-size:28px;line-height:1.3;font-weight:700;letter-spacing:-.02em;max-width:900px;margin-bottom:32px">'
      f'{esc(s.get("goal", ""))}</p>')
    H('<div class="sd-k">Approach</div><div class="sd-3">' + "".join(
        f'<div><div class="sd-mono sd-muted">{i:02d}</div><b style="font-size:17px">{esc(a.get("title"))}</b>'
        f'<div style="font-size:14px;line-height:1.55;color:#3A3F4A;margin-top:4px">{esc(a.get("text"))}</div></div>'
        for i, a in enumerate(s.get("approach", []), 1)) + "</div>")

    def col(cls, dot, title, items):
        its = "".join(f'<div class="sd-it">{esc(i.get("item"))}'
                      + (f'<small>{esc(i["note"])}</small>' if i.get("note") else "") + "</div>" for i in items)
        return f'<div class="sd-col {cls}"><div class="sd-ch">{dot} {title}</div>{its}</div>'

    H(f'<div style="display:flex;justify-content:space-between;align-items:baseline"><div class="sd-k">Where we got to</div>'
      f'<span class="sd-muted" style="font-size:13px">as of {esc(s.get("as_of", ""))}</span></div>')
    H('<div class="sd-3">'
      + col("", '<span class="sd-dot" style="background:#1B7F4B;width:8px;height:8px"></span>', "Working now", s.get("working", []))
      + col("prog", '<span class="sd-dot" style="background:#8A909C;width:8px;height:8px"></span>', "In progress", s.get("in_progress", []))
      + col("next", '<span class="sd-dot" style="border:1.5px dashed #8A909C;width:6px;height:6px"></span>', "With more time", s.get("next", []))
      + "</div>")


def main() -> None:
    st.set_page_config(page_title="SentinelDesk", layout="wide")
    H(CSS)
    st.session_state.setdefault("subject", "")
    st.session_state.setdefault("body", "")
    stub = "stub" in (TRIAGE_SOURCE, RETRIEVAL_SOURCE)
    pill = '<span class="sd-pill">placeholder triage + retrieval</span>' if stub else ""
    H(f'<div class="sd-top"><span class="sd-brand">SentinelDesk</span>{pill}</div>')
    tabs = st.tabs(["Console", "Trust Report", "Architecture", "Why trust it", "Approach"])
    with tabs[0]:
        tab_console()
    with tabs[1]:
        tab_trust_report()
    with tabs[2]:
        tab_architecture()
    with tabs[3]:
        tab_why_trust()
    with tabs[4]:
        tab_approach()


main()
