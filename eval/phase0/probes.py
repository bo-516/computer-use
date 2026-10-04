"""Phase 0 probe definitions (goal.md §12): what each headless grok run asks and verifies.

Boundary: pure data. ``run_probes.py`` runs each probe as one ``grok -p`` session in a throwaway
workspace with the probe plugin installed at project scope; ``analyze.py`` turns the probe logs
into verdicts. Prompts are model-facing, so they are English; each names the exact reply that
the analyzer looks for.
"""

from __future__ import annotations

from dataclasses import dataclass

PROBE_PLUGIN = "computer-use-probe"
IMAGE_SIZES = {"canonical": (1280, 800), "large": (2400, 1500)}
# Fallback refusals the prompts ask for, so a missing capability is distinguishable from a
# wrong answer.
NO_IMAGE = "NO_IMAGE"
NO_CODE = "NO_CODE"
ARGS_MARK = "ARGS<<"


@dataclass(frozen=True)
class Probe:
    """One headless run."""

    id: str
    verifies: tuple[str, ...]
    prompt: str
    per_model: bool = False
    per_sandbox: bool = False
    max_turns: int = 8


PROBES: tuple[Probe, ...] = (
    Probe("image-canonical", ("V1", "V2"),
          "Use the show_code tool of the probe MCP server with size \"canonical\". Reply with "
          f"only the code printed in the image, or {NO_IMAGE} if you cannot see an image.",
          per_model=True),
    Probe("image-large", ("V2",),
          "Use the show_code tool of the probe MCP server with size \"large\". Reply with only "
          f"the code printed in the image, or {NO_IMAGE} if you cannot see an image.",
          per_model=True),
    # Lowercase: grok's JSON output may echo tool results, so only the model's own rewrite of
    # the code counts as evidence that it saw the structured result.
    Probe("structured", ("V15",),
          "Use the structured_code tool of the probe MCP server. Reply with only the value of "
          f"\"code\" in its result converted to lowercase, or {NO_CODE} if you cannot see one.",
          per_model=True),
    Probe("env", ("V5", "V11"),
          "Use the env tool of the probe MCP server once, then reply DONE.", per_sandbox=True),
    Probe("delegate", ("V3", "V4", "V6", "V13"),
          f"Delegate this task to the {PROBE_PLUGIN}:probe subagent and wait for its report: "
          "\"Call the echo tool of the probe MCP server once with the text ping.\" Then reply "
          "with the subagent's DONE line."),
    Probe("delegate-scope", ("V4",),
          f"Delegate this task to the {PROBE_PLUGIN}:probe subagent and wait for its report: "
          "\"Call the echo tool of the probe2 MCP server with the text x. If you do not have "
          "it, say NOT_AVAILABLE.\" Then reply with the subagent's report."),
    Probe("delegate-turns", ("V4",),
          f"Delegate this task to the {PROBE_PLUGIN}:probe subagent and wait for its report: "
          "\"Call the echo tool of the probe MCP server six times, one call per turn, with the "
          "texts 1 to 6.\" Then reply with the subagent's report."),
    Probe("delegate-direct", ("V4",),
          f"Delegate this task to the {PROBE_PLUGIN}:probe-direct subagent and wait for its "
          "report: \"Call echo with the text direct.\" Then reply with its DONE line."),
    Probe("background", ("V10",),
          f"Start the {PROBE_PLUGIN}:probe subagent in the background with the task \"Call the "
          "echo tool of the probe MCP server once with the text bg.\" Wait until it finishes, "
          "then reply with its DONE line."),
    Probe("command-args", ("V12",), f"/{PROBE_PLUGIN}:probe-args hello probe world"),
    Probe("command-args-short", ("V12",), "/probe-args hello probe world"),
)

# Items no headless probe can settle; analyze.py lists them with the manual procedure.
MANUAL: dict[str, str] = {
    "V7": "On macOS, start a session with the computer-use plugin and run "
          "`grok-computer-mcp doctor`; record whether Accessibility and Screen Recording are "
          "granted when the driver is launched by grok (direct) and by the daemon.",
    "V8": "Run eval/grounding/run_benchmark.py on the collected set (goal.md §9.2).",
    "V9": "Run a 50+ step GUI task; check hooks.jsonl for pre-compact/post-compact records and "
          "whether old screenshots are dropped (grok export of the session).",
    "V14": "With Cua Driver running, observe a window that is not at the screen origin and "
           "compare element frames with the window bounds (`grok-computer-mcp doctor`, then "
           "set GROK_COMPUTER_CUA_FRAME_SPACE).",
    "V16": "With Cua Driver installed, call `cua-driver call get_window_state` on a test window "
           "and compare field names and refusal codes with the V16 notes in "
           "servers/grok-computer-mcp/src/grok_computer_mcp/backend/cua_parse.py.",
}
