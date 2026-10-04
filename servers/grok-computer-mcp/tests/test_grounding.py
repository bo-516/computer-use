"""Grounding clients: coordinate conventions, answer parsing, sampling, HTTP failures."""

from __future__ import annotations

import json

import httpx2
import pytest

from grok_computer_mcp.geometry import Point
from grok_computer_mcp.grounding.base import GroundingError, clamp, cluster
from grok_computer_mcp.grounding.uitars import UITarsGrounder, parse_point, smart_resize, to_image
from grok_computer_mcp.grounding.xai import XaiGrounder, parse_candidates

pytestmark = pytest.mark.anyio
JPEG = b"\xff\xd8fake"


def completions(*texts: str) -> httpx2.Response:
    """An OpenAI-style chat completion response."""
    return httpx2.Response(200, json={"choices": [{"message": {"content": t}} for t in texts]})


def test_smart_resize_matches_qwen_rules() -> None:
    assert smart_resize(800, 1280) == (812, 1288)
    h, w = smart_resize(4000, 6000)
    assert h % 28 == 0 and w % 28 == 0 and h * w <= 16384 * 28 * 28
    assert smart_resize(10, 10)[0] >= 28


@pytest.mark.parametrize(
    "text,expected",
    [
        ("click(start_box='(640,412)')", Point(640, 412)),
        ("<point>10 20</point>", Point(10, 20)),
        ("[100, 200, 300, 400]", Point(200, 300)),
        ('{"x": 5, "y": 6}', Point(5, 6)),
        ("no coordinates here", None),
    ],
)
def test_parse_point_formats(text: str, expected: Point | None) -> None:
    assert parse_point(text) == expected


def test_coordinate_conventions() -> None:
    assert to_image(Point(500, 500), 1280, 800, "relative_1000") == Point(640, 400)
    mapped = to_image(Point(1288, 812), 1280, 800, "smart_resize")
    assert abs(mapped.x - 1280) < 1e-6 and abs(mapped.y - 800) < 1e-6
    assert to_image(Point(3, 4), 1280, 800, "image") == Point(3, 4)


def test_cluster_confidence_and_clamp() -> None:
    groups = cluster([Point(100, 100), Point(105, 98), Point(900, 500)], total=3)
    assert groups[0].confidence == pytest.approx(2 / 3)
    assert groups[1].confidence == pytest.approx(1 / 3)
    assert clamp(Point(-0.5, 10), 100, 100) == Point(0, 10)
    assert clamp(Point(-50, 10), 100, 100) is None


async def test_uitars_samples_and_clusters() -> None:
    bodies: list[dict[str, object]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        bodies.append(json.loads(request.content))
        assert request.headers["Authorization"] == "Bearer k"
        return completions("(644,406)", "(640,410)", "(100,100)")

    grounder = UITarsGrounder(
        "http://ui-tars.test/v1", "ui-tars", "k", "image", httpx2.MockTransport(handler)
    )
    found = await grounder.locate(JPEG, 1280, 800, "the Save button")
    assert found[0].confidence == pytest.approx(2 / 3)
    assert abs(found[0].point.x - 642) < 1
    assert bodies[0]["n"] == 3


async def test_uitars_tops_up_when_the_server_ignores_n() -> None:
    calls = [0]

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls[0] += 1
        return completions("(10,10)")

    grounder = UITarsGrounder("http://x.test/v1", "m", None, "image", httpx2.MockTransport(handler))
    found = await grounder.locate(JPEG, 100, 100, "x")
    assert calls[0] == 3 and found[0].confidence == pytest.approx(1.0)


async def test_uitars_errors() -> None:
    def failing(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(500, text="boom")

    grounder = UITarsGrounder("http://x.test/v1", "m", None, "image", httpx2.MockTransport(failing))
    with pytest.raises(GroundingError, match="HTTP 500"):
        await grounder.locate(JPEG, 100, 100, "x")

    def nonsense(request: httpx2.Request) -> httpx2.Response:
        return completions("I cannot see it", "nope", "?")

    grounder = UITarsGrounder(
        "http://x.test/v1", "m", None, "image", httpx2.MockTransport(nonsense)
    )
    with pytest.raises(GroundingError, match="no coordinate"):
        await grounder.locate(JPEG, 100, 100, "x")


def test_xai_answer_parsing() -> None:
    text = (
        'Here: ```json\n{"candidates": [{"x": 10, "y": 20, "confidence": 0.8}, '
        '{"x": 9999, "y": 1, "confidence": 0.5}, {"x": "a", "y": 1}]}\n```'
    )
    found = parse_candidates(text, 1280, 800)
    assert [(c.point, c.confidence) for c in found] == [(Point(10, 20), 0.8)]
    with pytest.raises(GroundingError):
        parse_candidates("no json", 10, 10)


async def test_xai_grounder_round_trip() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        assert body["temperature"] == 0 and body["model"] == "grok-4.7"
        return completions('{"candidates": [{"x": 50, "y": 60, "confidence": 0.9}]}')

    grounder = XaiGrounder(
        "https://api.x.test/v1", "grok-4.7", "key", httpx2.MockTransport(handler)
    )
    found = await grounder.locate(JPEG, 1280, 800, "Save")
    assert found[0].point == Point(50, 60) and found[0].confidence == 0.9
