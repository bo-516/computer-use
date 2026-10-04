"""Cua Driver backend, part 2: actions (``CuaBackend`` is the class the facade uses).

Boundary: maps click/type/keys/scroll/drag onto Cua tools. Element actions use the ``element_token``
of the latest snapshot; point actions use window-local capture pixels plus the ``capture_id`` the
facade validated, so the driver refuses rather than guesses when its capture context moved.
"""

from __future__ import annotations

import sys

from ..geometry import Point
from .base import (
    ActionOutcome,
    BackendError,
    BackendErrorKind,
    Button,
    Delivery,
    Direction,
    ElementTarget,
    PointTarget,
    RawRegion,
    Target,
    WindowInfo,
    WindowSnapshot,
)
from .cua_core import CUA_KEYS, CUA_MODIFIERS, MODIFIERS, CuaCore, JsonObject, window_target
from .cua_parse import items, num, outcome_of, rect, text


class CuaBackend(CuaCore):
    """``Backend`` over Cua Driver."""

    def _addr(self, target: Target) -> JsonObject:
        """Addressing fields of a target (element token, or window-local capture pixels)."""
        if isinstance(target, ElementTarget):
            return {"target": window_target(target.window), "element_token": target.handle}
        args: JsonObject = {"x": round(target.point.x, 1), "y": round(target.point.y, 1)}
        if target.window is not None:
            args["target"] = window_target(target.window)
        if target.capture_id:
            args["capture_id"] = target.capture_id
        return args

    async def click(self, target: Target, *, button: Button, count: int,
                    modifiers: tuple[str, ...], delivery: Delivery) -> ActionOutcome:
        """``click`` / ``double_click`` (element tokens), pixel clicks with a count."""
        args = {**self._addr(target), "button": button, "delivery_mode": delivery.value}
        if modifiers:
            args["modifier"] = [CUA_MODIFIERS.get(m, m) for m in modifiers]
        tool = "click"
        if isinstance(target, ElementTarget) and count == 2:
            tool = "double_click"
        elif isinstance(target, PointTarget):
            args["count"] = count
        elif count > 1 and target.fallback_point is not None:
            args = {**self._addr(PointTarget(target.window, target.fallback_point)),
                    "button": button, "count": count, "delivery_mode": delivery.value}
        return outcome_of((await self._call(tool, args)).structured)

    async def type_text(self, window: WindowInfo, target: ElementTarget | None, text: str, *,
                        delivery: Delivery) -> ActionOutcome:
        """``type_text`` into the element or the focused element."""
        args: JsonObject = {"target": window_target(window), "text": text,
                            "delivery_mode": delivery.value}
        if target is not None and target.handle:
            args["element_token"] = target.handle
        return outcome_of((await self._call("type_text", args)).structured)

    async def clear_text(self, window: WindowInfo, target: ElementTarget | None) -> ActionOutcome:
        """``set_value`` to "" on the element, else select-all + delete on the focused one."""
        if target is not None and target.handle:
            reply = await self._call("set_value", {"target": window_target(window),
                                                   "element_token": target.handle, "value": ""})
            return outcome_of(reply.structured)
        select_all = ["cmd", "a"] if sys.platform == "darwin" else ["ctrl", "a"]
        await self._call("hotkey", {"target": window_target(window), "keys": select_all})
        return outcome_of((await self._call("press_key", {"target": window_target(window),
                                                          "key": "delete"})).structured)

    async def press_keys(self, window: WindowInfo, chord: str, *,
                         delivery: Delivery) -> ActionOutcome:
        """``press_key`` for a bare key, ``hotkey`` for chords with modifiers."""
        parts = chord.split("+")
        mods = [CUA_MODIFIERS[p] for p in parts if p in MODIFIERS]
        key = next((CUA_KEYS.get(p, p) for p in parts if p not in MODIFIERS), None)
        base: JsonObject = {"target": window_target(window), "delivery_mode": delivery.value}
        if key is None:
            raise BackendError(BackendErrorKind.PROTOCOL, f"chord {chord!r} has no key")
        if not mods:
            return outcome_of((await self._call("press_key", {**base, "key": key})).structured)
        return outcome_of((await self._call("hotkey", {**base, "keys": [*mods, key]})).structured)

    async def scroll(self, target: Target | None, window: WindowInfo, *, direction: Direction,
                     amount: int, delivery: Delivery) -> ActionOutcome:
        """``scroll`` at an element/point, or the window."""
        args: JsonObject = {"direction": direction, "amount": amount,
                            "delivery_mode": delivery.value}
        args.update(self._addr(target) if target is not None
                    else {"target": window_target(window)})
        return outcome_of((await self._call("scroll", args)).structured)

    async def drag(self, window: WindowInfo, start: Point, end: Point, *,
                   delivery: Delivery) -> ActionOutcome:
        """``drag`` between window-local capture pixels."""
        args: JsonObject = {"target": window_target(window), "from_x": round(start.x, 1),
                            "from_y": round(start.y, 1), "to_x": round(end.x, 1),
                            "to_y": round(end.y, 1), "delivery_mode": delivery.value}
        return outcome_of((await self._call("drag", args)).structured)

    async def cursor_position(self) -> Point | None:
        """``get_cursor_position`` in screen points."""
        try:
            s = (await self._call("get_cursor_position", {})).structured
        except BackendError:
            return None
        x, y = num(s.get("x")), num(s.get("y"))
        return Point(x, y) if x is not None and y is not None else None

    async def regions(self, snapshot: WindowSnapshot) -> list[RawRegion]:
        """``parse_visual_regions`` on the snapshot's capture (optional extension)."""
        if snapshot.capture is None or not snapshot.capture.capture_id:
            return []
        reply = await self._call("parse_visual_regions",
                                 {"capture_id": snapshot.capture.capture_id})
        out: list[RawRegion] = []
        for rec in items(reply.structured.get("regions")):
            box = rect(rec.get("bbox") or rec.get("frame"))
            if box is not None:
                out.append(RawRegion(box, text(rec.get("kind")) or "region", text(rec.get("text"))))
        return out

