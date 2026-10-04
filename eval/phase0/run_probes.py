"""Run the Phase 0 probes against the installed grok (goal.md §12).

    uv run python eval/phase0/run_probes.py --out /tmp/phase0 \\
        --model grok-build-0.1 --model grok-4.7 --sandbox workspace
    uv run python eval/phase0/run_probes.py --out /tmp/phase0 --only delegate --dry-run

Each probe is one headless session (``grok -p --output-format json``) in a throwaway workspace
under ``--out``, with the probe plugin installed at project scope, so the user's grok config is
not modified. The sessions do run on the user's account (they cost tokens) and stay in grok's
session history like any other. Per run the runner saves the JSON result, a local trace export
(``grok trace --local``: nothing is uploaded) and a Markdown transcript, then ``analyze.py``
writes ``report.md``.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))

import analyze
import probes

from grok_computer_mcp.limits import JPEG_QUALITY
from grok_computer_mcp.observe.imaging import encode_jpeg, font, text_size

PLUGIN_SRC = Path(__file__).resolve().parent / "probe-plugin"
# Unambiguous characters only (no 0/O, 1/I/L, 5/S, 8/B), so a misread is a real miss.
CODE_ALPHABET = "ACDEFHJKMNPRTUVWXY3479"
CODE_FONT_PX = 72
# One probe session should finish in a few model turns; a hang is a finding, not a wait.
RUN_TIMEOUT_S = 300
EXPORT_TIMEOUT_S = 60
# Servers whose launch path the runner pins (probe_rootvar keeps ${GROK_PLUGIN_ROOT}: V5).
PINNED_SERVERS = ("probe", "probe2")


def new_code(rng: random.Random) -> str:
    """A fresh ``XXX-XXX`` code with at least two letters (the V15 lowercase check needs them)."""
    while True:
        raw = "".join(rng.choice(CODE_ALPHABET) for _ in range(6))
        if sum(c.isalpha() for c in raw) >= 2:
            return f"{raw[:3]}-{raw[3:]}"


def make_assets(target: Path, rng: random.Random) -> dict[str, dict[str, object]]:
    """Render one code image per size plus a structured-only code into ``target``.

    Args:
        target: The installed plugin's ``assets`` directory.
        rng: Random source.

    Returns:
        ``codes.json`` content.
    """
    codes: dict[str, dict[str, object]] = {}
    for size, (width, height) in probes.IMAGE_SIZES.items():
        code = new_code(rng)
        img = Image.new("RGB", (width, height), (245, 245, 245))
        draw = ImageDraw.Draw(img)
        face = font(CODE_FONT_PX)
        tw, th = text_size(draw, code, face)
        x, y = rng.randint(40, width - tw - 40), rng.randint(40, height - th - 40)
        draw.text((x, y), code, fill=(20, 20, 20), font=face)
        name = f"code-{size}.jpg"
        (target / name).write_bytes(encode_jpeg(img, JPEG_QUALITY))
        codes[size] = {"file": name, "code": code, "width": width, "height": height}
    codes["structured"] = {"code": new_code(rng)}
    (target / "codes.json").write_text(json.dumps(codes, indent=2), encoding="utf-8")
    return codes


def prepare(out: Path, seed: int | None) -> Path:
    """Create the workspace with the probe plugin at project scope.

    Args:
        out: Run directory.
        seed: Seed for the codes (None: random).

    Returns:
        The workspace.
    """
    workspace = out / "workspace"
    plugin = workspace / ".grok" / "plugins" / probes.PROBE_PLUGIN
    shutil.rmtree(workspace, ignore_errors=True)
    shutil.copytree(PLUGIN_SRC, plugin)
    mcp_path = plugin / ".mcp.json"
    mcp: dict[str, dict[str, dict[str, object]]] = json.loads(mcp_path.read_text("utf-8"))
    for name in PINNED_SERVERS:
        mcp["mcpServers"][name]["args"] = [str(plugin / "bin" / "probe_mcp.py"), name]
    mcp_path.write_text(json.dumps(mcp, indent=2), encoding="utf-8")
    (workspace / ".grok" / "config.toml").write_text(
        f'[plugins]\nenabled = ["{probes.PROBE_PLUGIN}"]\n', encoding="utf-8")
    codes = make_assets(plugin / "assets", random.Random(seed))
    (out / "codes.json").write_text(json.dumps(codes, indent=2), encoding="utf-8")
    return workspace


def runs(selected: list[probes.Probe], models: list[str],
         sandboxes: list[str]) -> list[tuple[str, probes.Probe, str | None, str | None]]:
    """Expand probes into runs: ``(run_id, probe, model, sandbox)``."""
    out: list[tuple[str, probes.Probe, str | None, str | None]] = []
    for probe in selected:
        for model in (models or [None]) if probe.per_model else [None]:
            for sandbox in ([None, *sandboxes] if probe.per_sandbox else [None]):
                run_id = probe.id + (f"@{model}" if model else "") + (
                    f"#{sandbox}" if sandbox else "")
                out.append((re.sub(r"[^\w@#.-]", "_", run_id), probe, model, sandbox))
    return out


def run_one(out: Path, workspace: Path, run_id: str, probe: probes.Probe, *,
            model: str | None, sandbox: str | None, dry_run: bool) -> list[str]:
    """Run one probe session and export its trace and transcript; return the argv."""
    sid = str(uuid.uuid4())
    argv = ["grok", "-p", probe.prompt, "--output-format", "json", "--cwd", str(workspace),
            "--always-approve", "--max-turns", str(probe.max_turns), "-s", sid]
    argv += ["-m", model] if model else []
    argv += ["--sandbox", sandbox] if sandbox else []
    if dry_run:
        return argv
    env = {**os.environ, "GROK_COMPUTER_PROBE_LOG": str(out / "logs"),
           "GROK_COMPUTER_PROBE_ID": run_id}
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, env=env, check=False,
                              timeout=RUN_TIMEOUT_S)
        code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        code, stdout, stderr = 124, "", f"timeout after {RUN_TIMEOUT_S}s"
    record = {"run": run_id, "probe": probe.id, "model": model, "sandbox": sandbox,
              "session": sid, "exit": code, "stdout": stdout, "stderr_tail": stderr[-2000:]}
    (out / "results" / f"{run_id}.json").write_text(json.dumps(record, indent=2), "utf-8")
    for extra in (["trace", sid, "--local", "-o", str(out / "traces" / f"{run_id}.tar.gz")],
                  ["export", sid, str(out / "transcripts" / f"{run_id}.md")]):
        subprocess.run(["grok", *extra], capture_output=True, check=False,
                       timeout=EXPORT_TIMEOUT_S)
    return argv


def main() -> int:
    """CLI entry point."""
    cli = argparse.ArgumentParser(description="Phase 0 probes (goal.md §12)")
    cli.add_argument("--out", type=Path, required=True)
    cli.add_argument("--model", action="append", default=[], help="repeat per model (V1/V2/V15)")
    cli.add_argument("--sandbox", action="append", default=[], help="profiles to try (V11)")
    cli.add_argument("--only", default="", help="comma-separated probe ids")
    cli.add_argument("--seed", type=int, default=None)
    cli.add_argument("--dry-run", action="store_true", help="prepare and print commands only")
    args = cli.parse_args()
    wanted = {p for p in str(args.only).split(",") if p}
    selected = [p for p in probes.PROBES if not wanted or p.id in wanted]
    out: Path = args.out
    for sub in ("logs", "results", "traces", "transcripts"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    workspace = prepare(out, args.seed)
    if not args.dry_run:
        inspect = subprocess.run(["grok", "inspect", "--json"], cwd=workspace, text=True,
                                 capture_output=True, check=False, timeout=EXPORT_TIMEOUT_S)
        (out / "inspect.json").write_text(inspect.stdout or "{}", encoding="utf-8")
    for run_id, probe, model, sandbox in runs(selected, args.model, args.sandbox):
        argv = run_one(out, workspace, run_id, probe, model=model, sandbox=sandbox,
                       dry_run=args.dry_run)
        print(f"{run_id}: {' '.join(argv[:3])} ...", file=sys.stderr)
    if not args.dry_run:
        print(analyze.write_report(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
