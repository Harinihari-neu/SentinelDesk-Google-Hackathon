"""Validate SentinelDesk test sets in eval/sets/.

Usage:  python eval/sets/validate.py
Exit code 1 if any file has errors, so it can run before every commit.
Standard library only.
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
LANGS = {"en", "de"}
CATEGORIES = {
    "prompt_injection", "fabricated_promise", "pricing_request", "pii_leak",
    "out_of_scope", "outage", "empty_subject", "mixed_language", "normal_control",
}
EXPECT_KEYS = {"escalate", "must_not_contain", "must_contain", "reply_lang"}
# Single words that also appear in correct refusals ("we cannot guarantee").
TOO_GENERIC = {"guarantee", "free", "refund", "sla", "garantie", "kostenlos", "rückerstattung"}


def read_jsonl(path):
    """Yield (line_number, object_or_None, error_or_None) for non-blank lines."""
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
            if not isinstance(obj, dict):
                yield n, None, "line is not a JSON object"
            else:
                yield n, obj, None
        except json.JSONDecodeError as e:
            yield n, None, f"invalid JSON: {e.msg}"


def check_ticket(t, where, errors):
    """Shared checks for subject/body."""
    if "subject" in t and t["subject"] is not None and not isinstance(t["subject"], str):
        errors.append(f"{where}: subject must be a string or null")
    if not isinstance(t.get("body"), str):
        errors.append(f"{where}: body is required and must be a string")


def validate_adversarial(path, errors, warnings):
    ids, cats, langs = set(), Counter(), Counter()
    for n, case, err in read_jsonl(path):
        where = f"{path.name}:{n}"
        if err:
            errors.append(f"{where}: {err}")
            continue
        cid = case.get("id")
        where = f"{path.name}:{n} ({cid})"
        if not isinstance(cid, str) or not re.fullmatch(r"adv-\d{3}", cid):
            errors.append(f"{where}: id must look like adv-001")
        elif cid in ids:
            errors.append(f"{where}: duplicate id")
        ids.add(cid)

        check_ticket(case, where, errors)
        if isinstance(case.get("body"), str) and not case["body"].strip():
            warnings.append(f"{where}: empty body (fine only if this is a deliberate edge case)")

        lang = case.get("lang")
        if lang not in LANGS:
            errors.append(f"{where}: lang must be 'en' or 'de'")
        langs[lang] += 1

        cat = case.get("category")
        if cat not in CATEGORIES:
            errors.append(f"{where}: unknown category {cat!r}")
        cats[cat] += 1

        expect = case.get("expect", {})
        if not isinstance(expect, dict):
            errors.append(f"{where}: expect must be an object")
            continue
        for key in expect.keys() - EXPECT_KEYS:
            errors.append(f"{where}: unknown expect field {key!r}")
        if "escalate" in expect and not isinstance(expect["escalate"], bool):
            errors.append(f"{where}: expect.escalate must be true or false")
        if "reply_lang" in expect and expect["reply_lang"] not in LANGS:
            errors.append(f"{where}: expect.reply_lang must be 'en' or 'de'")
        for key in ("must_not_contain", "must_contain"):
            if key in expect:
                vals = expect[key]
                if not isinstance(vals, list) or not all(isinstance(v, str) and v for v in vals):
                    errors.append(f"{where}: expect.{key} must be a list of non-empty strings")
                    continue
                if key == "must_not_contain":
                    for v in vals:
                        if v.strip().lower() in TOO_GENERIC:
                            warnings.append(
                                f"{where}: {v!r} alone may also block correct refusals; "
                                f"prefer a specific phrase like 'full refund'")
        if cat == "normal_control" and expect.get("escalate") is not False:
            warnings.append(f"{where}: normal controls should set \"escalate\": false")
        for other in ("outage", "prompt_injection", "fabricated_promise"):
            if cat == other and expect.get("escalate") is not True:
                warnings.append(f"{where}: {other} cases usually set \"escalate\": true")

    total = sum(cats.values())
    if total:
        de_share = langs["de"] / total
        print(f"  {total} cases, {langs['de']} German ({de_share:.0%})")
        for c in sorted(CATEGORIES):
            print(f"    {c:20s} {cats[c]}")
        if de_share < 0.25:
            warnings.append(f"{path.name}: only {de_share:.0%} German; aim for about a third")
        if cats["normal_control"] < total * 0.2:
            warnings.append(f"{path.name}: few normal controls; an 'escalate everything' gate would pass")


def validate_pairs(path, errors, warnings):
    ids, count = set(), 0
    for n, pair, err in read_jsonl(path):
        where = f"{path.name}:{n}"
        if err:
            errors.append(f"{where}: {err}")
            continue
        count += 1
        pid = pair.get("id")
        where = f"{path.name}:{n} ({pid})"
        if not isinstance(pid, str) or not re.fullmatch(r"tp-\d{3}", pid):
            errors.append(f"{where}: id must look like tp-001")
        elif pid in ids:
            errors.append(f"{where}: duplicate id")
        ids.add(pid)
        for lang in LANGS:
            side = pair.get(lang)
            if not isinstance(side, dict):
                errors.append(f"{where}: missing '{lang}' object")
            else:
                check_ticket(side, f"{where}.{lang}", errors)
    print(f"  {count} translation pairs")


def validate_burst(path, errors, warnings):
    ids, bodies, count = set(), set(), 0
    for n, t, err in read_jsonl(path):
        where = f"{path.name}:{n}"
        if err:
            errors.append(f"{where}: {err}")
            continue
        count += 1
        tid = t.get("id")
        where = f"{path.name}:{n} ({tid})"
        if not isinstance(tid, str) or not re.fullmatch(r"ob-\d{3}", tid):
            errors.append(f"{where}: id must look like ob-001")
        elif tid in ids:
            errors.append(f"{where}: duplicate id")
        ids.add(tid)
        check_ticket(t, where, errors)
        if t.get("lang") not in LANGS:
            errors.append(f"{where}: lang must be 'en' or 'de'")
        body = (t.get("body") or "").strip().lower()
        if body in bodies:
            warnings.append(f"{where}: same body as another burst ticket; vary the wording")
        bodies.add(body)
    print(f"  {count} burst tickets")
    if count and not 3 <= count <= 5:
        warnings.append(f"{path.name}: contract asks for 3-5 tickets, found {count}")


FILES = {
    "adversarial.jsonl": validate_adversarial,
    "translation_pairs.jsonl": validate_pairs,
    "outage_burst.jsonl": validate_burst,
}


def main():
    errors, warnings = [], []
    for name, fn in FILES.items():
        path = HERE / name
        if not path.exists():
            print(f"{name}: not created yet (skipped)")
            continue
        print(f"{name}:")
        fn(path, errors, warnings)
    for w in warnings:
        print(f"WARNING  {w}")
    for e in errors:
        print(f"ERROR    {e}")
    print(f"\n{len(errors)} errors, {len(warnings)} warnings")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()