# grok-computer-mcp

The desktop facade of the Grok Build
[`computer-use` plugin](https://github.com/bo-516/computer-use): an MCP server (stdio) that gives
the plugin's `computer` subagent nine small GUI tools on top of
[Cua Driver](https://github.com/trycua/cua), with one coordinate space, canonical screenshots,
compact accessibility observations and fail-closed safety rules.

| Tool | Purpose |
|---|---|
| `observe` | Accessibility-tree observation with stable refs; Set-of-Mark or plain screenshot when needed |
| `click`, `type_text`, `press_keys`, `scroll`, `drag` | Actions on a `ref`, a Set-of-Mark `mark` or an image `point`, always bound to an `observation_id` |
| `apps` | List, launch and focus apps; list windows |
| `wait_for` | Wait until text or a role appears (or disappears) |
| `locate` | Grounding fallback: natural-language target to image coordinates (UI-TARS or Grok) |

Every coordinate the model sees or sends is a pixel of the current observation image (long edge
1280 px, JPEG q80), so host-side image re-encoding never shifts clicks. Actions on a screen that
changed since the observation are refused (`STALE_OBSERVATION`). Deny-listed apps, secure fields
and dangerous key chords are refused even when the plugin's hooks are not running, and a policy
file that fails to load denies every action.

## Run

The plugin's `.mcp.json` starts it with `uvx grok-computer-mcp@0.2.0`. Management commands:

```bash
uvx grok-computer-mcp@0.2.0 doctor          # installation, permissions, screenshot size
uvx grok-computer-mcp@0.2.0 status          # JSON status (never exposed to the model)
uvx grok-computer-mcp@0.2.0 stop            # emergency stop: refuse all GUI actions
uvx grok-computer-mcp@0.2.0 resume
uvx grok-computer-mcp@0.2.0 trace purge --all
```

`guard`, `audit`, `verify-gate` and `selfcheck` run the plugin's hooks from the same code, for
machines without a system `python3`.

Configuration is through `GROK_COMPUTER_*` environment variables (backend, host or sandbox mode,
Cua Driver transport, grounding provider and tier); see
[`docs/install.md`](https://github.com/bo-516/computer-use/blob/main/docs/install.md) in the repository.

License: Apache-2.0.
