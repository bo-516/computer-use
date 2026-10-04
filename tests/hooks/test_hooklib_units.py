"""Unit tests for the pure hooklib modules: keys, matching, policy layering, paths."""

from __future__ import annotations

import os
from typing import Dict

import pytest

from hooklib import keys, matching, paths
from hooklib.defaults import DEFAULT_POLICY
from hooklib.policy import apply_layer, build_policy


@pytest.mark.parametrize(
    "chord,expected",
    [
        ("cmd+q", ("cmd+q",)),
        ("Command + Q", ("cmd+q",)),
        ("shift+cmd+Q", ("shift+cmd+q",)),
        ("Control+Alt+Del", ("ctrl+alt+delete",)),
        ("Meta+W", ("cmd+w", "win+w")),
        ("ctrl++", ("ctrl+plus",)),
        ("Escape", ("esc",)),
        ("F12", ("f12",)),
        ("shift", ("shift",)),
    ],
)
def test_canonical_chords(chord: str, expected: object) -> None:
    assert keys.canonical_chords(chord) == expected


@pytest.mark.parametrize("chord", ["", "cmd+", "a+b", "cmd+notakey", "f25"])
def test_unparseable_chords(chord: str) -> None:
    assert keys.canonical_chords(chord) == ()


def test_cmd_and_win_stay_distinct() -> None:
    assert keys.dangerous_chord("cmd+l", DEFAULT_POLICY.dangerous_keys) is None
    assert keys.dangerous_chord("win+l", DEFAULT_POLICY.dangerous_keys) == "win+l"
    assert keys.dangerous_chord("meta+l", DEFAULT_POLICY.dangerous_keys) == "win+l"


def test_enter_and_close_detection() -> None:
    assert keys.presses_enter("Return") and keys.presses_enter("cmd+enter")
    assert not keys.presses_enter("tab")
    assert keys.closes_window("Meta+W") and keys.closes_window("ctrl+f4")


def test_chords_of_shapes() -> None:
    assert keys.chords_of("cmd+s") == ["cmd+s"]
    assert keys.chords_of(["cmd+a", "delete"]) == ["cmd+a", "delete"]
    assert keys.chords_of([]) is None and keys.chords_of(3) is None and keys.chords_of(" ") is None


@pytest.mark.parametrize(
    "label,word",
    [
        ("Delete account", "delete"),
        ("SEND", "send"),
        ("Place  order", "place order"),
        ("delete_button", "delete"),
        ("永久删除", "删除"),
        ("Postal code", None),
        ("Display name", None),
        ("Deleted items", None),
    ],
)
def test_risky_words_match_whole_latin_words_and_cjk_substrings(label: str, word: object) -> None:
    pattern = matching.compile_words(DEFAULT_POLICY.risky_words)
    found = matching.find_word(label, pattern)
    if word is None:
        assert found is None
    else:
        assert found is not None and matching.normalize(found) == word


def test_deny_patterns_are_substrings_and_allow_patterns_whole_names() -> None:
    assert matching.denied_app_pattern("1Password 8", "", DEFAULT_POLICY.deny_apps) == "1Password"
    assert (
        matching.denied_app_pattern(
            "System Settings", "Privacy & Security", DEFAULT_POLICY.deny_apps
        )
        == "System Settings > Privacy"
    )
    assert (
        matching.denied_app_pattern("System Settings", "Appearance", DEFAULT_POLICY.deny_apps)
        is None
    )
    assert matching.allowed_app("myapp", ["MyApp"])
    assert not matching.allowed_app("Xcode", ["Code"])
    assert matching.denied_app_pattern("", "", DEFAULT_POLICY.deny_apps) is None


def test_layering_appends_and_ignores_user_only_keys_from_project() -> None:
    result = build_policy(
        {"allow_apps": ["MyApp"], "state_max_age_s": 60, "risky_words": ["archive"]},
        {"allow_apps": ["MyApp Dev", "myapp"], "require_subagent": False, "deny_apps": ["Prod"]},
        "user.json",
        "project.json",
    )
    assert result.problems == ()
    assert result.policy.allow_apps == ("MyApp", "MyApp Dev")
    assert result.policy.state_max_age_s == 60.0
    assert result.policy.require_subagent is True
    assert "archive" in result.policy.risky_words
    assert result.policy.deny_apps[-1] == "Prod"
    assert len(result.policy.deny_apps) == len(DEFAULT_POLICY.deny_apps) + 1
    assert any("require_subagent" in w for w in result.warnings)


@pytest.mark.parametrize(
    "layer",
    [
        [1, 2],
        {"deny_apps": "x"},
        {"deny_apps": [1]},
        {"state_max_age_s": 0},
        {"state_max_age_s": True},
        {"require_subagent": "no"},
    ],
)
def test_malformed_layers_are_problems(layer: object) -> None:
    result = apply_layer(DEFAULT_POLICY, layer, "user.json", True)
    assert result.problems
    assert result.policy.deny_apps == DEFAULT_POLICY.deny_apps


def test_unknown_keys_warn_and_comments_are_ignored() -> None:
    result = apply_layer(DEFAULT_POLICY, {"_comment": "x", "colour": "red"}, "p.json", False)
    assert result.problems == ()
    assert result.warnings == ("p.json: unknown key 'colour' ignored",)


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        ("", None),
        ("relative/dir", None),
        ("${GROK_PLUGIN_DATA}", None),
        ("$GROK_PLUGIN_DATA/x", None),
        (os.sep, None),
    ],
)
def test_unusable_env_dirs_fall_back(value: object, expected: object) -> None:
    assert paths.usable_dir(value if isinstance(value, str) else None) is expected


def test_state_dir_resolution_order(tmp_path: object) -> None:
    home = "/home/u"
    env: Dict[str, str] = {}
    assert paths.state_dir(env, home) == os.path.join(home, ".grok-computer", "state")
    env["GROK_PLUGIN_DATA"] = "/data/plugin"
    assert paths.state_dir(env, home) == os.path.join("/data/plugin", "state")
    assert paths.state_dir_candidates(env, home) == [
        os.path.join("/data/plugin", "state"),
        os.path.join(home, ".grok-computer", "state"),
    ]
    env["GROK_COMPUTER_STATE_DIR"] = "/explicit/state"
    assert paths.state_dir(env, home) == "/explicit/state"
    env["GROK_PLUGIN_DATA"] = "${GROK_PLUGIN_DATA}"
    del env["GROK_COMPUTER_STATE_DIR"]
    assert paths.state_dir(env, home) == os.path.join(home, ".grok-computer", "state")


@pytest.mark.parametrize(
    "session,expected",
    [
        ("abc-123", "abc-123"),
        ("../../etc/passwd", "etc_passwd"),
        (None, "unknown"),
        ("", "unknown"),
        ("a" * 100, "a" * 64),
    ],
)
def test_session_ids_become_safe_file_names(session: object, expected: str) -> None:
    assert paths.safe_session(session) == expected
