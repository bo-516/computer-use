"""Read Cua Driver answers into backend types (pure).

Boundary: no I/O. Field names follow the Cua Driver MCP reference (``get_window_state`` returns
``elements[]`` with ``element_index``, ``element_token``, ``role``, ``label``, ``value``,
``actions``, ``frame{x,y,w,h}``, ``parent_index``, ``depth``, plus ``window_bounds``,
``screenshot_scale``, ``screenshot_width/height``, ``capture_id``, ``degraded_reason``,
``truncated``; actions return ``path``, ``verified``, ``effect``, ``escalation``).

V16: parts the reference leaves open (where an error code sits, optional element flags such as
``focused``/``enabled``/``subrole``, the CLI's output shape) are read tolerantly: every known
spelling is accepted and missing fields fall back to neutral defaults. The backend contract tests
(goal.md §9.1) pin them once a real driver is available.
"""

from __future__ import annotations

from typing import cast

from ..geometry import Rect
from .base import (
    ActionOutcome,
    AppInfo,
    BackendError,
    BackendErrorKind,
    Effect,
    RawElement,
    WindowInfo,
)
from .cua_transport import CuaReply

JsonObject = dict[str, object]

CODE_KINDS: dict[str, BackendErrorKind] = {
    "background_unavailable": BackendErrorKind.BACKGROUND_UNAVAILABLE,
    "background_occluded": BackendErrorKind.OCCLUDED,
    "background_uipi_blocked": BackendErrorKind.ELEVATED,
    "stale_element_token": BackendErrorKind.STALE_HANDLE,
    "window_id_not_found": BackendErrorKind.WINDOW_GONE,
    "window_target_not_found": BackendErrorKind.WINDOW_GONE,
    "window_owner_pid_mismatch": BackendErrorKind.WINDOW_GONE,
    "app_not_found": BackendErrorKind.NOT_FOUND,
    "not_found": BackendErrorKind.NOT_FOUND,
    "timeout": BackendErrorKind.TIMEOUT,
    "disabled": BackendErrorKind.NOT_INTERACTABLE,
    "not_actionable": BackendErrorKind.NOT_INTERACTABLE,
}
PERMISSION_HINTS = ("permission", "not_trusted", "tcc", "accessibility", "screen_recording")
EFFECTS: dict[str, Effect] = {"confirmed": "confirmed", "unverifiable": "unverifiable",
                              "suspected_noop": "suspected_noop"}


def obj(value: object) -> JsonObject:
    """A JSON object or ``{}``."""
    return cast(JsonObject, value) if isinstance(value, dict) else {}


def items(value: object) -> list[JsonObject]:
    """The objects of a JSON list (non-objects dropped)."""
    if not isinstance(value, list):
        return []
    return [cast(JsonObject, v) for v in cast(list[object], value) if isinstance(v, dict)]


def num(value: object) -> float | None:
    """A JSON number as float (booleans excluded)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def text(value: object) -> str:
    """A JSON string or ""."""
    return value if isinstance(value, str) else ""


def rect(value: object) -> Rect | None:
    """``{x, y, w|width, h|height}`` or ``[x, y, w, h]`` as a ``Rect``."""
    if isinstance(value, list):
        nums = [num(v) for v in cast(list[object], value)]
        if len(nums) == 4 and all(n is not None for n in nums):
            x, y, w, h = (n or 0.0 for n in nums)
            return Rect(x, y, w, h)
        return None
    o = obj(value)
    x, y = num(o.get("x")), num(o.get("y"))
    w = num(o.get("w", o.get("width")))
    h = num(o.get("h", o.get("height")))
    if x is None or y is None or w is None or h is None:
        return None
    return Rect(x, y, max(0.0, w), max(0.0, h))


def window_info(rec: JsonObject) -> WindowInfo | None:
    """A ``list_windows``/``launch_app`` window record."""
    wid, bounds = num(rec.get("window_id")), rect(rec.get("bounds"))
    if wid is None or bounds is None:
        return None
    pid = num(rec.get("pid"))
    z = num(rec.get("z_index"))
    title = text(rec.get("title"))
    unsaved = rec.get("document_edited") is True or title.endswith(" — Edited")
    return WindowInfo(int(wid), int(pid) if pid is not None else None,
                      text(rec.get("app_name") or rec.get("app")), title, bounds,
                      rec.get("is_on_screen", True) is not False,
                      int(z) if z is not None else None, unsaved)


def windows_of(structured: JsonObject) -> list[WindowInfo]:
    """Windows of a ``list_windows``/``launch_app`` answer."""
    return [w for w in (window_info(r) for r in items(structured.get("windows"))) if w]


def apps_of(structured: JsonObject) -> list[AppInfo]:
    """Apps of a ``list_apps`` answer."""
    out: list[AppInfo] = []
    for rec in items(structured.get("apps")):
        pid = num(rec.get("pid"))
        running = rec.get("running") is True
        out.append(AppInfo(text(rec.get("name")), int(pid) if pid and running else None,
                           text(rec.get("bundle_id")) or None, running, rec.get("active") is True))
    return out


def elements_of(structured: JsonObject) -> list[RawElement]:
    """Elements of a ``get_window_state`` answer, in document order."""
    out: list[RawElement] = []
    for pos, rec in enumerate(items(structured.get("elements"))):
        index = num(rec.get("element_index"))
        parent = num(rec.get("parent_index"))
        depth = num(rec.get("depth"))
        selected = rec.get("selected")
        actions = [a for a in cast(list[object], rec.get("actions") or [])
                   if isinstance(a, str)] if isinstance(rec.get("actions"), list) else []
        value = rec.get("value")
        out.append(RawElement(
            index=int(index) if index is not None else pos, role=text(rec.get("role")),
            label=text(rec.get("label") or rec.get("title")),
            value=None if value is None else str(value), frame=rect(rec.get("frame")),
            handle=text(rec.get("element_token")) or None, subrole=text(rec.get("subrole")),
            enabled=rec.get("enabled", True) is not False, focused=rec.get("focused") is True,
            selected=selected if isinstance(selected, bool) else None,
            parent=int(parent) if parent is not None else None,
            depth=int(depth) if depth is not None else 0, actions=tuple(actions)))
    return out


def _code(reply: CuaReply) -> str:
    """The driver's error/refusal code, wherever it sits (V16)."""
    s = reply.structured
    for candidate in (s.get("code"), obj(s.get("error")).get("code"), s.get("refusal"),
                      s.get("error") if isinstance(s.get("error"), str) else None):
        if isinstance(candidate, str) and candidate:
            return candidate
    lowered = reply.text.lower()
    return next((code for code in CODE_KINDS if code in lowered), "")


def classify(reply: CuaReply) -> BackendError | None:
    """Turn a failed or refused answer into a ``BackendError``.

    Args:
        reply: The driver's answer.

    Returns:
        The error, or None when the call succeeded.
    """
    s = reply.structured
    refused = s.get("ok") is False or s.get("effect") == "refused" or reply.is_error
    if not refused:
        return None
    code = _code(reply)
    message = text(s.get("message")) or text(obj(s.get("error")).get("message")) or (
        reply.text[:300] or code or "cua-driver refused the call")
    kind = CODE_KINDS.get(code)
    if kind is None:
        lowered = f"{code} {message}".lower()
        kind = (BackendErrorKind.PERMISSION if any(h in lowered for h in PERMISSION_HINTS)
                else BackendErrorKind.PROTOCOL)
    return BackendError(kind, message, detail=code)


def outcome_of(structured: JsonObject) -> ActionOutcome:
    """Action result fields (``path``, ``verified``, ``effect``, ``escalation``)."""
    effect = text(structured.get("effect"))
    verified = structured.get("verified")
    escalation = text(obj(structured.get("escalation")).get("recommended")) or None
    return ActionOutcome(
        path=text(structured.get("path")) or "unknown",
        effect=EFFECTS.get(effect, "unknown"),
        verified=verified if isinstance(verified, bool) else None, escalation=escalation)
