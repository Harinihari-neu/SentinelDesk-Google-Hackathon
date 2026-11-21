"""Offline tests for LLM error handling, fallbacks and the new checks. No API calls, no key needed.

    python tests/test_fallbacks.py        # or: pytest tests/
Uses the fake backend's fault injection (LLM_FAKE_ERRORS) to simulate 429, 503, 404, 401, timeouts, etc.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import llm  # noqa: E402
from app.checks import is_deferral, run_checks  # noqa: E402
from app.draft import DraftOut  # noqa: E402
from app.schemas import Draft, Precedent, Ticket  # noqa: E402

FAST = {"LLM_BACKEND": "fake", "LLM_BACKOFF_S": "0.01", "LLM_MAX_BACKOFF_S": "0.02", "LLM_MAX_RETRIES": "2",
        "LLM_DEADLINE_S": "10", "LLM_MODEL_FALLBACKS": "", "LLM_MIN_INTERVAL_S": "0"}
PREC = [Precedent(ticket_id="TKT-00001", score=0.9, language="en", subject="pw", body="reset mail missing",
                  answer="Check the spam folder.")]


def call(faults, **env):
    os.environ.update(FAST | env | {"LLM_FAKE_ERRORS": faults})
    llm._fake_i = 0
    return llm.generate_json("sys", "user TKT-00001", DraftOut, model="main-model")


def test_429_then_ok_retries_same_model():
    c = call("429,ok")
    assert c.parse_ok and c.attempts == 2 and c.errors_seen == ["rate_limited"] and c.model == "main-model"


def test_503_exhausted_reports_unavailable():
    c = call("503")
    assert not c.parse_ok and c.error_kind == "unavailable" and c.attempts == 3
    t = Ticket(ticket_id="X", body="b", language="en")
    d = Draft(reply="", cited_ticket_ids=[], language="en", model=c.model)
    assert run_checks(d, t, PREC, llm_error=c.error).failures == ["llm_error:unavailable"]


def test_503_exhausted_then_fallback_model_answers():
    c = call("503,503,503,ok", LLM_MODEL_FALLBACKS="backup-model")
    assert c.parse_ok and c.model == "backup-model" and c.fallback_from == "main-model" and c.attempts == 4


def test_404_skips_retries_and_falls_back():
    c = call("404,ok", LLM_MODEL_FALLBACKS="backup-model")
    assert c.parse_ok and c.attempts == 2 and c.model == "backup-model" and c.errors_seen == ["model_not_found"]


def test_404_without_fallback_fails_fast():
    c = call("404")
    assert not c.parse_ok and c.error_kind == "model_not_found" and c.attempts == 1


def test_400_bad_request_falls_back():
    c = call("400,ok", LLM_MODEL_FALLBACKS="backup-model")
    assert c.parse_ok and c.model == "backup-model"


def test_401_auth_stops_even_with_fallback():
    c = call("401,ok", LLM_MODEL_FALLBACKS="backup-model")
    assert not c.parse_ok and c.error_kind == "auth" and c.attempts == 1


def test_timeout_and_network_are_retried():
    assert call("timeout,ok").parse_ok
    assert call("network,ok").parse_ok
    assert call("500,ok").parse_ok


def test_truncated_moves_to_fallback_and_bills_tokens():
    c = call("truncated,ok", LLM_MODEL_FALLBACKS="backup-model")
    assert c.parse_ok and c.model == "backup-model" and c.in_tokens > 100  # failed attempt's tokens counted
    c = call("truncated")
    assert c.error_kind == "truncated" and c.attempts == 1


def test_bad_json_moves_to_fallback():
    c = call("badjson,ok", LLM_MODEL_FALLBACKS="backup-model")
    assert c.parse_ok and c.model == "backup-model" and c.errors_seen == ["parse"]
    c = call("badjson")
    assert c.error_kind == "parse"
    t = Ticket(ticket_id="X", body="b", language="en")
    d = Draft(reply="", cited_ticket_ids=[], language="en", model="m")
    assert run_checks(d, t, PREC, llm_error=c.error).failures == ["schema_invalid"]


def test_deadline_stops_retrying():
    c = call("503", LLM_MAX_RETRIES="50", LLM_BACKOFF_S="0.05", LLM_MAX_BACKOFF_S="0.05", LLM_DEADLINE_S="0.3")
    assert not c.parse_ok and c.attempts < 15 and c.latency_ms < 1000


def test_retry_after_is_parsed():
    e = llm.classify(type("E", (Exception,), {"code": 429})("RESOURCE_EXHAUSTED 'retryDelay': '23s'"))
    assert e.kind == "rate_limited" and e.retry_after == 23.0


def test_pipeline_never_crashes_on_outage():
    os.environ.update(FAST | {"LLM_FAKE_ERRORS": "503"})
    llm._fake_i = 0
    from app import pipeline, stubs
    real, pipeline._retrieve = pipeline._retrieve, stubs.retrieve  # stay offline: no embedding call
    try:
        r = pipeline.run("Password reset email", "I forgot my password and the reset email never arrives. Can you help?")
    finally:
        pipeline._retrieve = real
    assert r.status == "escalated"
    if r.checks:  # drafted: the outage shows as the reason
        assert r.checks.failures == ["llm_error:unavailable"]


def test_pipeline_escalates_when_embedding_api_is_down():
    from app import pipeline

    def down(ticket, k=5, queue=None):
        raise ConnectionError("embedding API unreachable")
    real, pipeline._retrieve = pipeline._retrieve, down
    try:
        r = pipeline.run("VPN", "The VPN drops every few minutes.")
    finally:
        pipeline._retrieve = real
    assert r.status == "escalated" and r.draft is None
    assert "retrieval_error:ConnectionError" in r.gate.reasons and "no_close_precedent" in r.gate.reasons


def test_gate_min_similarity_env_override():
    from app.gate import load_config
    os.environ["GATE_MIN_SIMILARITY"] = "0.85"
    try:
        assert load_config()["min_similarity"] == 0.85
    finally:
        del os.environ["GATE_MIN_SIMILARITY"]
    assert load_config()["min_similarity"] == 0.95


def test_deferral_needs_no_citation():
    t = Ticket(ticket_id="X", body="Wir wollen eine Rückerstattung", language="de")
    for reply, lang in [("A specialist will follow up.", "en"), ("Ein Spezialist wird sich bei Ihnen melden.", "de")]:
        t = t.model_copy(update={"language": lang})
        d = Draft(reply=reply, cited_ticket_ids=[], language=lang, model="m")
        assert is_deferral(reply) and run_checks(d, t, PREC).passed, reply
    assert not is_deferral("Please clear your cache and try again within 24 hours; our team will follow up.")
    assert not is_deferral("Check the spam folder.")


def test_german_transliteration():
    t = Ticket(ticket_id="X", body="Wie exportiere ich?", language="de")
    p = [Precedent(ticket_id="TKT-00004", score=0.9, language="de", body="Export",
                   answer="Öffnen Sie das Dashboard und wählen Sie Export > CSV.")]
    bad = Draft(reply="Oeffnen Sie das Dashboard, klicken Sie auf das Menue und waehlen Sie Export > CSV [TKT-00004].",
                cited_ticket_ids=["TKT-00004"], language="de", model="m")
    good = Draft(reply="Öffnen Sie das Dashboard, klicken Sie auf das Menü und wählen Sie Export > CSV [TKT-00004].",
                 cited_ticket_ids=["TKT-00004"], language="de", model="m")
    plain = Draft(reply="Bitte senden Sie uns eine neue Datei mit dem Export [TKT-00004].",  # "neue" is fine
                  cited_ticket_ids=["TKT-00004"], language="de", model="m")
    assert "german_transliteration" in run_checks(bad, t, p).failures
    assert "german_transliteration" not in run_checks(good, t, p).failures
    assert "german_transliteration" not in run_checks(plain, t, p).failures


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    failed = 0
    for name, f in tests:
        try:
            f()
            print(f"PASS {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {name} {e}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
