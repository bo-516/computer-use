"""Phase 0 runner and analyzer: the workspace is isolated and pinned, runs expand per model and
sandbox, and verdicts follow the evidence (unknown, never a crash, when it is missing)."""

from __future__ import annotations

import base64
import io
import json
import tarfile
from pathlib import Path

import analyze
import probes
import run_probes
from PIL import Image

from grok_computer_mcp.observe.imaging import encode_jpeg, open_rgb


def test_workspace_preparation_pins_paths_and_keeps_the_v5_server(tmp_path: Path):
    workspace = run_probes.prepare(tmp_path, seed=3)
    plugin = workspace / ".grok" / "plugins" / probes.PROBE_PLUGIN
    servers = json.loads((plugin / ".mcp.json").read_text())["mcpServers"]
    assert servers["probe"]["args"] == [str(plugin / "bin" / "probe_mcp.py"), "probe"]
    assert servers["probe"]["env"]["GROK_COMPUTER_PROBE_DATA"] == "${GROK_PLUGIN_DATA}"
    assert servers["probe_rootvar"]["args"][0].startswith("${GROK_PLUGIN_ROOT}")
    assert probes.PROBE_PLUGIN in (workspace / ".grok" / "config.toml").read_text()
    codes = json.loads((tmp_path / "codes.json").read_text())
    for size, (width, height) in probes.IMAGE_SIZES.items():
        image = open_rgb((plugin / "assets" / codes[size]["file"]).read_bytes())
        assert image.size == (width, height)
    assert len({codes[k]["code"] for k in ("canonical", "large", "structured")}) == 3
    run_probes.prepare(tmp_path, seed=3)
    assert json.loads((tmp_path / "codes.json").read_text()) == codes


def test_runs_expand_per_model_and_sandbox_with_isolated_sessions(tmp_path: Path):
    selected = [p for p in probes.PROBES if p.id in ("image-canonical", "env", "delegate")]
    expanded = run_probes.runs(selected, ["grok-build-0.1", "grok-4.7"], ["workspace"])
    assert [r[0] for r in expanded] == ["image-canonical@grok-build-0.1",
                                        "image-canonical@grok-4.7", "env", "env#workspace",
                                        "delegate"]
    run_id, probe, model, sandbox = expanded[3]
    argv = run_probes.run_one(tmp_path, tmp_path, run_id, probe, model=model, sandbox=sandbox,
                              dry_run=True)
    assert argv[:2] == ["grok", "-p"] and argv[-2:] == ["--sandbox", "workspace"]
    assert "-s" in argv and "--always-approve" in argv and "-m" not in argv


def _jpeg(width: int, height: int) -> str:
    """Base64 JPEG of a given size."""
    return base64.b64encode(encode_jpeg(Image.new("RGB", (width, height)), 80)).decode()


def _write_run(root: Path) -> None:
    """A synthetic probe run directory covering every automated verdict."""
    codes = {"canonical": {"code": "KAT-947"}, "large": {"code": "MEW-334"},
             "structured": {"code": "RAX-779"}}
    (root / "logs").mkdir(parents=True)
    (root / "results").mkdir()
    (root / "traces").mkdir()
    (root / "codes.json").write_text(json.dumps(codes))
    results = {"image-canonical@a": "The code is KAT-947", "image-canonical@b": "NO_IMAGE",
               "structured@a": "NO_CODE", "delegate": "DONE: echo", "delegate-scope": "x",
               "delegate-turns": "x", "background": "DONE: echo", "env#workspace": "DONE",
               "command-args": "ARGS<<hello probe world>>"}
    for run, stdout in results.items():
        (root / "results" / f"{run}.json").write_text(json.dumps(
            {"run": run, "exit": 0, "stdout": stdout}))
    hooks = [("pre-all", "delegate", {"toolName": "probe__echo", "subagentType": "probe"}),
             ("pre-matched", "delegate", {"toolName": "probe__echo"}),
             ("pre-all", "delegate-turns", {"toolName": "probe__echo", "subagentType": "probe"}),
             ("subagent-stop-all", "delegate", {"lastAssistantMessage": "DONE",
                                                "stopHookActive": False}),
             ("subagent-stop-matched", "delegate", {"lastAssistantMessage": "DONE"})]
    with (root / "logs" / "hooks.jsonl").open("w") as sink:
        for label, run, payload in hooks:
            sink.write(json.dumps({"label": label, "env": {"GROK_COMPUTER_PROBE_ID": run},
                                   "payload": payload}) + "\n")
    server = [{"server": "probe", "event": "start", "probe": "env",
               "env": {"GROK_COMPUTER_PROBE_DATA": "/Users/x/.grok/plugin-data/probe"}},
              {"server": "probe_rootvar", "event": "start", "probe": "env"},
              {"server": "probe", "event": "env", "probe": "env#workspace"}]
    (root / "logs" / "probe-mcp.jsonl").write_text("".join(json.dumps(s) + "\n" for s in server))
    blob = json.dumps({"content": [{"type": "image", "data": _jpeg(1280, 800)}]}).encode()
    with tarfile.open(root / "traces" / "image-canonical@a.tar.gz", "w:gz") as archive:
        info = tarfile.TarInfo("session/events.jsonl")
        info.size = len(blob)
        archive.addfile(info, io.BytesIO(blob))


def test_verdicts_follow_the_evidence(tmp_path: Path):
    _write_run(tmp_path)
    text = analyze.write_report(tmp_path)
    verdicts = json.loads((tmp_path / "report.json").read_text())
    status = {(v["item"], v["detail"].split(":")[0]): v["status"] for v in verdicts}
    by_item: dict[str, set[str]] = {}
    for v in verdicts:
        by_item.setdefault(v["item"], set()).add(v["status"])
    assert status["V1", "image-canonical@a"] == "PASS"
    assert status["V1", "image-canonical@b"] == "FAIL"
    for item in ("V2", "V3", "V4", "V5", "V6", "V10", "V11", "V12", "V13"):
        assert by_item[item] == {"PASS"}, (item, verdicts)
    assert by_item["V15"] == {"FAIL"}  # the model could not see structuredContent
    for item in ("V7", "V8", "V9", "V14", "V16"):
        assert by_item[item] == {"MANUAL"}
    assert text.startswith("# Phase 0 probe report") and "| V15 | FAIL |" in text


def test_an_empty_run_is_unknown_not_a_crash(tmp_path: Path):
    verdicts = analyze.analyze(analyze.evidence.load(tmp_path))
    automated = {v.status for v in verdicts if v.item not in probes.MANUAL}
    assert automated == {"UNKNOWN"}
