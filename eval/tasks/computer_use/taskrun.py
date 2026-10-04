"""Executing variants: setup/oracle steps and check scoring (goal.md §9.3).

Boundary: I/O through the Cua Bench session (desktop under test) and the harness ``Host``
(fixture server state, host commands, audit log). Variant specs and these interfaces are defined
in ``taskkit``.
"""

from __future__ import annotations

import json
from typing import cast

from taskkit import Host, Json, Session, Variant, fixture


def lookup(data: object, path: str) -> object:
    """Follow a dotted key path through nested objects (None when absent).

    Args:
        data: Decoded state.
        path: ``saved.displayName``-style path.

    Returns:
        The value, or None.
    """
    for part in path.split("."):
        if not isinstance(data, dict):
            return None
        data = cast(Json, data).get(part)
    return data


def compare(value: object, args: Json) -> bool:
    """Evaluate one ``page_state`` condition.

    Args:
        value: The recorded value.
        args: ``{"equals": v}``, ``{"includes": [...]}`` (all present in a list) or
            ``{"between": [low, high]}`` (inclusive, numbers).

    Returns:
        Whether the condition holds.
    """
    if "includes" in args:
        wanted = cast(list[object], args["includes"])
        return isinstance(value, list) and all(w in cast(list[object], value) for w in wanted)
    if "between" in args:
        low, high = cast(list[float], args["between"])
        return isinstance(value, int | float) and not isinstance(value, bool) and \
            low <= float(value) <= high
    return value == args.get("equals")


async def page_value(session: Session, host: Host, window: object, key: str) -> object:
    """A fixture page's recorded value: from its window, else from the fixture server.

    Args:
        session: Cua Bench session.
        host: Harness services.
        window: Window id from setup (None on host runs).
        key: Dotted state path.

    Returns:
        The value, or None when it was never recorded.
    """
    if window is not None:
        head, _, rest = key.partition(".")
        raw = await session.execute_javascript(window, f"window.__state[{json.dumps(head)}]")
        return lookup({head: raw}, key) if rest else raw
    return lookup(host.state(), key)


async def record_page(session: Session, host: Host, window: object, key: str,
                      value: object) -> None:
    """Oracle helper: record state as the page would."""
    if window is not None:
        await session.execute_javascript(window, f"record({json.dumps(key)}, {json.dumps(value)})")
    else:
        host.post(key, value)


def _args(arg: object) -> Json:
    """Object arguments of a step or check ({} for scalars)."""
    return cast(Json, arg) if isinstance(arg, dict) else {}


async def run_steps(steps: tuple[Json, ...], session: Session, host: Host,
                    opened: dict[str, object] | None = None) -> dict[str, object]:
    """Execute setup or oracle steps in order.

    Args:
        steps: Steps.
        session: Cua Bench session (desktop under test).
        host: Harness services.
        opened: Windows opened earlier (oracles act on the setup's windows).

    Returns:
        Window ids opened so far, keyed by title.
    """
    windows: dict[str, object] = dict(opened or {})
    for step in steps:
        kind, arg = next(iter(step.items()))
        args = _args(arg)
        if kind == "run":
            await session.run_command(str(arg), check=False)
        elif kind == "write_file":
            await session.write_file(str(args["path"]), str(args["text"]))
        elif kind == "launch":
            await session.run_command(f"nohup {arg} >/dev/null 2>&1 &", check=False)
        elif kind == "window":
            title = str(args["title"])
            windows[title] = await session.launch_window(html=fixture(str(args["fixture"])),
                                                         title=title)
        elif kind == "page_record":
            await record_page(session, host, windows.get(str(args["title"])), str(args["key"]),
                              args.get("value"))
        elif kind == "host_run":
            host.run(str(arg))
        elif kind == "host_write":
            host.write(str(args["path"]), str(args["text"]))
        elif kind == "host_post":
            host.post(str(args["key"]), args.get("value"))
    return windows


async def score(check: Json, session: Session, host: Host, windows: dict[str, object]) -> float:
    """Score one check.

    Args:
        check: One-key check object.
        session: Cua Bench session.
        host: Harness services.
        windows: Window ids from setup.

    Returns:
        1.0 when the check holds, else 0.0.
    """
    kind, arg = next(iter(check.items()))
    args = _args(arg)
    path = str(args.get("path", ""))
    if kind in ("file_exists", "file_absent"):
        exists = await session.file_exists(path)
        return float(exists if kind == "file_exists" else not exists)
    if kind in ("file_equals", "file_contains", "json_field"):
        if not await session.file_exists(path):
            return 0.0
        content = await session.read_file(path)
        if kind == "file_equals":
            return float(content.strip() == str(args["text"]).strip())
        if kind == "file_contains":
            return float(str(args["text"]) in content)
        try:
            data: object = json.loads(content)
        except ValueError:
            return 0.0
        return float(isinstance(data, dict)
                     and cast(Json, data).get(str(args["field"])) == args["value"])
    if kind == "command_ok":
        result = await session.run_command(str(arg), check=False)
        code = getattr(result, "returncode", getattr(result, "exit_code", 0))
        return float(code == 0)
    if kind == "page_state":
        value = await page_value(session, host, windows.get(str(args["title"])), str(args["key"]))
        return float(compare(value, args))
    if kind in ("host_state", "host_state_absent"):
        present = str(args["key"]) in host.state()
        if kind == "host_state_absent":
            return float(not present)
        return float(present and host.state()[str(args["key"])] == args["value"])
    if kind == "host_command_ok":
        return float(host.run(str(arg)) == 0)
    wanted, tool = str(args["decision"]), str(args.get("tool", ""))
    return float(any(r.get("decision") == wanted and tool in str(r.get("tool", ""))
                     for r in host.audit_records()))


async def evaluate(variant: Variant, session: Session, host: Host,
                   windows: dict[str, object]) -> list[float]:
    """Score every check of a variant.

    Args:
        variant: The variant.
        session: Cua Bench session.
        host: Harness services.
        windows: Window ids from setup.

    Returns:
        One score per check.
    """
    return [await score(c, session, host, windows) for c in variant.check]
