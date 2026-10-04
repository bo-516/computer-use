"""Grounding benchmark scoring and the decisions it feeds (goal.md §9.2, §6.3).

Boundary: pure. Inputs are per-target attempts; outputs are per-model metrics (hit rate of the
top candidate, top-3 hit rate, latency, cost, confidence calibration), the §6.3 grounding tier,
the subagent's default Grok model, and the facade settings that put the decision into effect.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import mean

from grok_computer_mcp.limits import GROUNDING_CONFIDENCE_THRESHOLD

# goal.md §6.3: Grok coordinate-click hit rate at or above which Grok grounds by itself.
DIRECT_MIN_HIT_RATE = 0.85
# goal.md §6.3: below this, raw coordinate clicks are disabled (strict tier).
SOM_MIN_HIT_RATE = 0.60
# The p95 latency reported next to the mean (tail latency decides interactive feel).
LATENCY_PERCENTILE = 0.95


@dataclass(frozen=True)
class Attempt:
    """One model's answer for one target."""

    model: str
    image: str
    target: str
    point: tuple[float, float] | None
    hit: bool
    hit_any: bool
    confidence: float | None
    latency_s: float
    cost_usd: float | None
    error: str | None = None


@dataclass(frozen=True)
class ModelSummary:
    """Metrics of one model over the dataset (errors count as misses)."""

    model: str
    attempts: int
    hit_rate: float
    top3_hit_rate: float
    error_rate: float
    mean_latency_s: float
    p95_latency_s: float
    mean_cost_usd: float | None
    confident_share: float
    confident_hit_rate: float | None


@dataclass(frozen=True)
class Decision:
    """What the benchmark decides (goal.md §9.2 outputs)."""

    subagent_model: str
    grok_hit_rate: float
    tier: str
    locate_model: str
    settings: dict[str, str]


def percentile(values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile.

    Args:
        values: Samples (non-empty).
        q: Quantile in [0, 1].

    Returns:
        The value at that rank.
    """
    ordered = sorted(values)
    rank = max(1, math.ceil(q * len(ordered)))
    return ordered[rank - 1]


def summarize(attempts: Sequence[Attempt]) -> list[ModelSummary]:
    """Per-model metrics, in first-seen model order.

    Args:
        attempts: Every attempt of a run.

    Returns:
        One summary per model.
    """
    out: list[ModelSummary] = []
    for model in dict.fromkeys(a.model for a in attempts):
        mine = [a for a in attempts if a.model == model]
        latencies = [a.latency_s for a in mine]
        costs = [a.cost_usd for a in mine if a.cost_usd is not None]
        confident = [a for a in mine if a.confidence is not None
                     and a.confidence >= GROUNDING_CONFIDENCE_THRESHOLD]
        out.append(ModelSummary(
            model=model, attempts=len(mine),
            hit_rate=sum(a.hit for a in mine) / len(mine),
            top3_hit_rate=sum(a.hit_any for a in mine) / len(mine),
            error_rate=sum(a.error is not None for a in mine) / len(mine),
            mean_latency_s=mean(latencies),
            p95_latency_s=percentile(latencies, LATENCY_PERCENTILE),
            mean_cost_usd=mean(costs) if costs else None,
            confident_share=len(confident) / len(mine),
            confident_hit_rate=(sum(a.hit for a in confident) / len(confident))
            if confident else None))
    return out


def decide_tier(hit_rate: float) -> str:
    """The §6.3 grounding tier for the subagent model's coordinate hit rate.

    Args:
        hit_rate: Top-1 hit rate in [0, 1].

    Returns:
        ``direct`` (Grok grounds by itself), ``som`` (Set-of-Mark by default, external
        ``locate``) or ``strict`` (raw coordinate clicks need a ``locate`` result).
    """
    if hit_rate >= DIRECT_MIN_HIT_RATE:
        return "direct"
    return "som" if hit_rate >= SOM_MIN_HIT_RATE else "strict"


def _rank(summary: ModelSummary) -> tuple[float, float]:
    """Sort key: higher hit rate first, then lower mean latency."""
    return (-summary.hit_rate, summary.mean_latency_s)


def decide(summaries: Sequence[ModelSummary]) -> Decision:
    """Choose the subagent model, the tier and the ``locate`` model.

    Grok models are specs starting with ``xai:``. The best Grok model becomes the subagent's
    default and its hit rate sets the tier. In the ``direct`` tier ``locate`` uses that model;
    otherwise it uses whichever model (Grok or external) grounds best.

    Args:
        summaries: Output of ``summarize``.

    Returns:
        The decision with the facade environment that applies it.

    Raises:
        ValueError: No Grok model was benchmarked.
    """
    grok = sorted((s for s in summaries if s.model.startswith("xai:")), key=_rank)
    if not grok:
        raise ValueError("benchmark at least one xai: model to decide the tier")
    best = grok[0]
    tier = decide_tier(best.hit_rate)
    locate = best if tier == "direct" else sorted(summaries, key=_rank)[0]
    provider, _, rest = locate.model.partition(":")
    model, _, url = rest.partition("@")
    settings = {"GROK_COMPUTER_GROUNDING_TIER": tier, "GROK_COMPUTER_GROUNDING_PROVIDER": provider,
                "GROK_COMPUTER_GROUNDING_MODEL": model}
    if url:
        settings["GROK_COMPUTER_GROUNDING_URL"] = url
    return Decision(best.model.partition(":")[2], best.hit_rate, tier, locate.model, settings)
