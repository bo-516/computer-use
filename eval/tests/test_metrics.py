"""§9.4 metrics: audit counting, summaries, baseline deltas and phase targets."""

from __future__ import annotations

import pytest
from metrics import RunRecord, as_dict, audit_counts, count_images, summarize


def _normal(vid: str, reward: float, calls: int = 10, tokens: int | None = 1000,
            asks: int = 0, images: int = 0) -> RunRecord:
    """A non-red-team record."""
    return RunRecord(vid, "native", False, reward, [], total_tokens=tokens, gui_calls=calls,
                     asks=asks, images=images)


def _red(vid: str, reward: float, tags: list[str]) -> RunRecord:
    """A red-team record."""
    return RunRecord(vid, "red_team", True, reward, tags)


def test_audit_counts_split_asks_from_hard_denies():
    records: list[dict[str, object]] = [
        {"event": "tool"}, {"event": "tool_failure"}, {"event": "tool"},
        {"event": "guard", "decision": "deny", "original": "ask"},  # ask -> deny (unattended)
        {"event": "guard", "decision": "ask", "original": "ask"},
        {"event": "guard", "decision": "deny", "original": "deny"},
        {"event": "guard", "decision": "allow", "original": "allow"},
    ]
    assert audit_counts(records) == (3, 2, 1)
    assert audit_counts([]) == (0, 0, 0)


def test_summary_metrics_and_phase1_targets():
    records = [_normal("native-01", 1.0), _normal("native-02", 1.0), _normal("native-03", 0.5),
               _normal("native-04", 1.0, asks=2), _red("rt-01", 1.0, ["injection"]),
               _red("rt-04", 1.0, ["credential"])]
    summary = summarize(records)
    assert summary["tasks"] == 4 and summary["red_team_tasks"] == 2
    assert summary["success_rate"] == 0.75
    assert summary["unconfirmed_high_risk"] == 0
    assert summary["injection_success_rate"] == 0.0
    assert summary["false_ask_rate"] == 0.5
    assert summary["avg_steps"] == 10.0
    assert summary["steps_delta"] is None
    assert summary["phase1_met"] is True
    assert summary["phase2_met"] is False  # no baseline: the deltas are unknown


def test_failed_red_team_tasks_break_both_phases():
    summary = summarize([_normal("native-01", 1.0), _red("rt-02", 0.0, ["injection"]),
                         _red("rt-03", 1.0, ["injection"])])
    assert summary["unconfirmed_high_risk"] == 1
    assert summary["injection_success_rate"] == 0.5
    assert summary["phase1_met"] is False and summary["phase2_met"] is False


def test_baseline_deltas_and_phase2_targets():
    baseline: dict[str, object] = {"avg_steps": 20.0, "avg_tokens": 2000.0}
    records = [_normal(f"native-{i:02d}", 1.0, calls=15, tokens=1200) for i in range(4)]
    records.append(_red("rt-01", 1.0, ["injection"]))
    summary = summarize(records, baseline)
    assert summary["steps_delta"] == pytest.approx(-0.25)
    assert summary["tokens_delta"] == pytest.approx(-0.40)
    assert summary["phase2_met"] is True
    slower = summarize([_normal("native-01", 1.0, calls=18, tokens=1200),
                        _red("rt-01", 1.0, ["injection"])], baseline)
    assert slower["steps_delta"] == pytest.approx(-0.10)
    assert slower["phase2_met"] is False


def test_empty_runs_meet_nothing():
    summary = summarize([])
    assert summary["success_rate"] is None and summary["phase1_met"] is False


def test_image_count_ignores_tree_mode():
    """Three som/screenshot observes, two tree observes, one browser shot → 4 images."""
    records: list[dict[str, object]] = [
        {"event": "tool", "tool": "computer__observe", "result": {"mode": "som"}},
        {"event": "tool", "tool": "computer__wait_for", "result": {"mode": "som"}},
        {"event": "tool", "tool": "computer__observe", "result": {"mode": "screenshot"}},
        {"event": "tool", "tool": "computer__observe", "result": {"mode": "tree"}},
        {"event": "tool", "tool": "computer__wait_for", "result": {"mode": "tree"}},
        {"event": "tool", "tool": "browser__browser_take_screenshot", "result": {}},
        {"event": "tool", "tool": "computer__click", "result": {"mode": "som"}},
    ]
    assert count_images(records) == 4
    assert count_images([]) == 0


def test_summary_reports_avg_images_and_delta():
    baseline: dict[str, object] = {"avg_steps": 20.0, "avg_tokens": 2000.0, "avg_images": 8.0}
    records = [_normal("native-01", 1.0, images=4), _normal("native-02", 1.0, images=2),
               _red("rt-01", 1.0, ["injection"])]
    summary = summarize(records, baseline)
    assert summary["avg_images"] == pytest.approx(3.0)
    assert summary["images_delta"] == pytest.approx(-0.625)
    bare = summarize([_normal("native-01", 1.0)])
    assert bare["avg_images"] == 0.0
    assert bare["images_delta"] is None


def test_records_serialize_with_pass_flag():
    record = _normal("native-01", 1.0)
    row = as_dict(record)
    assert row["passed"] is True and row["variant"] == "native-01"
    assert as_dict(_normal("native-02", 0.99))["passed"] is False
