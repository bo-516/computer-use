"""Tool input models (goal.md §5.4). The server validates every call with these (AGENTS.md).

Boundary: pure Pydantic models; field descriptions are model-facing (English). Coordinates are
IMAGE pixels of the observation named by ``observation_id`` (origin top-left). Names and fields
are public API: the guard hook reads ``ref``/``mark``/``point``/``keys``/``app``/``name``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..limits import (
    CLICK_MAX_COUNT,
    DEFAULT_MAX_ELEMENTS,
    MAX_ELEMENTS_LIMIT,
    SCROLL_MAX_AMOUNT,
    TYPE_TEXT_MAX_CHARS,
    WAIT_FOR_DEFAULT_MS,
    WAIT_FOR_MAX_MS,
)

OBS_ID = r"^obs_[0-9a-f]+$"
REF = r"^e[0-9]+$"
# Upper bound on chords per press_keys call (hooklib.keys.MAX_CHORDS_PER_CALL).
MAX_CHORDS = 10
NAME_MAX_CHARS = 200
DESCRIPTION_MAX_CHARS = 300
WAIT_FOR_MIN_MS = 100
MAX_PAGE = 100
MAX_MODIFIERS = 4

ObservationId = Field(pattern=OBS_ID, description="observation_id of the observation you act on")


class _Strict(BaseModel):
    """Base: unknown fields are errors, so typos surface instead of being ignored."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class PointIn(_Strict):
    """A point in the observation's image (pixels, origin top-left)."""

    x: float = Field(ge=0, description="Image x in pixels")
    y: float = Field(ge=0, description="Image y in pixels")


class TargetIn(_Strict):
    """Exactly one of ref, mark or point."""

    ref: str | None = Field(default=None, pattern=REF, description="Element ref, e.g. e7")
    mark: int | None = Field(default=None, ge=1, description="Set-of-Mark number")
    point: PointIn | None = Field(default=None, description="Image point (last resort)")

    @model_validator(mode="after")
    def _one_target(self) -> TargetIn:
        """Require exactly one addressing mode."""
        if sum(v is not None for v in (self.ref, self.mark, self.point)) != 1:
            raise ValueError("give exactly one of ref, mark or point")
        return self


class ObserveIn(_Strict):
    """Arguments of ``observe``."""

    app: str | None = Field(default=None, max_length=NAME_MAX_CHARS,
                            description="App to observe; default: the last observed window or "
                                        "the frontmost app")
    window_id: int | None = Field(default=None, description="Window from apps(action='windows')")
    mode: Literal["auto", "tree", "screenshot", "som"] = Field(
        default="auto", description="auto: tree, or Set-of-Mark screenshot when the tree is thin")
    scope: Literal["window", "screen"] = Field(
        default="window", description="screen only for cross-app work (deny-listed apps masked)")
    root_ref: str | None = Field(default=None, pattern=REF,
                                 description="Expand this container: list all its descendants")
    max_elements: int = Field(default=DEFAULT_MAX_ELEMENTS, ge=1, le=MAX_ELEMENTS_LIMIT)
    page: int = Field(default=1, ge=1, le=MAX_PAGE, description="Set-of-Mark page (80 marks each)")


class ClickIn(_Strict):
    """Arguments of ``click``: one of ref, mark or point."""

    observation_id: str = ObservationId
    ref: str | None = Field(default=None, pattern=REF)
    mark: int | None = Field(default=None, ge=1)
    point: PointIn | None = None
    button: Literal["left", "right", "middle"] = "left"
    count: int = Field(default=1, ge=1, le=CLICK_MAX_COUNT)
    modifiers: list[Literal["cmd", "ctrl", "alt", "shift", "fn"]] = Field(
        default_factory=list[Literal["cmd", "ctrl", "alt", "shift", "fn"]],
        max_length=MAX_MODIFIERS)

    @model_validator(mode="after")
    def _one_target(self) -> ClickIn:
        """Require exactly one addressing mode."""
        if sum(v is not None for v in (self.ref, self.mark, self.point)) != 1:
            raise ValueError("give exactly one of ref, mark or point")
        return self


class TypeTextIn(_Strict):
    """Arguments of ``type_text``."""

    observation_id: str = ObservationId
    ref: str | None = Field(default=None, pattern=REF,
                            description="Field to type into; default: the focused element")
    text: str = Field(min_length=1, max_length=TYPE_TEXT_MAX_CHARS)
    clear_first: bool = Field(default=False, description="Empty the field before typing")


class PressKeysIn(_Strict):
    """Arguments of ``press_keys``."""

    observation_id: str = ObservationId
    keys: str | list[str] = Field(
        description="One chord like 'cmd+s' or 'enter', or a list of chords pressed in order",
        min_length=1)

    @model_validator(mode="after")
    def _bounded(self) -> PressKeysIn:
        """At most ``MAX_CHORDS`` chords, none empty."""
        chords = [self.keys] if isinstance(self.keys, str) else self.keys
        if len(chords) > MAX_CHORDS or any(not c.strip() for c in chords):
            raise ValueError(f"keys must be 1-{MAX_CHORDS} non-empty chords")
        return self


class ScrollIn(_Strict):
    """Arguments of ``scroll``: optional ref, mark or point (default: the window)."""

    observation_id: str = ObservationId
    ref: str | None = Field(default=None, pattern=REF)
    mark: int | None = Field(default=None, ge=1)
    point: PointIn | None = None
    direction: Literal["up", "down", "left", "right"]
    amount: int = Field(default=3, ge=1, le=SCROLL_MAX_AMOUNT, description="Wheel notches")

    @model_validator(mode="after")
    def _at_most_one(self) -> ScrollIn:
        """At most one addressing mode."""
        if sum(v is not None for v in (self.ref, self.mark, self.point)) > 1:
            raise ValueError("give at most one of ref, mark or point")
        return self


class DragIn(_Strict):
    """Arguments of ``drag``."""

    observation_id: str = ObservationId
    from_: TargetIn = Field(alias="from")
    to: TargetIn


class AppsIn(_Strict):
    """Arguments of ``apps``."""

    action: Literal["list", "launch", "focus", "windows"]
    name: str | None = Field(default=None, max_length=NAME_MAX_CHARS)
    observation_id: str | None = Field(default=None, pattern=OBS_ID)

    @model_validator(mode="after")
    def _needs_name(self) -> AppsIn:
        """launch and focus need an app name."""
        if self.action in ("launch", "focus") and not (self.name and self.name.strip()):
            raise ValueError(f"apps(action='{self.action}') needs name")
        return self


class WaitForIn(_Strict):
    """Arguments of ``wait_for``."""

    text: str | None = Field(default=None, max_length=NAME_MAX_CHARS,
                             description="Wait for an element whose label or value contains this")
    ref_role: str | None = Field(default=None, max_length=40,
                                 description="Only elements of this role (e.g. 'button')")
    gone: bool = Field(default=False, description="Wait until the element disappears instead")
    app: str | None = Field(default=None, max_length=NAME_MAX_CHARS)
    window_id: int | None = None
    timeout_ms: int = Field(default=WAIT_FOR_DEFAULT_MS, ge=WAIT_FOR_MIN_MS, le=WAIT_FOR_MAX_MS)

    @model_validator(mode="after")
    def _condition(self) -> WaitForIn:
        """Need something to wait for."""
        if not self.text and not self.ref_role:
            raise ValueError("give text, ref_role, or both")
        return self


class LocateIn(_Strict):
    """Arguments of ``locate``."""

    description: str = Field(min_length=1, max_length=DESCRIPTION_MAX_CHARS,
                             description="What to find, e.g. 'the blue Export button'")
    observation_id: str = ObservationId
