"""Phase 0 verdicts: turn probe evidence into a V1-V16 status table (goal.md §12).

Boundary: pure verdict functions over ``evidence.Evidence``, plus ``write_report`` which writes
``report.md`` and ``report.json`` into the run directory. Statuses: PASS (assumption holds), FAIL
(it does not: apply the §12 fallback), UNKNOWN (the probe did not produce evidence), MANUAL (no
headless probe can settle it; the procedure is listed).

    uv run python eval/phase0/analyze.py /tmp/phase0
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

sys.path.insert(0, str(Path(__file__).resolve().parent))

import evidence
import probes
from evidence import Evidence, field, output, run_of

TOOL_NAME = ("toolName", "tool_name")
SUBAGENT = ("subagentType", "subagent_type")
# goal.md §5.3: the facade's canonical screenshot size the host must not rescale.
CANONICAL = probes.IMAGE_SIZES["canonical"]
# Subagent maxTurns in agents/probe.md; more echo calls than this means it was ignored.
PROBE_MAX_TURNS = 3


@dataclass(frozen=True)
class Verdict:
    """One verification item's outcome."""

    item: str
    status: str
    detail: str


def _hooks(ev: Evidence, label: str, run_prefix: str) -> list[evidence.Json]:
    """Payloads of one hook label from runs starting with ``run_prefix``."""
    return [cast(evidence.Json, r["payload"]) for r in ev.hooks if r.get("label") == label
            and run_of(r).startswith(run_prefix) and isinstance(r.get("payload"), dict)]


def _code(ev: Evidence, key: str) -> str:
    """A generated code ("" when codes are missing)."""
    return str(ev.codes.get(key, {}).get("code", ""))


def v1(ev: Evidence) -> list[Verdict]:
    """V1: models read the code that exists only in the MCP image."""
    code = _code(ev, "canonical")
    out: list[Verdict] = []
    for run in sorted(r for r in ev.results if r.startswith("image-canonical")):
        text = output(ev, run)
        status = "PASS" if code and code in text else (
            "FAIL" if probes.NO_IMAGE in text else "UNKNOWN")
        out.append(Verdict("V1", status, f"{run}: expected {code}, exit "
                                         f"{ev.results[run].get('exit')}"))
    return out or [Verdict("V1", "UNKNOWN", "image-canonical did not run")]


def v2(ev: Evidence) -> Verdict:
    """V2: the canonical image reaches the session at its sent size."""
    seen = [s for run, sizes in ev.trace_images.items() if run.startswith("image-canonical")
            for s in sizes]
    large = sorted({s for run, sizes in ev.trace_images.items() if run.startswith("image-large")
                    for s in sizes})
    note = f"canonical run images {sorted(set(seen))}; large run images {large}"
    if CANONICAL in seen:
        return Verdict("V2", "PASS", note)
    return Verdict("V2", "FAIL" if seen else "UNKNOWN", note)


def v3(ev: Evidence) -> Verdict:
    """V3: PreToolUse inside a subagent carries subagentType."""
    calls = [p for p in _hooks(ev, "pre-all", "delegate")
             if "echo" in str(field(p, *TOOL_NAME))]
    typed = sorted({str(field(p, *SUBAGENT)) for p in calls if field(p, *SUBAGENT)})
    if typed:
        return Verdict("V3", "PASS", f"subagentType values: {typed}")
    return Verdict("V3", "FAIL" if calls else "UNKNOWN", f"{len(calls)} echo calls seen")


def v4(ev: Evidence) -> Verdict:
    """V4: plugin agent fields take effect (scope, maxTurns; direct MCP tools noted)."""
    scope_calls = [p for p in _hooks(ev, "pre-all", "delegate-scope")
                   if "probe2" in str(field(p, *TOOL_NAME)) and field(p, *SUBAGENT)]
    turn_calls = [p for p in _hooks(ev, "pre-all", "delegate-turns")
                  if "echo" in str(field(p, *TOOL_NAME)) and field(p, *SUBAGENT)]
    direct = [p for p in _hooks(ev, "pre-all", "delegate-direct")
              if "echo" in str(field(p, *TOOL_NAME)) and field(p, *SUBAGENT)]
    ran = all(f"delegate-{k}" in ev.results for k in ("scope", "turns"))
    detail = (f"probe2 calls by subagent: {len(scope_calls)}; echo calls under maxTurns "
              f"{PROBE_MAX_TURNS}: {len(turn_calls)}; direct MCP tool in tools: "
              f"{'works' if direct else 'no call seen'}")
    if not ran or not turn_calls:
        return Verdict("V4", "UNKNOWN", detail)
    ok = not scope_calls and len(turn_calls) <= PROBE_MAX_TURNS
    return Verdict("V4", "PASS" if ok else "FAIL", detail)


def v5(ev: Evidence) -> Verdict:
    """V5: ``${GROK_PLUGIN_DATA}``/``${GROK_PLUGIN_ROOT}`` are expanded in .mcp.json."""
    starts = [r for r in ev.server if r.get("event") == "start" and r.get("server") == "probe"]
    values = {str(field(r.get("env"), "GROK_COMPUTER_PROBE_DATA")) for r in starts}
    rootvar = any(r.get("server") == "probe_rootvar" for r in ev.server)
    detail = f"env values {sorted(values)}; ${{GROK_PLUGIN_ROOT}} in args: " + (
        "expanded (server started)" if rootvar else "server never started")
    if not starts:
        return Verdict("V5", "UNKNOWN", detail)
    ok = all(v.startswith("/") and "${" not in v for v in values)
    return Verdict("V5", "PASS" if ok else "FAIL", detail)


def v6(ev: Evidence) -> Verdict:
    """V6: tool names in hook payloads are ``server__tool`` and matchers see them."""
    names = sorted({str(field(p, *TOOL_NAME)) for p in _hooks(ev, "pre-all", "")
                    if "probe" in str(field(p, *TOOL_NAME))})
    matched = bool(_hooks(ev, "pre-matched", ""))
    detail = f"names {names}; matcher ^probe__ fired: {matched}"
    if not names:
        return Verdict("V6", "UNKNOWN", detail)
    ok = matched and all(n.startswith("probe") and "__" in n for n in names)
    return Verdict("V6", "PASS" if ok else "FAIL", detail)


def v_simple(item: str, run_prefix: str, needle: str, bad: str) -> Callable[[Evidence], Verdict]:
    """A verdict on whether a run's output contains ``needle`` (FAIL on ``bad``)."""
    def judge(ev: Evidence) -> Verdict:
        texts = [output(ev, r) for r in ev.results if r.startswith(run_prefix)]
        if any(needle and needle in t for t in texts):
            return Verdict(item, "PASS", f"{run_prefix}: found {needle!r}")
        status = "FAIL" if any(bad in t for t in texts) else "UNKNOWN"
        return Verdict(item, status, f"{run_prefix}: {len(texts)} runs, {needle!r} not found")
    return judge


def v11(ev: Evidence) -> list[Verdict]:
    """V11: the probe server starts and answers under each sandbox profile."""
    out: list[Verdict] = []
    for run in sorted(r for r in ev.results if r.startswith("env#")):
        answered = any(r.get("event") == "env" and r.get("probe") == run for r in ev.server)
        seen = "logged" if answered else "missing"
        out.append(Verdict("V11", "PASS" if answered else "FAIL", f"{run}: env call {seen}"))
    return out or [Verdict("V11", "UNKNOWN", "no --sandbox profiles were probed")]


def v13(ev: Evidence) -> Verdict:
    """V13: SubagentStop carries lastAssistantMessage/stopHookActive; matchers see the type."""
    stops = _hooks(ev, "subagent-stop-all", "delegate")
    keys = all(field(p, "lastAssistantMessage", "last_assistant_message") is not None
               and field(p, "stopHookActive", "stop_hook_active") is not None for p in stops)
    matched = bool(_hooks(ev, "subagent-stop-matched", "delegate"))
    detail = f"{len(stops)} stops; fields present: {keys}; matcher probe$ fired: {matched}"
    if not stops:
        return Verdict("V13", "UNKNOWN", detail)
    return Verdict("V13", "PASS" if keys and matched else "FAIL", detail)


def analyze(ev: Evidence) -> list[Verdict]:
    """Every verdict, in V order."""
    verdicts = [*v1(ev), v2(ev), v3(ev), v4(ev), v5(ev), v6(ev),
                v_simple("V10", "background", "DONE", "NOT_AVAILABLE")(ev), *v11(ev),
                v_simple("V12", "command-args", f"{probes.ARGS_MARK}hello probe world",
                         f"{probes.ARGS_MARK}$ARGUMENTS")(ev), v13(ev),
                v_simple("V15", "structured", _code(ev, "structured").lower(),
                         probes.NO_CODE)(ev)]
    verdicts += [Verdict(k, "MANUAL", text) for k, text in probes.MANUAL.items()]
    if _hooks(ev, "pre-compact", ""):
        verdicts.append(Verdict("V9", "INFO", "pre-compact hook payloads were recorded"))
    return sorted(verdicts, key=lambda v: int(v.item[1:]))


def write_report(out: Path) -> str:
    """Analyze a run directory and write ``report.md``/``report.json``; return the Markdown."""
    verdicts = analyze(evidence.load(out))
    lines = ["# Phase 0 probe report", "", "| item | status | evidence |", "|---|---|---|"]
    lines += [f"| {v.item} | {v.status} | {v.detail.replace('|', '/')} |" for v in verdicts]
    text = "\n".join(lines) + "\n"
    (out / "report.md").write_text(text, encoding="utf-8")
    (out / "report.json").write_text(json.dumps([asdict(v) for v in verdicts], indent=2),
                                     encoding="utf-8")
    return text


if __name__ == "__main__":
    print(write_report(Path(sys.argv[1])))
