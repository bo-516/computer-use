"""Command line: ``grok-computer-mcp [serve|doctor|status|guard|audit|verify-gate|selfcheck|
trace purge|stop|resume|version]``.

Boundary: process edge. ``serve`` (the default) speaks MCP on stdout, so everything else this
process says goes to stderr (AGENTS.md). The hook subcommands run the vendored ``hooklib`` entry
points, so a host without a system ``python3`` (Windows) can point ``hooks.json`` at
``grok-computer-mcp guard`` instead (goal.md §7.4 Phase 2 note).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import sys

import anyio
from mcp.server.stdio import stdio_server

from . import __version__
from . import doctor as checks_mod
from .app import create_backend, create_context
from .config import ConfigError, Settings, settings_from_process
from .errors import FacadeError
from .hooklib.entry import audit_main, guard_main
from .hooklib.entry_stop import selfcheck_main, verify_gate_main
from .safety.policy import PolicyHolder
from .server import build_server
from .trace.recorder import purge

EXIT_USAGE = 2


def _settings() -> Settings | None:
    """Load settings, reporting configuration errors on stderr."""
    try:
        return settings_from_process()
    except ConfigError as exc:
        print(f"grok-computer-mcp: invalid configuration: {exc}", file=sys.stderr)
        return None


def serve(settings: Settings) -> int:
    """Run the MCP server on stdio until the client disconnects.

    Args:
        settings: Facade settings.

    Returns:
        Exit code 0.
    """
    logging.basicConfig(stream=sys.stderr, level=settings.log_level)
    server = build_server(create_context(settings))

    async def run() -> None:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    anyio.run(run)
    return 0


def doctor(settings: Settings, as_json: bool) -> int:
    """Run the installation self-check.

    Args:
        settings: Facade settings.
        as_json: Print JSON instead of text.

    Returns:
        0 when every critical check passed, else 1.
    """
    checks = checks_mod.static_checks(settings)
    checks += anyio.run(checks_mod.backend_checks, settings, create_backend(settings))
    print(json.dumps(checks_mod.as_json(checks), indent=2) if as_json
          else checks_mod.render(checks))
    return 0 if all(c.ok or not c.critical for c in checks) else 1


def status(settings: Settings) -> int:
    """Print management status (paths, mode, policy, stop flag) as JSON.

    Args:
        settings: Facade settings.

    Returns:
        Exit code 0.
    """
    holder = PolicyHolder(settings.home, settings.workspace)
    with contextlib.suppress(FacadeError):  # status reports policy problems instead of failing
        holder.rules()
    print(json.dumps({
        "version": __version__, "backend": settings.backend, "mode": settings.mode,
        "state_dir": str(settings.state_dir), "trace_dir": str(settings.trace_dir),
        "lock_dir": str(settings.lock_dir), "policy_problems": list(holder.problems),
        "policy_warnings": list(holder.warnings),
        "emergency_stop": any(flag.exists() for flag in settings.stop_flags),
        "grounding": {"provider": settings.grounding_provider, "tier": settings.grounding_tier},
    }, indent=2))
    return 0


def set_stop(settings: Settings, on: bool) -> int:
    """Set the canonical emergency stop flag, or clear every flag (goal.md §7.2 L2 "急停").

    Args:
        settings: Facade settings.
        on: True to stop, False to resume.

    Returns:
        Exit code 0.
    """
    if on:
        # The canonical flag does not depend on GROK_PLUGIN_DATA, so a stop set from a plain
        # shell reaches facades that the host started with their own data directory.
        flag = settings.stop_flags[0]
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.touch()
        print(f"emergency stop set ({flag}); GUI actions are refused until `resume`",
              file=sys.stderr)
    else:
        for flag in settings.stop_flags:
            flag.unlink(missing_ok=True)
        print("emergency stop cleared", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point.

    Args:
        argv: Arguments (default: ``sys.argv[1:]``).

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(prog="grok-computer-mcp",
                                     description="Computer-use facade MCP server for Grok Build")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="run the MCP server on stdio (default)")
    doc = sub.add_parser("doctor", help="check installation, permissions and screenshot size")
    doc.add_argument("--json", action="store_true")
    sub.add_parser("status", help="print management status as JSON")
    for name in ("guard", "audit", "verify-gate", "selfcheck"):
        sub.add_parser(name, help=f"run the {name} hook (reads the hook payload on stdin)")
    trace = sub.add_parser("trace", help="manage local traces")
    trace_sub = trace.add_subparsers(dest="trace_cmd", required=True)
    purge_p = trace_sub.add_parser("purge", help="delete traces and audit logs")
    group = purge_p.add_mutually_exclusive_group()
    group.add_argument("--older-than-days", type=float, default=None)
    group.add_argument("--all", action="store_true")
    sub.add_parser("stop", help="emergency stop: refuse all GUI actions")
    sub.add_parser("resume", help="clear the emergency stop")
    sub.add_parser("version", help="print the version")
    args = parser.parse_args(argv)
    hooks = {"guard": guard_main, "audit": audit_main, "verify-gate": verify_gate_main,
             "selfcheck": selfcheck_main}
    if args.cmd in hooks:
        return hooks[args.cmd]()
    if args.cmd == "version":
        print(__version__)
        return 0
    settings = _settings()
    if settings is None:
        return EXIT_USAGE
    if args.cmd == "doctor":
        return doctor(settings, args.json)
    if args.cmd == "status":
        return status(settings)
    if args.cmd in ("stop", "resume"):
        return set_stop(settings, args.cmd == "stop")
    if args.cmd == "trace":
        days = None if args.all else (args.older_than_days if args.older_than_days is not None
                                      else float(settings.trace_retention_days))
        removed = purge(settings.trace_dir, days)
        print(f"removed {len(removed)} trace entries from {settings.trace_dir}", file=sys.stderr)
        return 0
    return serve(settings)
