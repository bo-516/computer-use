# Grok Computer

**English** | [简体中文](README.zh-CN.md)

Computer use for Grok Build: a Grok Build plugin (`computer-use`) plus a purpose-built MCP server (the `grok-computer-mcp` facade) that let Grok Build see the screen, click and type in desktop apps and browsers on macOS, Windows and Linux. The main use is checking UI inside the development loop (change code → launch the app → look at the UI → change again), and operating desktop and web tools that have no API.

Design document: [docs/goal.md](docs/goal.md). Installation and configuration: [docs/install.md](docs/install.md). The documents under `docs/` are written in Chinese.

## How it works

```
main agent ──delegates──▶ computer subagent (own context; only reports go back)
                            │
                            ├─▶ computer: grok-computer-mcp facade ──▶ Cua Driver ──▶ desktop (host / sandbox)
                            └─▶ browser: Playwright MCP (--isolated)
hooks: guard (risk grading and confirmation) · audit · verify_gate (report and final check) · selfcheck
```

- **Screenshots stay out of the main session**: all GUI work goes to the `computer-use:computer` subagent; the main agent only receives a structured report of at most 2 KB (`STATUS / SUMMARY / EVIDENCE / BLOCKERS / NEXT`).
- **Accessibility tree first, pixels last**: an observation is a compact list of elements by default (one interactive element per line, with a stable ref). When the tree is not enough it falls back to a numbered Set-of-Mark screenshot, and only then to a grounding model or raw coordinates.
- **One coordinate space**: every coordinate the model sees or sends is a pixel of the current observation image (long edge 1280 px). HiDPI scaling, window offsets and multiple displays are all handled inside the facade, and an action based on an observation of a screen that has since changed is refused (`STALE_OBSERVATION`).
- **Two safety layers**: the hooks ask you before high-risk actions (send, delete, pay, ...), and the facade enforces hard rules itself and refuses everything when its policy cannot be loaded (deny-listed apps, secure fields, dangerous key chords, a session lock, an emergency stop).

The facade exposes nine tools: `observe`, `click`, `type_text`, `press_keys`, `scroll`, `drag`, `apps`, `wait_for` and `locate`.

## Quick start

```bash
curl -fsSL https://cua.ai/driver/install.sh | bash      # desktop driver (macOS also needs permissions, see the install guide)
curl -LsSf https://astral.sh/uv/install.sh | sh         # runs the facade
grok plugin marketplace add bo-516/computer-use
grok plugin install computer-use --trust
grok plugin enable computer-use
uvx grok-computer-mcp@0.2.0 doctor                      # self-check
```

Then describe the task to grok in plain language, or use `/computer open Settings, turn on dark mode and confirm it took effect`.

**Privacy**: screenshots are sent to the model provider. Deny-listed apps (password managers, keychains, terminals, banking apps, ...) are never observed; traces stay on your machine and are deleted after 7 days by default. See section 6 of the install guide.

## Repository layout

| Path | Contents |
|---|---|
| `.grok-plugin/` | Marketplace index and component catalog |
| `plugins/computer-use/` | The plugin: subagent, skill and platform notes, `/computer` command, hooks, `.mcp.json` |
| `servers/grok-computer-mcp/` | The facade MCP server (Python 3.11+, published on PyPI, run with `uvx`) |
| `eval/` | Task set (60 Cua Bench variants), host-mode runner and metrics, grounding benchmark, Phase 0 probes; see [eval/README.md](eval/README.md) |
| `docs/` | Design document, install guide, Phase 0 verification record, state file schema |
| `scripts/` | hooklib sync, plugin index generator |
| `tests/` | Hook fixture tests and repository contract tests |

## Development

Requires `uv` (it installs Python 3.11 when needed); the dev-loop task tests also need Node.js.

```bash
uv sync
uv run pytest                 # all tests (fake backend; no desktop or account needed)
uv run ruff check
uv run pyright
uv run --python 3.8 --no-project --with pytest==8.3.5 pytest tests/hooks   # hooks on Python 3.8
uv run python scripts/sync_hooklib.py --check     # the facade's hooklib copy matches the plugin
uv run python scripts/plugin_index.py --check     # the component catalog is current
grok plugin validate plugins/computer-use
```

Conventions are in [AGENTS.md](AGENTS.md). The plugin directory ships files only and the hooks use the standard library only; the facade is the only code that talks to Cua Driver; the safety rules (fail-closed facade, R3 never loosened, typed text redacted, ...) must not regress.

## Status

The plugin, the facade, the task set, the grounding benchmark and the Phase 0 probe kit are implemented and tested against a fake backend and a fake Cua Driver. What still needs a real environment (running the Phase 0 probes and the grounding benchmark, running the task set on macOS, Windows and Linux machines) is listed in [docs/goal.md §10.1](docs/goal.md) and [docs/phase0-verification.md](docs/phase0-verification.md).

## License

[Apache-2.0](LICENSE).
