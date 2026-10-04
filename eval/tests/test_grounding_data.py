"""Grounding benchmark data: manifest validation, synthetic screenshots and facade collection."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import collect
import dataset
import manifest
import pytest
import synth

from grok_computer_mcp.observe.imaging import open_rgb


def _entry(**overrides: object) -> dict[str, object]:
    """A valid manifest entry, with overrides."""
    base: dict[str, object] = {
        "image": "a.jpg", "width": 1280, "height": 800, "app": "MyApp", "source": "collect",
        "reviewed": True,
        "targets": [{"id": "t1", "description": "the Save button", "bbox": [10, 20, 80, 30]}]}
    base.update(overrides)
    return base


def _write(root: Path, *entries: dict[str, object]) -> None:
    """Write a manifest and empty image files for its entries."""
    root.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(e) for e in entries]
    (root / manifest.MANIFEST).write_text("\n".join(lines) + "\n", encoding="utf-8")
    for e in entries:
        (root / str(e["image"])).write_bytes(b"jpeg")


def test_target_boxes_include_their_edges():
    target = dataset.Target("t1", "x", (10, 20, 80, 30))
    assert target.contains(10, 20) and target.contains(90, 50) and target.contains(50, 35)
    assert not target.contains(9.9, 30) and not target.contains(50, 50.1)


def test_unreviewed_entries_are_skipped_unless_asked(tmp_path: Path):
    _write(tmp_path, _entry(), _entry(image="b.jpg", reviewed=False))
    assert [s.image for s in manifest.load(tmp_path)] == ["a.jpg"]
    assert len(manifest.load(tmp_path, include_unreviewed=True)) == 2


BAD_ENTRIES: list[dict[str, object]] = [
    _entry(targets=[]),
    _entry(targets=[{"id": f"t{i}", "description": "x", "bbox": [1, 1, 5, 5]} for i in range(4)]),
    _entry(targets=[{"id": "t1", "description": "x", "bbox": [1, 1, 5, 5]},
                    {"id": "t1", "description": "y", "bbox": [9, 9, 5, 5]}]),
    _entry(targets=[{"id": "t1", "description": " ", "bbox": [1, 1, 5, 5]}]),
    _entry(targets=[{"id": "t1", "description": "x" * 201, "bbox": [1, 1, 5, 5]}]),
    _entry(targets=[{"id": "t1", "description": "x", "bbox": [1250, 1, 40, 5]}]),
    _entry(targets=[{"id": "t1", "description": "x", "bbox": [1, 1, 0, 5]}]),
    _entry(targets=[{"id": "t1", "description": "x", "bbox": [1, 1, 5]}]),
    _entry(targets=[{"id": "t1", "description": "x", "bbox": [1, True, 5, 5]}]),
    _entry(width=2560, height=1600),
    _entry(width=0),
    _entry(image="/etc/a.jpg"),
    _entry(image="../a.jpg"),
]


@pytest.mark.parametrize("bad", BAD_ENTRIES)
def test_malformed_entries_are_rejected(bad: dict[str, object]):
    with pytest.raises(dataset.DatasetError):
        dataset.parse_sample(bad, "manifest.jsonl:1")


def test_missing_or_duplicate_images_are_rejected(tmp_path: Path):
    _write(tmp_path, _entry(), _entry())
    with pytest.raises(dataset.DatasetError, match="twice"):
        manifest.load(tmp_path)
    _write(tmp_path / "b", _entry())
    (tmp_path / "b" / "a.jpg").unlink()
    with pytest.raises(dataset.DatasetError, match="not found"):
        manifest.load(tmp_path / "b")
    (tmp_path / "c").mkdir()
    (tmp_path / "c" / manifest.MANIFEST).write_text("not json\n", encoding="utf-8")
    with pytest.raises(dataset.DatasetError, match="not JSON"):
        manifest.load(tmp_path / "c")


def test_synthetic_set_is_valid_canonical_and_deterministic(tmp_path: Path):
    samples = synth.generate(tmp_path / "a", count=6, seed=3)
    synth.generate(tmp_path / "b", count=6, seed=3)
    loaded = manifest.load(tmp_path / "a")
    assert loaded == samples and len(loaded) == 6
    manifest_a = (tmp_path / "a" / manifest.MANIFEST).read_text()
    assert manifest_a == (tmp_path / "b" / manifest.MANIFEST).read_text()
    for sample in loaded:
        data = (tmp_path / "a" / sample.image).read_bytes()
        assert data == (tmp_path / "b" / sample.image).read_bytes()
        assert open_rgb(data).size == (sample.width, sample.height) == (1280, 800)
        assert len({t.description for t in sample.targets}) == len(sample.targets)
    regenerated = synth.generate(tmp_path / "a", count=2, seed=4)
    assert manifest.load(tmp_path / "a") == regenerated  # the manifest is replaced, not appended


def _fake_env(root: Path) -> dict[str, str]:
    """Facade environment over the bundled fake scene."""
    return {"GROK_COMPUTER_BACKEND": "fake", "GROK_PLUGIN_DATA": str(root / "plugin-data"),
            "GROK_COMPUTER_LOCK_DIR": str(root / "locks"), "GROK_SESSION_ID": "collect-test"}


def test_collection_proposes_reviewable_targets_through_the_facade(tmp_path: Path):
    home, work, out = tmp_path / "home", tmp_path / "work", tmp_path / "data"
    home.mkdir()
    work.mkdir()
    log = asyncio.run(collect.collect(out, ["MyApp", "1Password", "MyApp"], _fake_env(tmp_path),
                                      home, work))
    assert "1Password: skipped (APP_DENIED)" in log  # deny-listed apps are never captured
    assert manifest.load(out) == []  # nothing counts until reviewed
    samples = manifest.load(out, include_unreviewed=True)
    assert [s.image for s in samples] == ["myapp-01.jpg", "myapp-02.jpg"]
    first = samples[0]
    assert first.source == "collect" and not first.reviewed and len(first.targets) == 3
    image = open_rgb((out / first.image).read_bytes())
    assert image.size == (first.width, first.height)
    line = json.loads((out / manifest.MANIFEST).read_text().splitlines()[0])
    described = [t["description"] for t in line["targets"]]
    described += [p["description"] for p in line["proposals"]]
    assert all("API token" not in d for d in described)  # secure fields are never proposed
    assert all("Sync now" not in d for d in described)  # disabled elements are skipped
    roles = [p["role"] for p in line["proposals"]]
    assert len({str(t["description"]).split(" ")[1] for t in line["targets"]}) == 3, roles
