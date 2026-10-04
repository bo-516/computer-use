"""Phase 0 probe hook: record the host's hook payloads (goal.md §12 V3, V6, V9, V13).

Boundary: reads one JSON payload from stdin, appends it to
``$GROK_COMPUTER_PROBE_LOG/hooks.jsonl`` (fallback: ``<cwd>/.probe-logs``) tagged with the label
given on the command line, and prints nothing, so the host proceeds as if no hook ran. Stdlib
only, Python 3.8+, no network. Fails open: any error still exits 0. Typed text in tool inputs is
reduced to length + first 2 characters, as in the product's audit log (AGENTS.md).
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Dict, List, cast

REDACT_KEYS = ("text", "value", "password", "secret", "token")
ENV_KEYS = ("GROK_PLUGIN_ROOT", "GROK_PLUGIN_DATA", "GROK_COMPUTER_PROBE_ID")


def redact(value: object) -> object:
    """Copy of ``value`` with typed text reduced to length + first 2 characters."""
    if isinstance(value, dict):
        out: Dict[str, object] = {}
        for key, item in cast(Dict[object, object], value).items():
            if str(key).lower() in REDACT_KEYS and isinstance(item, str):
                out[str(key)] = {"len": len(item), "head": item[:2]}
            else:
                out[str(key)] = redact(item)
        return out
    if isinstance(value, list):
        return [redact(item) for item in cast(List[object], value)]
    return value


def main() -> int:
    """Record one payload."""
    label = sys.argv[1] if len(sys.argv) > 1 else "hook"
    try:
        raw = sys.stdin.read()
        try:
            payload: object = json.loads(raw)
        except ValueError:
            payload = {"unparsed_len": len(raw)}
        root = os.environ.get("GROK_COMPUTER_PROBE_LOG") or os.path.join(os.getcwd(),
                                                                          ".probe-logs")
        os.makedirs(root, exist_ok=True)
        line = {"ts": time.time(), "label": label, "cwd": os.getcwd(),
                "env": {key: os.environ.get(key) for key in ENV_KEYS},
                "payload": redact(payload)}
        with open(os.path.join(root, "hooks.jsonl"), "a", encoding="utf-8") as sink:
            sink.write(json.dumps(line) + "\n")
    except Exception as exc:  # process edge: a probe hook always fails open
        sys.stderr.write("probe hook failed: %s\n" % exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
