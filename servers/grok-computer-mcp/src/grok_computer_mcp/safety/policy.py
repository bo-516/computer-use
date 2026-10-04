"""Fail-closed safety policy for the facade (goal.md §7.2 L2, AGENTS.md "the facade fails closed").

Boundary: wraps the vendored ``hooklib`` policy so the guard hook and the facade classify apps,
labels and keys identically. ``PolicyHolder`` re-reads the policy files when they change; when any
layer is unreadable or malformed, every tool call is refused with ``POLICY_ERROR`` until it is
fixed (the guard, by contrast, asks). ``SafetyRules`` holds pure checks over a loaded policy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..errors import ErrorCode, FacadeError
from ..hooklib import io as hook_io
from ..hooklib import keys, matching
from ..hooklib import paths as hook_paths
from ..hooklib.defaults import Policy
from ..hooklib.policy import build_policy


@dataclass(frozen=True)
class SafetyRules:
    """Pure R2/R3 checks over one loaded policy."""

    policy: Policy
    risky: re.Pattern[str] | None
    credential: re.Pattern[str] | None

    @classmethod
    def from_policy(cls, policy: Policy) -> SafetyRules:
        """Compile the word lists once.

        Args:
            policy: Effective policy.

        Returns:
            The rules.
        """
        return cls(policy, matching.compile_words(policy.risky_words),
                   matching.compile_words(policy.credential_words))

    def denied_app(self, app: str, window_title: str = "") -> str | None:
        """Deny-list pattern matching the app (and window title), if any (R3)."""
        return matching.denied_app_pattern(app, window_title, self.policy.deny_apps)

    def allowed_app(self, app: str) -> bool:
        """Whether the app is allow-listed (relaxes R1 only)."""
        return matching.allowed_app(app, self.policy.allow_apps)

    def risky_word(self, text: str) -> str | None:
        """R2 word found in a label, if any."""
        return matching.find_word(text, self.risky)

    def credential_word(self, text: str) -> str | None:
        """Credential word found in a label, if any (typing there is R3)."""
        return matching.find_word(text, self.credential)

    def dangerous_chord(self, chord: str) -> str | None:
        """Dangerous-key entry matched by a chord, if any (R3)."""
        return keys.dangerous_chord(chord, self.policy.dangerous_keys)

    def poor_ax(self, app: str) -> bool:
        """Whether the app is on the thin-accessibility list (goal.md §5.7)."""
        return matching.denied_app_pattern(app, "", self.policy.poor_ax_apps) is not None


def policy_paths(home: Path, workspace: Path | None) -> tuple[Path, Path | None]:
    """User and project policy paths.

    Args:
        home: The user's home directory.
        workspace: Project root, if known.

    Returns:
        ``(user_path, project_path_or_None)``.
    """
    project = hook_paths.project_policy_path(str(workspace) if workspace else None)
    return Path(hook_paths.user_policy_path(str(home))), Path(project) if project else None


def _signature(path: Path | None) -> tuple[int, int] | None:
    """``(mtime_ns, size)`` of a file, or None when it does not exist."""
    if path is None:
        return None
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


class PolicyHolder:
    """Loads the layered policy, reloads on change, and refuses everything after a bad load."""

    def __init__(self, home: Path, workspace: Path | None) -> None:
        """Remember where the policy files live; nothing is read until ``rules()``.

        Args:
            home: The user's home directory.
            workspace: Project root, if known.
        """
        self._user, self._project = policy_paths(home, workspace)
        self._loaded_sig: tuple[object, object] | None = None
        self._rules: SafetyRules | None = None
        self._problems: tuple[str, ...] = ()
        self.warnings: tuple[str, ...] = ()

    def _load(self) -> None:
        """Read and layer both files, recording problems instead of raising."""
        layers: list[object] = []
        problems: list[str] = []
        for path in (self._user, self._project):
            if path is None:
                layers.append(None)
                continue
            status, value = hook_io.read_json_file(str(path))
            if status == hook_io.STATUS_ERROR:
                problems.append(str(value))
            layers.append(value if status == hook_io.STATUS_OK else None)
        result = build_policy(layers[0], layers[1], str(self._user), str(self._project or ""))
        self._problems = tuple(problems) + result.problems
        self.warnings = result.warnings
        self._rules = None if self._problems else SafetyRules.from_policy(result.policy)

    def rules(self) -> SafetyRules:
        """Current rules, reloading if a policy file changed.

        Returns:
            The rules.

        Raises:
            FacadeError: ``POLICY_ERROR`` when a policy file is unreadable or malformed (fail
                closed: every action is refused until it is fixed).
        """
        sig = (_signature(self._user), _signature(self._project))
        if sig != self._loaded_sig:
            self._load()
            self._loaded_sig = sig
        if self._rules is None:
            raise FacadeError(ErrorCode.POLICY_ERROR,
                              "Policy failed to load: " + "; ".join(self._problems))
        return self._rules

    @property
    def problems(self) -> tuple[str, ...]:
        """Problems from the last load (empty when healthy)."""
        return self._problems
