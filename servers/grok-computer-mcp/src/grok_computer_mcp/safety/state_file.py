"""Writer side of ``last_observation.json``, the facade -> hooks contract (goal.md A.10).

Boundary: ``build_state`` is pure; ``StateWriter`` performs the atomic write (temp file + rename,
AGENTS.md). The schema is ``docs/schemas/last_observation.schema.json``; the readers are
``hooklib.state`` / ``hooklib.targets`` (guard) and ``hooklib.verify_gate``. A schema change updates
this writer, those readers and their fixtures together.

The file only lists interactive elements, each with its IMAGE-space ``bbox`` so the guard can
hit-test coordinate clicks before the facade sees them, and stays under 64 KB.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ..hooklib import io as hook_io
from ..hooklib.paths import STATE_FILE
from ..hooklib.state import STATE_SCHEMA_VERSION
from ..limits import STATE_FILE_MAX_BYTES, STATE_LABEL_MAX_CHARS
from ..observe.elements import ObservedElement
from ..observe.tree import clip, focused_element
from ..session import Observation, now

# Label length used when the full state does not fit the 64 KB budget.
TRIMMED_LABEL_CHARS = 40
BBOX_DECIMALS = 1

Record = dict[str, object]


def element_record(el: ObservedElement, app: str) -> Record:
    """One element as the guard reads it.

    Args:
        el: Observation element.
        app: App the element belongs to.

    Returns:
        ``{ref, role, label, app, bbox?, secure?, in_form?}``.
    """
    rec: Record = {"ref": el.ref, "role": el.role,
                   "label": clip(el.label, STATE_LABEL_MAX_CHARS), "app": app}
    if el.bbox is not None:
        box = el.bbox
        rec["bbox"] = [round(v, BBOX_DECIMALS) for v in (box.x, box.y, box.w, box.h)]
    if el.secure:
        rec["secure"] = True
    if el.in_form:
        rec["in_form"] = True
    return rec


def build_state(obs: Observation, *, last_action_at: float | None, last_observe_at: float | None,
                last_hit: Record | None, screenshot_path: str | None,
                screenshot_at: float | None, updated_at: float) -> Record:
    """Build the state document for one observation.

    Args:
        obs: The current observation.
        last_action_at: Time of the last action, None before the first.
        last_observe_at: Time of the last explicit observation (observe or wait_for).
        last_hit: What the facade's hit test found for the last coordinate click.
        screenshot_path: Last saved screenshot.
        screenshot_at: When it was taken.
        updated_at: Write time.

    Returns:
        The document, trimmed to ``STATE_FILE_MAX_BYTES``.
    """
    interactive = [el for el in obs.elements if el.interactive or el.secure]
    focused = focused_element(obs.elements)
    state: Record = {
        "schema_version": STATE_SCHEMA_VERSION,
        "updated_at": updated_at,
        "observation_id": obs.id,
        "app": obs.app,
        "pid": obs.pid,
        "window_id": obs.window.window_id if obs.window else None,
        "window_title": obs.window_title,
        "window_unsaved": bool(obs.window and obs.window.unsaved),
        "scope": obs.scope,
        "image": [obs.frame.image_w, obs.frame.image_h],
        "elements": {el.ref: element_record(el, obs.app) for el in interactive},
        "marks": {str(n): dict(element_record(obs.refs[m.ref], obs.app)) if m.ref in obs.refs
                  else {"role": m.role, "label": clip(m.label, STATE_LABEL_MAX_CHARS),
                        "app": obs.app, "bbox": [m.bbox.x, m.bbox.y, m.bbox.w, m.bbox.h]}
                  for n, m in obs.marks.items()},
        "focused": element_record(focused, obs.app) if focused else None,
        "last_hit": last_hit,
        "last_action_at": last_action_at,
        "last_observe_at": last_observe_at,
        "last_screenshot_path": screenshot_path,
        "last_screenshot_at": screenshot_at,
    }
    return _fit(state, obs, interactive)


def _size(state: Record) -> int:
    """Serialized size in bytes."""
    return len(json.dumps(state, ensure_ascii=False).encode())


def _fit(state: Record, obs: Observation, interactive: list[ObservedElement]) -> Record:
    """Trim the document to the budget: off-screen elements, then labels, then the tail."""
    if _size(state) <= STATE_FILE_MAX_BYTES:
        return state
    visible = [el for el in interactive if el.visible or el.secure]
    state["elements"] = {el.ref: element_record(el, obs.app) for el in visible}
    if _size(state) <= STATE_FILE_MAX_BYTES:
        return state
    trimmed: dict[str, Record] = {}
    for el in visible:
        rec = element_record(el, obs.app)
        rec["label"] = clip(el.label, TRIMMED_LABEL_CHARS)
        trimmed[el.ref] = rec
    state["elements"] = trimmed
    state["marks"] = {}
    while _size(state) > STATE_FILE_MAX_BYTES and trimmed:
        trimmed.pop(next(reversed(trimmed)))
    return state


class StateWriter:
    """Keeps the action/observation timestamps and writes the state file atomically."""

    def __init__(self, state_dir: Path) -> None:
        """Create the writer.

        Args:
            state_dir: Directory shared with the hooks (``GROK_PLUGIN_DATA/state``).
        """
        self.path = state_dir / STATE_FILE
        self.last_action_at: float | None = None
        self.last_observe_at: float | None = None
        self.screenshot_path: str | None = None
        self.screenshot_at: float | None = None

    def write(self, obs: Observation, *, explicit_observation: bool = False,
              acting: bool = False, last_hit: Record | None = None) -> bool:
        """Write the state for ``obs``.

        Args:
            obs: Current observation.
            explicit_observation: An observe/wait_for call produced it (verify gate evidence).
            acting: An action is about to run on it (stamps ``last_action_at`` first).
            last_hit: Hit-test result of a coordinate click.

        Returns:
            True when written. A failed write is logged and tolerated: the guard then sees stale
            state and asks (the safe direction).
        """
        stamp = now()
        if acting:
            self.last_action_at = stamp
        if explicit_observation:
            self.last_observe_at = stamp
        if obs.screenshot_path and obs.screenshot_path != self.screenshot_path:
            self.screenshot_path, self.screenshot_at = obs.screenshot_path, obs.created_at
        state = build_state(obs, last_action_at=self.last_action_at,
                            last_observe_at=self.last_observe_at, last_hit=last_hit,
                            screenshot_path=self.screenshot_path,
                            screenshot_at=self.screenshot_at, updated_at=stamp)
        ok = hook_io.write_json_atomic(str(self.path), state)
        if not ok:
            print(f"grok-computer-mcp: cannot write {self.path}", file=sys.stderr)
        return ok
