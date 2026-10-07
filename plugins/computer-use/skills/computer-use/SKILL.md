---
name: computer-use
description: >-
  How to delegate GUI work to the computer subagent. Use when a task requires
  seeing or interacting with a real desktop app or browser UI: checking that an
  app you just changed looks and behaves correctly, clicking through a local web
  flow, or operating a tool that has no CLI or API.
---

# Computer use (delegation guide)

## When to delegate

Delegate to the `computer-use:computer` subagent only when the task truly needs a
real GUI. Prefer code, CLIs, APIs, and test frameworks whenever they can answer
the question (for example, use unit or end-to-end tests for logic; use the GUI
for "does it actually look and behave right").

Never call `computer__*` or `browser__*` tools yourself. Screenshots would flood
your context, and the guard hook will deny the call.

## Before delegating

- Build and launch the app (or start the dev server) yourself, and confirm it is
  running. Give the subagent the app name or URL.
- Only ONE GUI task at a time: the desktop (screen, focus, clipboard) is shared and
  the computer server answers `BUSY` to a second operator. `run_in_background` may
  be true, but wait for the report before delegating another GUI task.
- If a login is needed, ask the user to sign in first. The subagent never types
  credentials.

## Task template

Use `spawn_subagent` with `subagent_type: "computer-use:computer"` and a prompt
in this shape:

```
GOAL: <one sentence>
CONTEXT: <app name / window / URL; what state it is in; relevant recent code change>
STEPS (optional hints): <known navigation path>
SUCCESS CRITERIA (observable on screen):
  1. <...>
  2. <...>
DO NOT: <actions that are out of scope, e.g. do not submit the form, do not change other settings>
RETURN: the standard report. Attach a screenshot of the final state only when a success criterion is visual (color, image, layout, rendering) or the task asks for one.
```

## Reading the report

The subagent ends with exactly these sections:
STATUS / SUMMARY / EVIDENCE / BLOCKERS / NEXT.

- `success`: check EVIDENCE against your criteria before telling the user it works.
- `partial` / `failed`: use SUMMARY and EVIDENCE to fix the code, rebuild, and
  delegate again with the same criteria.
- `blocked`: the user must act (login, 2FA, permission dialog, risky confirmation).
  Tell the user exactly what is needed. Do not retry blindly.

Screenshot paths in EVIDENCE are local files; open them with `read_file` only if
you need to look yourself.

See `references/` for platform notes (macOS permissions, Windows UAC, Linux
Wayland limits).
