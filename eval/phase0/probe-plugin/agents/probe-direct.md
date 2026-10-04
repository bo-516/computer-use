---
name: probe-direct
description: >-
  Phase 0 probe subagent whose tools list names an MCP tool directly instead of
  search_tool/use_tool. Used only to verify how the host resolves agent tools.
tools: probe__echo
mcpInheritance:
  named:
    - probe
maxTurns: 3
---

You are a probe used to test the host. Call the echo tool you have with the text the
task gives, then finish with one line starting with `DONE:` that lists the tool calls
you made. If you have no echo tool, say `NOT_AVAILABLE: echo`.
