"""The nine public tools: names, descriptions, models and MCP annotations (goal.md §5.4).

Boundary: declarations only. Tool and server names are public API (``computer__<tool>`` feeds hook
matchers and permission rules); never rename one, and call out any addition explicitly
(AGENTS.md). ``status`` is deliberately not a tool: it is the ``grok-computer-mcp status`` CLI.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

import mcp_types as types
from pydantic import BaseModel

from .handlers import actions, actions_pointer, apps, locate, observe
from .handlers.context import FacadeContext, ToolOutput
from .models import inputs, outputs
from .schema import output_schema, tool_input_schema

M = TypeVar("M", bound=BaseModel)
Runner = Callable[[FacadeContext, dict[str, object]], Awaitable[ToolOutput]]


@dataclass(frozen=True)
class ToolSpec:
    """One public tool."""

    name: str
    title: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    read_only: bool
    destructive: bool
    idempotent: bool
    open_world: bool
    run: Runner


def _bind(model: type[M], handler: Callable[[FacadeContext, M], Awaitable[ToolOutput]]) -> Runner:
    """Validate raw arguments with ``model`` and call ``handler``."""

    async def run(ctx: FacadeContext, args: dict[str, object]) -> ToolOutput:
        return await handler(ctx, model.model_validate(args))

    return run


TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec("observe", "Observe the screen",
             "Observe the GUI: interactive elements with refs (e7) and [x,y,w,h] boxes in image "
             "pixels. mode auto returns the tree, or a Set-of-Mark screenshot when the tree is "
             "thin; screenshot/som attach an image. root_ref expands a container; scope='screen' "
             "lists windows for cross-app work. Start every step with this.",
             inputs.ObserveIn, outputs.ObserveResult, True, False, True, False,
             _bind(inputs.ObserveIn, observe.observe)),
    ToolSpec("click", "Click",
             "Click an element: prefer ref, then mark (from a som observation), then point "
             "(image pixels, last resort). Pass the observation_id you act on. Returns what "
             "changed and the next observation_id.",
             inputs.ClickIn, outputs.ActionResult, False, True, False, False,
             _bind(inputs.ClickIn, actions.click)),
    ToolSpec("type_text", "Type text",
             "Type text into the field ref, or the focused field. Refused for password and "
             "credential fields: never type secrets. clear_first empties the field first.",
             inputs.TypeTextIn, outputs.ActionResult, False, True, False, False,
             _bind(inputs.TypeTextIn, actions.type_text)),
    ToolSpec("press_keys", "Press keys",
             "Press a key chord such as 'cmd+s', 'enter' or 'tab', or a list of chords in order. "
             "Quit, log-out, lock, task-manager and run-dialog shortcuts are refused.",
             inputs.PressKeysIn, outputs.ActionResult, False, True, False, False,
             _bind(inputs.PressKeysIn, actions.press_keys)),
    ToolSpec("scroll", "Scroll",
             "Scroll the window, or at a ref/mark/point, by wheel notches.",
             inputs.ScrollIn, outputs.ActionResult, False, False, False, False,
             _bind(inputs.ScrollIn, actions_pointer.scroll)),
    ToolSpec("drag", "Drag",
             "Drag from one ref/mark/point to another in the same observation.",
             inputs.DragIn, outputs.ActionResult, False, True, False, False,
             _bind(inputs.DragIn, actions_pointer.drag)),
    ToolSpec("apps", "Apps and windows",
             "list apps, list windows (optionally of one app), launch an app in the background, "
             "or focus (bring to front) an app.",
             inputs.AppsIn, outputs.AppsResult, False, False, True, False,
             _bind(inputs.AppsIn, apps.apps)),
    ToolSpec("wait_for", "Wait for an element",
             "Wait until an element whose label or value contains text (optionally of a role) "
             "appears, or disappears with gone=true. Returns a fresh observation.",
             inputs.WaitForIn, outputs.WaitResult, True, False, True, False,
             _bind(inputs.WaitForIn, observe.wait_for)),
    ToolSpec("locate", "Locate by description",
             "Find a described target in the current screenshot with a grounding model when refs "
             "and marks fail. Returns candidate points in image pixels; when not confident, up "
             "to 3 candidates and an annotated screenshot.",
             inputs.LocateIn, outputs.LocateResult, True, False, True, True,
             _bind(inputs.LocateIn, locate.locate)),
)
TOOLS_BY_NAME = {t.name: t for t in TOOLS}




def tool_definitions() -> list[types.Tool]:
    """MCP tool definitions (stable order).

    Returns:
        The nine tools with input/output schemas and annotations.
    """
    return [types.Tool(
        name=spec.name, title=spec.title, description=spec.description,
        input_schema=tool_input_schema(spec.input_model),
        output_schema=output_schema(spec.output_model, outputs.ErrorResult),
        annotations=types.ToolAnnotations(
            title=spec.title, read_only_hint=spec.read_only, destructive_hint=spec.destructive,
            idempotent_hint=spec.idempotent, open_world_hint=spec.open_world))
        for spec in TOOLS]
