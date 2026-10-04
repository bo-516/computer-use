"""Tool result models: the ``outputSchema`` of every tool and the shape of ``structuredContent``.

Boundary: pure Pydantic models. Every coordinate here is IMAGE space of the observation the result
names (AGENTS.md), which is why window lists carry no desktop bounds. Errors use ``ErrorResult``
(goal.md §5.8); each tool's output schema is ``success | error``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Box = list[int]


class ErrorResult(BaseModel):
    """``{ok: false, code, message, retryable, hint}``."""

    ok: Literal[False]
    code: str
    message: str
    retryable: bool
    hint: str


class ElementOut(BaseModel):
    """One listed element."""

    ref: str
    role: str
    label: str
    value: str | None = None
    bbox: Box | None = Field(default=None, description="[x, y, w, h] image pixels")
    secure: bool = False
    enabled: bool = True
    focused: bool = False
    visible: bool = True


class MarkOut(BaseModel):
    """One Set-of-Mark number."""

    mark: int
    ref: str | None = None
    role: str
    label: str
    bbox: Box


class WindowOut(BaseModel):
    """The observed window."""

    window_id: int | None
    title: str
    app: str


class ImageOut(BaseModel):
    """The observation image (attached or not, coordinates are always in its space)."""

    width: int
    height: int
    attached: bool
    screenshot_path: str | None = None


class ObserveResult(BaseModel):
    """Result of ``observe`` (and the observation part of ``wait_for``)."""

    ok: Literal[True]
    observation_id: str
    app: str
    scope: Literal["window", "screen"]
    mode: Literal["tree", "screenshot", "som"]
    window: WindowOut | None
    image: ImageOut
    elements: list[ElementOut]
    marks: list[MarkOut] = Field(default_factory=list[MarkOut])
    mark_pages: int = 1
    omitted: int = 0
    degraded: str | None = None
    hint: str | None = None


class HitOut(BaseModel):
    """What a coordinate or mark click actually hit."""

    point: list[float]
    ref: str | None = None
    role: str
    label: str


class ActionResult(BaseModel):
    """Result of an action: the new observation id and what changed (no image)."""

    ok: Literal[True]
    observation_id: str
    app: str
    summary: str
    changes: list[dict[str, object]]
    delivery: str
    effect: str
    hit: HitOut | None = None
    hint: str | None = None


class AppOut(BaseModel):
    """An app known to the desktop."""

    name: str
    running: bool
    active: bool = False
    denied: bool = False


class WindowListOut(BaseModel):
    """A window known to the desktop (ids only; observe it to get coordinates)."""

    window_id: int
    app: str
    title: str
    on_screen: bool
    denied: bool = False


class AppsResult(BaseModel):
    """Result of ``apps``."""

    ok: Literal[True]
    action: Literal["list", "launch", "focus", "windows"]
    apps: list[AppOut] | None = None
    windows: list[WindowListOut] | None = None
    app: str | None = None


class WaitResult(ObserveResult):
    """Result of ``wait_for``: the observation in which the condition held."""

    matched: ElementOut | None = None
    waited_ms: int = 0


class CandidateOut(BaseModel):
    """A grounding candidate."""

    point: list[float] = Field(description="[x, y] image pixels")
    confidence: float
    bbox: Box | None = None


class LocateResult(BaseModel):
    """Result of ``locate``."""

    ok: Literal[True]
    observation_id: str
    candidates: list[CandidateOut]
    confident: bool
    provider: str
    image_attached: bool = False
