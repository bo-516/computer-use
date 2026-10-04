"""Where the hooks and the facade keep state, audit logs and policy files.

Boundary: pure functions of an environment mapping and a home directory; nothing here touches
the filesystem. The facade resolves the same paths through the vendored copy of this module,
which is what keeps ``last_observation.json`` (the facade <-> hooks contract) in one place.

Resolution order (AGENTS.md: state paths come from ``GROK_PLUGIN_DATA``; ``~/.grok-computer/`` is
only the fallback):

1. ``GROK_COMPUTER_STATE_DIR`` / ``GROK_COMPUTER_TRACE_DIR`` when set to a usable path;
2. ``$GROK_PLUGIN_DATA/state`` and ``$GROK_PLUGIN_DATA/traces``;
3. ``~/.grok-computer/state`` and ``~/.grok-computer/traces``.
"""

from __future__ import annotations

import os
import re
from typing import List, Mapping, Optional

FALLBACK_DIRNAME = ".grok-computer"
STATE_FILE = "last_observation.json"
USER_POLICY_FILE = "policy.json"
PROJECT_POLICY_PARTS = (".grok", "computer-policy.json")
# Emergency stop flag (goal.md §7.2 L2 "急停"): while it exists every facade action is refused.
STOP_FILE = "STOP"
# Session ids become file names; keep them short and portable.
SESSION_ID_MAX_CHARS = 64
_UNSAFE_SESSION_CHARS = re.compile(r"[^A-Za-z0-9_.-]")


def usable_dir(value: Optional[str]) -> Optional[str]:
    """Return ``value`` if it is a usable absolute directory path, else None.

    V5: the host may hand an MCP server ``${GROK_PLUGIN_DATA}`` verbatim (no expansion) or expand
    it to "" when the variable is not set for MCP processes. Both must fall back instead of
    creating ``./${GROK_PLUGIN_DATA}`` or writing to ``/state``.

    Args:
        value: Raw environment value.

    Returns:
        The normalized path, or None for empty, relative or unexpanded values.
    """
    if not value or "$" in value:
        return None
    path = os.path.normpath(value)
    if not os.path.isabs(path) or path == os.path.sep:
        return None
    return path


def fallback_root(home: str) -> str:
    """Return ``~/.grok-computer`` for the given home directory.

    Args:
        home: The user's home directory.

    Returns:
        The fallback data root.
    """
    return os.path.join(home, FALLBACK_DIRNAME)


def data_root(env: Mapping[str, str], home: str) -> str:
    """Return the plugin data root: ``$GROK_PLUGIN_DATA`` or the fallback.

    Args:
        env: Process environment.
        home: The user's home directory.

    Returns:
        An absolute directory path (may not exist yet).
    """
    return usable_dir(env.get("GROK_PLUGIN_DATA")) or fallback_root(home)


def state_dir(env: Mapping[str, str], home: str) -> str:
    """Return the directory the facade writes ``last_observation.json`` into.

    Args:
        env: Process environment.
        home: The user's home directory.

    Returns:
        The primary state directory.
    """
    explicit = usable_dir(env.get("GROK_COMPUTER_STATE_DIR"))
    return explicit or os.path.join(data_root(env, home), "state")


def state_dir_candidates(env: Mapping[str, str], home: str) -> List[str]:
    """Return every directory the hooks should read state from, primary first.

    V5: if the facade could not see ``GROK_PLUGIN_DATA`` it writes to the fallback root while the
    hooks (which always get the variable) look under the plugin data dir. Reading both and
    keeping the freshest state makes that mismatch harmless.

    Args:
        env: Process environment.
        home: The user's home directory.

    Returns:
        Distinct directories in priority order.
    """
    dirs = [state_dir(env, home), os.path.join(fallback_root(home), "state")]
    return list(dict.fromkeys(dirs))


def stop_flag_paths(env: Mapping[str, str], home: str) -> List[str]:
    """Return every emergency stop flag location, the canonical per-user one first.

    The stop must reach every facade on the machine whatever data directory its host gave it:
    ``grok-computer-mcp stop`` usually runs in a plain shell without ``GROK_PLUGIN_DATA`` while
    the facade runs with it. The canonical flag therefore lives at a fixed per-user path
    (``~/.grok-computer/STOP``, like the desktop lock) that does not depend on the environment;
    a flag in any state directory is honoured too. A flag anywhere means stopped (fail closed).

    Args:
        env: Process environment.
        home: The user's home directory.

    Returns:
        Distinct flag paths, canonical first.
    """
    flags = [os.path.join(fallback_root(home), STOP_FILE)]
    flags += [os.path.join(d, STOP_FILE) for d in state_dir_candidates(env, home)]
    return list(dict.fromkeys(flags))


def trace_dir(env: Mapping[str, str], home: str) -> str:
    """Return the directory for audit logs and facade traces.

    Args:
        env: Process environment.
        home: The user's home directory.

    Returns:
        The trace directory.
    """
    explicit = usable_dir(env.get("GROK_COMPUTER_TRACE_DIR"))
    return explicit or os.path.join(data_root(env, home), "traces")


def user_policy_path(home: str) -> str:
    """Return ``~/.grok-computer/policy.json`` (never relocated by GROK_PLUGIN_DATA).

    Args:
        home: The user's home directory.

    Returns:
        The user policy path.
    """
    return os.path.join(fallback_root(home), USER_POLICY_FILE)


def project_policy_path(workspace_root: Optional[str]) -> Optional[str]:
    """Return ``<workspace>/.grok/computer-policy.json``, or None without a workspace.

    Args:
        workspace_root: The session workspace root from the hook payload or the environment.

    Returns:
        The project policy path, or None.
    """
    if not workspace_root or not os.path.isabs(workspace_root):
        return None
    return os.path.join(workspace_root, *PROJECT_POLICY_PARTS)


def safe_session(session_id: object) -> str:
    """Turn a host session id into a safe file-name component.

    Args:
        session_id: ``sessionId`` from the hook payload (any type).

    Returns:
        A non-empty string of ``[A-Za-z0-9_.-]``.
    """
    text = session_id if isinstance(session_id, str) else ""
    cleaned = _UNSAFE_SESSION_CHARS.sub("_", text)[:SESSION_ID_MAX_CHARS].strip("._")
    return cleaned or "unknown"


def audit_log_path(env: Mapping[str, str], home: str, session_id: object) -> str:
    """Return the per-session audit log written by the hooks.

    Args:
        env: Process environment.
        home: The user's home directory.
        session_id: Host session id.

    Returns:
        ``<traces>/audit-<session>.jsonl``.
    """
    return os.path.join(trace_dir(env, home), f"audit-{safe_session(session_id)}.jsonl")


def asked_apps_path(env: Mapping[str, str], home: str, session_id: object) -> str:
    """Return the per-session file listing apps the user already approved (R1).

    Args:
        env: Process environment.
        home: The user's home directory.
        session_id: Host session id.

    Returns:
        ``<state>/asked_apps_<session>.json``.
    """
    return os.path.join(state_dir(env, home), f"asked_apps_{safe_session(session_id)}.json")
