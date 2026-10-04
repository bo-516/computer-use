"""Shared logic for the computer-use plugin hooks (guard, audit, verify gate, self-check).

Boundary: stdlib only and Python 3.8 compatible, because the plugin ships files, not runtimes,
and the hooks run on whatever ``python3`` the user already has (AGENTS.md). Nothing here may
open a network connection or import a heavy module; the host gives hooks 5 s and we budget
under 100 ms of cold start.

The same package is vendored byte-for-byte into ``grok_computer_mcp.hooklib`` so the facade
can expose ``grok-computer-mcp guard`` and reuse the exact policy semantics for its own
fail-closed checks. ``tests/test_hooklib_sync.py`` keeps the two copies identical; edit the
plugin copy and run ``scripts/sync_hooklib.py``.
"""

HOOKLIB_VERSION = "0.2.0"
