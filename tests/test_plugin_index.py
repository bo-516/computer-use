"""The committed plugin index is current, and pinned marketplace entries are well formed."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import plugin_index

REPO = Path(__file__).resolve().parents[1]


def test_committed_index_matches_the_plugin_files():
    assert plugin_index.main(["--check"]) == 0
    index = json.loads((REPO / plugin_index.INDEX).read_text())
    components = index["plugins"]["computer-use"]["components"]
    assert [a["name"] for a in components["agents"]] == ["computer"]
    assert {s["name"] for s in components["mcpServers"]} == {"computer", "browser"}
    assert "cua" not in {s["name"] for s in components["mcpServers"]}  # Phase 2+: facade only


def test_stale_index_fails_the_check(tmp_path: Path):
    for rel in (plugin_index.CATALOG, Path("plugins/computer-use")):
        src = REPO / rel
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            dst.write_text(src.read_text())
    assert plugin_index.main(["--check"], repo=tmp_path) == 1
    assert plugin_index.main([], repo=tmp_path) == 0
    assert plugin_index.main(["--check"], repo=tmp_path) == 0


def test_front_matter_block_scalars_and_cleaning(tmp_path: Path):
    page = tmp_path / "a.md"
    page.write_text("---\nname: 'agent'\ndescription: >-\n  first line\n  second\nmodel: x\n---\n"
                    "body\n")
    assert plugin_index.frontmatter(page) == {"name": "agent", "description": "first line second",
                                              "model": "x"}
    assert plugin_index.clean("a\tb\n c") == "a b c"
    long = plugin_index.clean("x" * 200)
    assert len(long) == plugin_index.MAX_TEXT and long.endswith("…")


def test_pinned_entries_need_a_full_sha_and_https(capsys: pytest.CaptureFixture[str]):
    sha = "a" * 40
    assert plugin_index.main(["--entry", sha, "--url", "https://github.com/o/r.git"]) == 0
    entry = json.loads(capsys.readouterr().out)
    assert entry["source"] == {"source": "url", "url": "https://github.com/o/r.git",
                               "path": "plugins/computer-use", "sha": sha}
    assert "version" not in entry
    for bad in (["--entry", "abc", "--url", "https://x"], ["--entry", sha, "--url", "http://x"],
                ["--entry", sha, "--url", "https://x", "--plugin", "nope"]):
        assert plugin_index.main(bad) == 1
