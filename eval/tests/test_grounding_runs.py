"""Grounding benchmark runs: scoring, the §6.3 tier decision, metered clients and the runner,
against a mock OpenAI-compatible endpoint (no network)."""

from __future__ import annotations

import asyncio
import base64
import json
import re
from pathlib import Path
from typing import cast

import dataset
import httpx2
import models
import pytest
import run_benchmark
import scoring
import synth


def _attempt(model: str, hit: bool, latency: float = 1.0, *, conf: float | None = 0.9,
             error: str | None = None, cost: float | None = None) -> scoring.Attempt:
    """An attempt with the given outcome."""
    return scoring.Attempt(model, "a.jpg", "t1", (1.0, 1.0) if error is None else None, hit,
                           hit, conf, latency, cost, error)


def _summary(model: str, hit_rate: float, latency: float = 1.0) -> scoring.ModelSummary:
    """A summary with the given hit rate."""
    return scoring.ModelSummary(model, 100, hit_rate, hit_rate, 0.0, latency, latency, None,
                                1.0, hit_rate)


def test_summaries_count_errors_as_misses():
    attempts = [_attempt("xai:a", True, 1.0, cost=0.002), _attempt("xai:a", False, 3.0, conf=0.2),
                _attempt("xai:a", False, 2.0, conf=None, error="timeout"),
                _attempt("xai:a", True, 4.0, cost=0.004), _attempt("xai:b", True)]
    a, b = scoring.summarize(attempts)
    assert (a.model, a.attempts, a.hit_rate, a.error_rate) == ("xai:a", 4, 0.5, 0.25)
    assert a.mean_latency_s == 2.5 and a.p95_latency_s == 4.0
    assert a.mean_cost_usd == pytest.approx(0.003)
    assert a.confident_share == 0.5 and a.confident_hit_rate == 1.0
    assert b.mean_cost_usd is None and b.hit_rate == 1.0


@pytest.mark.parametrize(("rate", "tier"), [(1.0, "direct"), (0.85, "direct"), (0.8499, "som"),
                                            (0.60, "som"), (0.5999, "strict"), (0.0, "strict")])
def test_tier_thresholds_follow_section_6_3(rate: float, tier: str):
    assert scoring.decide_tier(rate) == tier


def test_direct_tier_keeps_locate_on_the_subagent_model():
    decision = scoring.decide([_summary("xai:grok-build-0.1", 0.90, 2.0),
                               _summary("xai:grok-4.7", 0.90, 1.0),
                               _summary("uitars:ui-tars-1.5-7b@http://gpu/v1", 0.97)])
    assert decision.subagent_model == "grok-4.7"  # same hit rate, lower latency
    assert decision.tier == "direct" and decision.locate_model == "xai:grok-4.7"
    assert decision.settings == {"GROK_COMPUTER_GROUNDING_TIER": "direct",
                                 "GROK_COMPUTER_GROUNDING_PROVIDER": "xai",
                                 "GROK_COMPUTER_GROUNDING_MODEL": "grok-4.7"}


def test_lower_tiers_use_the_best_grounder_for_locate():
    decision = scoring.decide([_summary("xai:grok-4.7", 0.55),
                               _summary("uitars:ui-tars-1.5-7b@http://gpu:8000/v1", 0.80)])
    assert decision.tier == "strict" and decision.grok_hit_rate == 0.55
    assert decision.locate_model == "uitars:ui-tars-1.5-7b@http://gpu:8000/v1"
    assert decision.settings["GROK_COMPUTER_GROUNDING_URL"] == "http://gpu:8000/v1"
    assert decision.settings["GROK_COMPUTER_GROUNDING_PROVIDER"] == "uitars"
    with pytest.raises(ValueError, match="xai"):
        scoring.decide([_summary("uitars:x@http://h/v1", 0.9)])


def test_specs_and_prices_parse_strictly():
    spec = models.parse_spec("uitars:ui-tars-1.5-7b@http://gpu:8000/v1")
    assert (spec.provider, spec.model, spec.url) == ("uitars", "ui-tars-1.5-7b",
                                                     "http://gpu:8000/v1")
    assert models.parse_spec("xai:grok-4.7").url is None
    for bad in ("grok-4.7", "openai:gpt", "xai:", "uitars:ui-tars"):
        with pytest.raises(models.SpecError):
            models.parse_spec(bad)
    prices = models.parse_prices(["xai:grok-4.7=2,10"], ["uitars:m@http://h:1/v1=3.6"])
    assert prices["xai:grok-4.7"].cost(1_000_000, 100_000, 5.0) == pytest.approx(3.0)
    assert prices["uitars:m@http://h:1/v1"].cost(9, 9, 10.0) == pytest.approx(0.01)
    for bad_price in (["xai:a=1"], ["=1,2"], ["xai:a=x,y"]):
        with pytest.raises(models.SpecError):
            models.parse_prices(bad_price, [])


class Endpoint:
    """Mock chat-completions endpoint that answers with the centre of the described target."""

    def __init__(self, root: Path, samples: list[dataset.Sample], *, miss: bool = False) -> None:
        """Index targets by (image data URI, description); ``miss`` answers a corner instead."""
        self.boxes: dict[tuple[str, str], tuple[float, float, float, float]] = {}
        for sample in samples:
            data = base64.b64encode(run_benchmark.jpeg_bytes(root / sample.image)).decode()
            for target in sample.targets:
                self.boxes[f"data:image/jpeg;base64,{data}", target.description] = target.bbox
        self.miss = miss
        self.auth: list[str] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        """Answer one request in the xAI JSON format, with usage."""
        self.auth.append(request.headers.get("Authorization", ""))
        body = json.loads(request.content)
        content = body["messages"][1]["content"]
        found = re.search(r"Find: (.*)\n", content[1]["text"])
        assert found is not None
        x, y, w, h = self.boxes[content[0]["image_url"]["url"], found.group(1)]
        point = (1, 1) if self.miss else (round(x + w / 2), round(y + h / 2))
        answer = json.dumps({"candidates": [{"x": point[0], "y": point[1], "confidence": 0.8}]})
        return httpx2.Response(200, json={"choices": [{"message": {"content": answer}}],
                                          "usage": {"prompt_tokens": 1000,
                                                    "completion_tokens": 20}})


def test_metered_client_reports_tokens_and_hits(tmp_path: Path):
    samples = synth.generate(tmp_path, count=3, seed=11)
    endpoint = Endpoint(tmp_path, samples)
    meter = models.UsageMeter(httpx2.MockTransport(endpoint))
    grounder = models.create(models.parse_spec("xai:grok-4.7"), meter,
                             env={"XAI_API_KEY": "test-key"})
    attempts = asyncio.run(run_benchmark.run_model(
        "xai:grok-4.7", grounder, meter, samples, root=tmp_path,
        price=models.Price(input_per_mtok=1.0, output_per_mtok=5.0)))
    targets = sum(len(s.targets) for s in samples)
    assert len(attempts) == targets and all(a.hit and a.hit_any for a in attempts)
    assert meter.tokens() == (1000 * targets, 20 * targets)
    assert all(a.cost_usd == pytest.approx(0.0011) for a in attempts)
    assert set(endpoint.auth) == {"Bearer test-key"}


def test_runner_writes_results_and_decides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data, out = tmp_path / "data", tmp_path / "out"
    samples = synth.generate(data, count=4, seed=5)
    endpoints = {"good": Endpoint(data, samples), "bad": Endpoint(data, samples, miss=True)}
    calls: list[str] = []
    real_meter = models.UsageMeter

    def meter_factory() -> models.UsageMeter:
        calls.append("good" if not calls else "bad")
        return real_meter(httpx2.MockTransport(endpoints[calls[-1]]))

    monkeypatch.setattr(run_benchmark.models, "UsageMeter", meter_factory)
    code = run_benchmark.main(["--data", str(data), "--model", "xai:grok-4.7", "--model",
                               "xai:grok-build-0.1", "--out", str(out)])
    assert code == 0
    targets = sum(len(s.targets) for s in samples)
    assert len((out / "attempts.jsonl").read_text().splitlines()) == 2 * targets
    summary = json.loads((out / "summary.json").read_text())
    rates = {m["model"]: m["hit_rate"] for m in summary["models"]}
    assert rates == {"xai:grok-4.7": 1.0, "xai:grok-build-0.1": 0.0}
    decision = cast(dict[str, object], summary["decision"])
    assert decision["tier"] == "direct" and decision["subagent_model"] == "grok-4.7"
    assert "GROK_COMPUTER_GROUNDING_TIER=direct" in (out / "report.md").read_text()


def test_runner_rejects_bad_input(tmp_path: Path):
    assert run_benchmark.main(["--data", str(tmp_path), "--model", "xai:grok-4.7"]) == 2
    synth.generate(tmp_path, count=1, seed=1)
    assert run_benchmark.main(["--data", str(tmp_path), "--model", "bogus"]) == 2
