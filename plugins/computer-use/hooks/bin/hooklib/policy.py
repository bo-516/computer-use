"""Computer-use safety policy: layering user and project files over the defaults.

Boundary: no I/O. Callers decode the JSON layers (``hooklib.io``) and hand the values in, so the
guard, the audit hook and the facade's fail-closed checks (``grok_computer_mcp.safety.policy``)
share one definition of the policy. Matching helpers live in ``hooklib.matching`` and
``hooklib.keys``; the record and its defaults live in ``hooklib.defaults``.

Layering only tightens (goal.md §7.3, B.3; AGENTS.md):

* every layer may append to the lists in ``APPEND_ONLY_LISTS``; nothing removes a default;
* ``require_subagent``, ``state_max_age_s``, ``allow_isolated_browser`` and ``subagent_types``
  are honoured only from the user's own ``~/.grok-computer/policy.json``; a project file that
  sets them is ignored with a warning;
* ``allow_apps`` only relaxes the R1 first-touch ask and never overrides an R3 denial.

Malformed values are reported as *problems*. Consumers decide what a problem means: the guard
answers ``ask`` (it cannot tell what the user intended) and the facade denies every action.
"""

from __future__ import annotations

from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple, cast

from .defaults import DEFAULT_POLICY, Policy

POLICY_SCHEMA_VERSION = 1

# Lists that any layer may extend. allow_apps relaxes only R1 asks; the rest tighten.
APPEND_ONLY_LISTS = (
    "allow_apps",
    "deny_apps",
    "risky_words",
    "credential_words",
    "dangerous_keys",
    "poor_ax_apps",
)
# Settings only ~/.grok-computer/policy.json may change (goal.md §7.4, B.3 "只认用户目录").
USER_ONLY_SCALARS = ("require_subagent", "state_max_age_s", "allow_isolated_browser")
USER_ONLY_LISTS = ("subagent_types",)
# Keys a layer may carry for documentation; skipped silently.
IGNORED_KEYS = ("$schema", "_comment", "comment", "description", "version")

# Bounds for the user-tunable state freshness window. goal.md §7.4 step 3 uses 30 s; the cap
# keeps a typo from making the guard trust a minutes-old screen.
STATE_MAX_AGE_MIN_S = 1.0
STATE_MAX_AGE_MAX_S = 300.0


class LayerResult(NamedTuple):
    """Outcome of applying one policy layer: the new policy plus problems and warnings."""

    policy: Policy
    problems: Tuple[str, ...]
    warnings: Tuple[str, ...]


def str_list(value: object) -> Optional[List[str]]:
    """Return ``value`` as a list of stripped, non-empty strings, or None if it is not one.

    Args:
        value: A decoded JSON value.

    Returns:
        The strings (blank entries dropped), or None when ``value`` is not a list of strings.
    """
    if not isinstance(value, list):
        return None
    out: List[str] = []
    for item in cast(List[object], value):
        if not isinstance(item, str):
            return None
        if item.strip():
            out.append(item.strip())
    return out


def dedupe(items: Sequence[str]) -> Tuple[str, ...]:
    """Drop case-insensitive duplicates, keeping the first spelling and the order.

    Args:
        items: Strings to deduplicate.

    Returns:
        A tuple with one entry per distinct casefolded value.
    """
    seen: Dict[str, None] = {}
    out: List[str] = []
    for item in items:
        key = " ".join(item.casefold().split())
        if key and key not in seen:
            seen[key] = None
            out.append(item)
    return tuple(out)


def _user_scalar(key: str, value: object) -> Tuple[Optional[object], Optional[str]]:
    """Validate one user-only scalar.

    Args:
        key: One of ``USER_ONLY_SCALARS``.
        value: The decoded JSON value.

    Returns:
        ``(value, None)`` when valid, else ``(None, problem)``.
    """
    if key == "state_max_age_s":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None, "'state_max_age_s' must be a number"
        if not STATE_MAX_AGE_MIN_S <= float(value) <= STATE_MAX_AGE_MAX_S:
            return None, (f"'state_max_age_s' must be between {STATE_MAX_AGE_MIN_S:g} and "
                          f"{STATE_MAX_AGE_MAX_S:g}")
        return float(value), None
    if not isinstance(value, bool):
        return None, f"'{key}' must be true or false"
    return value, None


def apply_layer(base: Policy, layer: object, source: str, user_layer: bool) -> LayerResult:
    """Apply one decoded policy file on top of ``base``; only tightening is possible.

    Args:
        base: Policy accumulated so far.
        layer: Decoded JSON content of the file (must be an object).
        source: Path or label used in messages.
        user_layer: True only for ``~/.grok-computer/policy.json``.

    Returns:
        The new policy, problems (malformed values; consumers fail closed) and warnings (keys
        that were ignored).
    """
    if not isinstance(layer, dict):
        return LayerResult(base, (f"{source}: policy must be a JSON object",), ())
    problems: List[str] = []
    warnings: List[str] = []
    changes: Dict[str, object] = {}
    current = base._asdict()
    for raw_key, value in cast(Dict[object, object], layer).items():
        key = str(raw_key)
        if key in IGNORED_KEYS:
            continue
        user_only = key in USER_ONLY_LISTS or key in USER_ONLY_SCALARS
        if user_only and not user_layer:
            warnings.append(f"{source}: '{key}' is only read from the user policy; ignored")
        elif key in APPEND_ONLY_LISTS or key in USER_ONLY_LISTS:
            items = str_list(value)
            if items is None:
                problems.append(f"{source}: '{key}' must be a list of strings")
                continue
            existing = cast(Tuple[str, ...], changes.get(key, current[key]))
            changes[key] = dedupe(list(existing) + items)
        elif key in USER_ONLY_SCALARS:
            checked, problem = _user_scalar(key, value)
            if problem is not None:
                problems.append(f"{source}: {problem}")
            else:
                changes[key] = checked
        else:
            warnings.append(f"{source}: unknown key '{key}' ignored")
    return LayerResult(base._replace(**changes), tuple(problems), tuple(warnings))


def build_policy(user_layer: object, project_layer: object, user_source: str,
                 project_source: str) -> LayerResult:
    """Layer the user and project files over ``DEFAULT_POLICY``.

    Args:
        user_layer: Decoded ``~/.grok-computer/policy.json``, or None when absent.
        project_layer: Decoded ``<workspace>/.grok/computer-policy.json``, or None when absent.
        user_source: Path of the user file, for messages.
        project_source: Path of the project file, for messages.

    Returns:
        The effective policy with all problems and warnings from both layers.
    """
    policy = DEFAULT_POLICY
    problems: List[str] = []
    warnings: List[str] = []
    for layer, source, is_user in ((user_layer, user_source, True),
                                   (project_layer, project_source, False)):
        if layer is None:
            continue
        result = apply_layer(policy, layer, source, is_user)
        policy = result.policy
        problems.extend(result.problems)
        warnings.extend(result.warnings)
    return LayerResult(policy, tuple(problems), tuple(warnings))
