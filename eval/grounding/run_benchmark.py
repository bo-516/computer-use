"""Run the grounding benchmark (goal.md §9.2) and decide the §6.3 tier.

    uv run python eval/grounding/run_benchmark.py --data eval/grounding/data/collected \\
        --model xai:grok-build-0.1 --model xai:grok-4.7 \\
        --model uitars:ui-tars-1.5-7b@http://gpu:8000/v1 \\
        --price xai:grok-4.7=IN,OUT --hourly uitars:ui-tars-1.5-7b@http://gpu:8000/v1=USD \\
        --out /tmp/grounding-run

Writes ``attempts.jsonl`` (one line per model and target), ``summary.json`` and ``report.md``:
per-model hit rate of the top candidate, top-3 hit rate, latency and cost, then the recommended
tier, subagent model and facade settings. Each model gets the stored canonical JPEG, as
``locate`` would send it. Prices are arguments because they change; without one, cost is
reported as unknown.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dataset
import manifest
import models
import scoring

from grok_computer_mcp.config import GroundingCoords
from grok_computer_mcp.grounding.base import Grounder, GroundingError
from grok_computer_mcp.limits import JPEG_QUALITY
from grok_computer_mcp.observe.imaging import encode_jpeg, open_rgb

JPEG_MAGIC = b"\xff\xd8\xff"
COORD_SPACES: tuple[GroundingCoords, ...] = ("smart_resize", "relative_1000", "image")


def jpeg_bytes(path: Path) -> bytes:
    """The image as JPEG (PNG sources are encoded at the facade's quality).

    Args:
        path: Image file.

    Returns:
        JPEG bytes.
    """
    data = path.read_bytes()
    return data if data.startswith(JPEG_MAGIC) else encode_jpeg(open_rgb(data), JPEG_QUALITY)


async def run_model(name: str, grounder: Grounder, meter: models.UsageMeter,
                    samples: Sequence[dataset.Sample], *, root: Path,
                    price: models.Price | None) -> list[scoring.Attempt]:
    """Ask one model for every target, sequentially (latency is measured per call).

    Args:
        name: Model spec, as reported.
        grounder: Client.
        meter: The client's transport (token usage).
        samples: Dataset samples.
        root: Dataset directory.
        price: How the model is charged, if known.

    Returns:
        One attempt per target.
    """
    attempts: list[scoring.Attempt] = []
    for sample in samples:
        jpeg = jpeg_bytes(root / sample.image)
        for target in sample.targets:
            before = meter.tokens()
            start = time.perf_counter()
            error: str | None = None
            try:
                found = await grounder.locate(jpeg, sample.width, sample.height,
                                              target.description)
            except GroundingError as exc:
                found, error = [], str(exc)
            latency = time.perf_counter() - start
            used_in, used_out = (a - b for a, b in zip(meter.tokens(), before, strict=True))
            top = found[0] if found else None
            attempts.append(scoring.Attempt(
                model=name, image=sample.image, target=target.id,
                point=(top.point.x, top.point.y) if top else None,
                hit=top is not None and target.contains(top.point.x, top.point.y),
                hit_any=any(target.contains(c.point.x, c.point.y) for c in found),
                confidence=top.confidence if top else None, latency_s=latency,
                cost_usd=price.cost(used_in, used_out, latency) if price else None, error=error))
    return attempts


def report(summaries: Sequence[scoring.ModelSummary], decision: scoring.Decision | None,
           samples: int, targets: int) -> str:
    """Markdown report of a run.

    Args:
        summaries: Per-model metrics.
        decision: Tier decision (None when no Grok model ran).
        samples: Screenshots used.
        targets: Targets used.

    Returns:
        The report.
    """
    lines = [f"# Grounding benchmark ({samples} screenshots, {targets} targets)", "",
             "| model | hit | top-3 hit | errors | mean s | p95 s | cost/call USD | "
             "conf>=0.5 share | conf>=0.5 hit |", "|---|---|---|---|---|---|---|---|---|"]
    for s in summaries:
        cost = f"{s.mean_cost_usd:.5f}" if s.mean_cost_usd is not None else "unknown"
        conf_hit = f"{s.confident_hit_rate:.1%}" if s.confident_hit_rate is not None else "-"
        lines.append(f"| {s.model} | {s.hit_rate:.1%} | {s.top3_hit_rate:.1%} | "
                     f"{s.error_rate:.1%} | {s.mean_latency_s:.2f} | {s.p95_latency_s:.2f} | "
                     f"{cost} | {s.confident_share:.1%} | {conf_hit} |")
    lines.append("")
    if decision is None:
        lines.append("No xai: model was benchmarked, so no tier decision (goal.md §6.3).")
    else:
        lines += [f"Subagent default model: `{decision.subagent_model}` "
                  f"(coordinate hit rate {decision.grok_hit_rate:.1%}).",
                  f"Grounding tier (goal.md §6.3): `{decision.tier}`; `locate` model: "
                  f"`{decision.locate_model}`.", "", "Facade settings:", "", "```"]
        lines += [f"{key}={value}" for key, value in decision.settings.items()]
        lines.append("```")
    return "\n".join(lines) + "\n"


async def run(args: argparse.Namespace) -> int:
    """Run every model and write the outputs."""
    specs = [models.parse_spec(m) for m in args.model]
    prices = models.parse_prices(args.price, args.hourly)
    samples = manifest.load(args.data, include_unreviewed=args.include_unreviewed)
    samples = samples[:args.limit] if args.limit else samples
    if not samples:
        print("dataset has no (reviewed) samples", file=sys.stderr)
        return 2
    attempts: list[scoring.Attempt] = []
    for spec in specs:
        meter = models.UsageMeter()
        try:
            grounder = models.create(spec, meter, args.uitars_coords)
            attempts += await run_model(spec.spec, grounder, meter, samples, root=args.data,
                                        price=prices.get(spec.spec))
        finally:
            await meter.shutdown()
    summaries = scoring.summarize(attempts)
    has_grok = any(s.model.startswith("xai:") for s in summaries)
    decision = scoring.decide(summaries) if has_grok else None
    targets = sum(len(s.targets) for s in samples)
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    with (out / "attempts.jsonl").open("w", encoding="utf-8") as sink:
        sink.writelines(json.dumps(asdict(a)) + "\n" for a in attempts)
    summary = {"dataset": str(args.data), "samples": len(samples), "targets": targets,
               "models": [asdict(s) for s in summaries],
               "decision": asdict(decision) if decision else None}
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    text = report(summaries, decision, len(samples), targets)
    (out / "report.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


def parser() -> argparse.ArgumentParser:
    """Command-line interface."""
    cli = argparse.ArgumentParser(description="Grounding benchmark (goal.md §9.2)")
    cli.add_argument("--data", type=Path, required=True, help="dataset directory")
    cli.add_argument("--model", action="append", required=True,
                     help="provider:model[@url]; repeat for each model")
    cli.add_argument("--price", action="append", default=[],
                     help="spec=IN,OUT USD per million input/output tokens")
    cli.add_argument("--hourly", action="append", default=[],
                     help="spec=USD per hour of a self-hosted endpoint")
    cli.add_argument("--uitars-coords", choices=COORD_SPACES, default="smart_resize")
    cli.add_argument("--include-unreviewed", action="store_true")
    cli.add_argument("--limit", type=int, default=0, help="use only the first N screenshots")
    cli.add_argument("--out", type=Path, default=Path("grounding-run"))
    return cli


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    args = parser().parse_args(argv)
    try:
        return asyncio.run(run(args))
    except (models.SpecError, dataset.DatasetError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
