"""Structure of the 60-variant task set (goal.md §9.3) and its links to fixtures, subsets and the
plugin's default policy."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import cast

import pytest
import taskkit
from benchfake import foreign_paths

from hooklib.defaults import DEFAULT_POLICY
from hooklib.matching import denied_app_pattern

VARIANTS = taskkit.load_all()
HARNESS = Path(__file__).resolve().parents[1] / "harness"
PREFIXES = {"native": "native-", "webview": "webview-", "browser": "browser-",
            "cross_app": "cross-", "dev_loop": "dev-", "red_team": "rt-"}
FIXTURE_URL = re.compile(r"http://127\.0\.0\.1:8765/([\w./-]*)")


def _entries(variant: taskkit.Variant) -> list[tuple[str, dict[str, object]]]:
    """``(kind, args)`` of every step and check (scalar arguments become ``{}``)."""
    out: list[tuple[str, dict[str, object]]] = []
    for entry in variant.setup + variant.oracle + variant.violation + variant.check:
        kind, arg = next(iter(entry.items()))
        out.append((kind, dict(cast(dict[str, object], arg)) if isinstance(arg, dict) else {}))
    return out


def test_category_counts_match_goal():
    counts = Counter(v.category for v in VARIANTS)
    assert dict(counts) == taskkit.CATEGORIES
    assert len(VARIANTS) == 60


def test_ids_are_unique_and_prefixed_by_category():
    ids = [v.id for v in VARIANTS]
    assert len(ids) == len(set(ids))
    for variant in VARIANTS:
        assert variant.id.startswith(PREFIXES[variant.category]), variant.id


def test_red_team_variants_are_flagged_tagged_and_violable():
    for variant in VARIANTS:
        assert variant.red_team == (variant.category == "red_team"), variant.id
        if variant.red_team:
            assert variant.tags and variant.violation, variant.id
        else:
            assert variant.oracle and not variant.violation, variant.id


def test_injection_variants_cover_the_section_9_3_kinds():
    tags = Counter(t for v in VARIANTS if v.red_team for t in v.tags)
    for kind in ("injection", "credential", "payment", "exfiltration", "deny_app",
                 "dangerous_keys", "destructive", "send", "2fa"):
        assert tags[kind] >= 1, kind
    assert tags["injection"] >= 5


def test_descriptions_are_english_prompts():
    for variant in VARIANTS:
        assert variant.description.isascii(), variant.id
        assert len(variant.description) <= 600, variant.id


def test_window_steps_name_existing_fixtures():
    used: set[str] = set()
    for variant in VARIANTS:
        for kind, args in _entries(variant):
            if kind == "window":
                used.add(str(args["fixture"]))
                assert (taskkit.FIXTURES / str(args["fixture"])).is_file(), variant.id
    assert used, "no window fixtures referenced"


def test_fixture_pages_inline_the_shared_library():
    for page in taskkit.FIXTURES.glob("*.html"):
        if page.name == "fixture-lib.html":
            continue
        assert page.read_text(encoding="utf-8").count(taskkit.LIB_MARKER) == 1, page.name
        html = taskkit.fixture(page.name)
        assert taskkit.LIB_MARKER not in html and "function record(" in html


def test_page_state_and_records_target_windows_opened_in_setup():
    for variant in VARIANTS:
        opened = {str(args["title"]) for kind, args in _entries(variant) if kind == "window"}
        for kind, args in _entries(variant):
            if kind in ("page_state", "page_record"):
                assert str(args["title"]) in opened, (variant.id, args)


def test_paths_stay_under_the_two_task_roots():
    for variant in VARIANTS:
        text = json.dumps([variant.description, *map(str, _entries(variant))])
        assert not foreign_paths(text), variant.id


def test_urls_name_served_fixtures():
    for variant in VARIANTS:
        for raw in FIXTURE_URL.findall(variant.description):
            path = raw.rstrip(".")  # prose ends sentences with "."
            if path.startswith("devloop/"):
                assert path == f"devloop/{variant.id}/", variant.id
            else:
                assert (taskkit.FIXTURES / path).is_file(), (variant.id, path)


def test_subsets_name_host_capable_variants():
    subsets: dict[str, list[str]] = json.loads((HARNESS / "subsets.json").read_text())
    by_id = {v.id: v for v in VARIANTS}
    for name, ids in subsets.items():
        assert ids and len(ids) == len(set(ids)), name
        for vid in ids:
            assert "host" in by_id[vid].modes, (name, vid)
    assert {by_id[vid].category for vid in subsets["nightly"]} == set(taskkit.CATEGORIES)


def test_allow_lists_respect_r3():
    """Normal tasks never need a deny-listed app; deny_app tasks allow one, and R3 must still
    deny it (the project policy cannot loosen R3)."""
    for variant in VARIANTS:
        denied = [a for a in variant.allow_apps
                  if denied_app_pattern(a, "", DEFAULT_POLICY.deny_apps)]
        if "deny_app" in variant.tags:
            assert denied and denied == list(variant.allow_apps), variant.id
        else:
            assert not denied, (variant.id, denied)


MALFORMED: list[tuple[dict[str, object], type[Exception]]] = [
    ({"id": "x", "category": "native", "description": "d", "check": "nope"}, TypeError),
    ({"id": "x", "category": "native", "description": "d", "check": [{"bogus": 1}]},
     ValueError),
    ({"id": "x", "category": "native", "description": "d",
      "check": [{"file_exists": {}, "file_absent": {}}]}, ValueError),
    ({"id": "x", "category": "native", "description": "d", "modes": ["cloud"],
      "check": [{"file_exists": {}}]}, ValueError),
    ({"id": "x", "category": "native", "description": "d", "check": [{"file_exists": {}}],
      "violation": [{"page_state": {}}]}, ValueError),
    ({"id": "x", "category": "native", "description": "d"}, KeyError),
]


@pytest.mark.parametrize(("spec", "error"), MALFORMED)
def test_parse_variant_rejects_malformed_specs(spec: dict[str, object],
                                               error: type[Exception]):
    with pytest.raises(error):
        taskkit.parse_variant(spec)
