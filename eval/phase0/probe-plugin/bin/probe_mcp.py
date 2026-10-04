"""Phase 0 probe MCP server (goal.md §12 V1, V2, V5, V6, V15): stdlib only, Python 3.8+.

Boundary: newline-delimited JSON-RPC over stdio (the MCP stdio transport) and appends to the probe
log; no network. Tools:

* ``show_code(size)``: a JPEG with a code printed in it and no code in the text (V1: does the
  model see MCP images; V2: does the host rescale them). ``canonical`` is 1280x800, ``large`` is
  2400x1500, above the host's re-encode threshold.
* ``structured_code()``: a code only in ``structuredContent`` (V15: is it fed to the model).
* ``env()``: the variables this server was started with (V5: ``${GROK_PLUGIN_*}`` expansion).
* ``echo(text)``: a trivial call whose hook payloads show tool naming (V6) and the subagent
  fields (V3, V13).

Images and codes are generated per run by ``run_probes.py`` into ``assets/`` (fresh codes, so
cross-session memory cannot answer). Every start and call is logged to
``$GROK_COMPUTER_PROBE_LOG/probe-mcp.jsonl`` (fallback: ``<cwd>/.probe-logs``).
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
from typing import Dict, List, Optional, cast

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(os.path.dirname(HERE), "assets")
PROTOCOL_FALLBACK = "2025-06-18"
ENV_KEYS = ("GROK_PLUGIN_ROOT", "GROK_PLUGIN_DATA", "GROK_COMPUTER_PROBE_DATA",
            "GROK_COMPUTER_PROBE_ROOT", "GROK_COMPUTER_PROBE_ID", "GROK_COMPUTER_PROBE_LOG")
TOOLS: List[Dict[str, object]] = [
    {"name": "show_code", "description": "Show an image that contains a short code.",
     "inputSchema": {"type": "object", "properties": {
         "size": {"type": "string", "enum": ["canonical", "large"]}},
         "additionalProperties": False},
     "annotations": {"readOnlyHint": True}},
    {"name": "structured_code", "description": "Return a code in the structured result.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
     "outputSchema": {"type": "object", "properties": {"code": {"type": "string"}},
                      "required": ["code"]},
     "annotations": {"readOnlyHint": True}},
    {"name": "env", "description": "Report how this server was started.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
     "annotations": {"readOnlyHint": True}},
    {"name": "echo", "description": "Echo the text back.",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}},
                     "required": ["text"], "additionalProperties": False},
     "annotations": {"readOnlyHint": True}},
]


def log_path() -> str:
    """Where probe records go (created on demand)."""
    root = os.environ.get("GROK_COMPUTER_PROBE_LOG") or os.path.join(os.getcwd(), ".probe-logs")
    os.makedirs(root, exist_ok=True)
    return os.path.join(root, "probe-mcp.jsonl")


def record(server: str, event: str, **fields: object) -> None:
    """Append one log record; never raises (logging must not break the protocol)."""
    line = {"ts": time.time(), "server": server, "event": event,
            "probe": os.environ.get("GROK_COMPUTER_PROBE_ID", ""), **fields}
    try:
        with open(log_path(), "a", encoding="utf-8") as sink:
            sink.write(json.dumps(line) + "\n")
    except OSError as exc:
        sys.stderr.write("probe log failed: %s\n" % exc)


def assets() -> Dict[str, Dict[str, object]]:
    """``codes.json`` written by run_probes.py ({} when missing)."""
    try:
        with open(os.path.join(ASSETS, "codes.json"), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return cast(Dict[str, Dict[str, object]], data) if isinstance(data, dict) else {}


def call(server: str, name: str, args: Dict[str, object]) -> Dict[str, object]:
    """Run one tool; return the MCP ``CallToolResult``."""
    codes = assets()
    if name == "show_code":
        size = str(args.get("size") or "canonical")
        item = codes.get(size)
        if not item:
            return {"content": [{"type": "text", "text": "assets missing"}], "isError": True}
        with open(os.path.join(ASSETS, str(item["file"])), "rb") as handle:
            data = base64.b64encode(handle.read()).decode()
        record(server, "show_code", size=size, width=item["width"], height=item["height"],
               bytes=len(data) * 3 // 4)
        return {"content": [{"type": "text", "text": "Image attached. Read the code in it."},
                            {"type": "image", "data": data, "mimeType": "image/jpeg"}]}
    if name == "structured_code":
        code = str(codes.get("structured", {}).get("code", ""))
        record(server, "structured_code")
        return {"content": [{"type": "text", "text": "The code is in the structured result."}],
                "structuredContent": {"code": code}}
    if name == "env":
        seen = {key: os.environ.get(key) for key in ENV_KEYS}
        record(server, "env", env=seen, cwd=os.getcwd(), argv=sys.argv)
        return {"content": [{"type": "text", "text": json.dumps(seen)}]}
    text = str(args.get("text", ""))
    record(server, "echo", text_len=len(text))
    return {"content": [{"type": "text", "text": text}]}


def respond(request: Dict[str, object], server: str) -> Optional[Dict[str, object]]:
    """The JSON-RPC response to one message (None for notifications)."""
    method, rid = request.get("method"), request.get("id")
    raw = request.get("params")
    params = cast(Dict[str, object], raw) if isinstance(raw, dict) else {}
    if rid is None:
        return None
    if method == "initialize":
        version = params.get("protocolVersion")
        result: Dict[str, object] = {
            "protocolVersion": version or PROTOCOL_FALLBACK, "capabilities": {"tools": {}},
            "serverInfo": {"name": "computer-use-probe-" + server, "version": "0.1.0"}}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        arguments = params.get("arguments")
        result = call(server, str(params.get("name")),
                      cast(Dict[str, object], arguments) if isinstance(arguments, dict) else {})
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": rid,
                "error": {"code": -32601, "message": "method not found: %s" % method}}
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def main() -> int:
    """Serve until stdin closes."""
    server = sys.argv[1] if len(sys.argv) > 1 else "probe"
    record(server, "start", env={key: os.environ.get(key) for key in ENV_KEYS},
           cwd=os.getcwd(), argv=sys.argv)
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
        except ValueError:
            continue
        reply = respond(cast(Dict[str, object], request), server) \
            if isinstance(request, dict) else None
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
