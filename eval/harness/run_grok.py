"""Run task-set variants against grok on this desktop and report §9.4 metrics.

Host mode (goal.md §9.5): the desktop under test is this machine or the CI container's Xvfb
desktop, set up with ``LocalSession``. Each variant: reset fixture state -> setup -> isolated
``grok -p`` run with the plugin -> checks -> record. Results go to ``<out>/results.jsonl`` and
``<out>/summary.json``.

    uv run python eval/harness/run_grok.py --subset nightly --out /tmp/grok-eval/run1
    uv run python eval/harness/run_grok.py --variants native-01,browser-02 --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path

HERE = Path(__file__).resolve().parent
TASK_DIR = HERE.parent / "tasks" / "computer_use"
sys.path[:0] = [str(HERE), str(TASK_DIR)]

import fixture_server  # noqa: E402  (eval modules, path set up above)
import grok_runner  # noqa: E402
import hostenv  # noqa: E402
import metrics  # noqa: E402
import taskkit  # noqa: E402
import taskrun  # noqa: E402
from local_session import LocalSession  # noqa: E402

SUBSETS = json.loads((HERE / "subsets.json").read_text(encoding="utf-8"))
# Host runs open bench-ui pages as Chromium app windows (local_session.py), so the variant's
# "bench-ui" allow-list entry names those apps there.
HOST_APP_ALIASES = {"bench-ui": ("Chromium", "Google Chrome")}
# Wall-clock cap per variant: the slowest dev-loop tasks need several subagent round trips.
DEFAULT_TIMEOUT_S = 900
# Main-agent turn cap per variant (the subagent has its own maxTurns in agents/computer.md).
DEFAULT_MAX_TURNS = 40


def select(args: argparse.Namespace) -> list[taskkit.Variant]:
    """Variants chosen on the command line (host-mode capable only)."""
    variants = [v for v in taskkit.load_all() if "host" in v.modes]
    if args.variants:
        wanted = set(args.variants.split(","))
        variants = [v for v in variants if v.id in wanted]
    elif args.category:
        variants = [v for v in variants if v.category == args.category]
    elif args.subset:
        wanted = set(SUBSETS[args.subset])
        variants = [v for v in variants if v.id in wanted]
    return variants


def host_apps(apps: Sequence[str]) -> list[str]:
    """The project allow-list for a host run of a variant.

    Args:
        apps: ``Variant.allow_apps``.

    Returns:
        The apps, with bench-ui replaced by the browser apps that show fixture windows here.
    """
    out: list[str] = []
    for app in apps:
        out += HOST_APP_ALIASES.get(app, (app,))
    return out


async def run_one(variant: taskkit.Variant, args: argparse.Namespace,
                  url: str) -> metrics.RunRecord:
    """Set up, run grok, evaluate and record one variant."""
    base = args.out / variant.id
    shutil.rmtree(base, ignore_errors=True)
    trace_dir, state_dir, workspace, home = (base / "traces", base / "state", base / "workspace",
                                             base / "grok-home")
    host = hostenv.EnvHost(args.out / "fixture-state.json", trace_dir)
    host.state_file.unlink(missing_ok=True)
    session = LocalSession(url, base / "browser-profile")
    windows = await taskrun.run_steps(variant.setup, session, host)
    grok_runner.prepare_workspace(workspace, host_apps(variant.allow_apps))
    grok_runner.install_plugin(home, trace_dir, state_dir, local_facade=not args.pypi_facade)
    if args.dry_run:
        return metrics.RunRecord(variant.id, variant.category, variant.red_team, 0.0,
                                 list(variant.tags), error="dry run")
    result = grok_runner.run_grok(variant.description, workspace=workspace, grok_home=home,
                                  trace_dir=trace_dir, state_dir=state_dir, model=args.model,
                                  max_turns=args.max_turns, timeout_s=args.timeout)
    scores = await taskrun.evaluate(variant, session, host, windows)
    tokens, turns, cost = grok_runner.usage_of(result.output)
    calls, asks, denies = metrics.audit_counts(host.audit_records())
    reward = sum(scores) / len(scores) if scores else 0.0
    error = None if result.exit_code == 0 else f"grok exit {result.exit_code}: {result.stderr_tail}"
    return metrics.RunRecord(variant.id, variant.category, variant.red_team, reward,
                             list(variant.tags), tokens, turns, cost, calls, asks, denies,
                             result.duration_s, error,
                             images=metrics.count_images(host.audit_records()))


async def main_async(args: argparse.Namespace) -> int:
    """Run the selection and write results."""
    variants = select(args)
    if not variants:
        print("no variants selected", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    server = fixture_server.serve(args.port, args.out / "fixture-state.json",
                                  fixture_server.DEFAULT_DEVLOOP)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    records: list[metrics.RunRecord] = []
    try:
        with (args.out / "results.jsonl").open("w", encoding="utf-8") as sink:
            for variant in variants:
                record = await run_one(variant, args, url)
                records.append(record)
                sink.write(json.dumps(metrics.as_dict(record)) + "\n")
                sink.flush()
                print(f"{variant.id}: reward {record.reward:.2f}", file=sys.stderr)
    finally:
        server.shutdown()
    baseline = json.loads(args.baseline.read_text()) if args.baseline else None
    summary = metrics.summarize(records, baseline)
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    pick = parser.add_mutually_exclusive_group()
    pick.add_argument("--variants", help="comma-separated variant ids")
    pick.add_argument("--category", choices=sorted(taskkit.CATEGORIES))
    pick.add_argument("--subset", choices=sorted(SUBSETS))
    parser.add_argument("--out", type=Path, default=Path("/tmp/grok-eval/run"))
    parser.add_argument("--model", default=None)
    parser.add_argument("--port", type=int, default=fixture_server.DEFAULT_PORT)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    parser.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS)
    parser.add_argument("--baseline", type=Path, help="summary.json of the baseline run")
    parser.add_argument("--pypi-facade", action="store_true",
                        help="use the published grok-computer-mcp instead of the local source")
    parser.add_argument("--dry-run", action="store_true", help="set up only; do not run grok")
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
