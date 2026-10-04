# Windows notes for GUI delegation

These notes are for the parent agent when it writes a GUI task or reads a report.

## Setup

- Install Cua Driver (`irm https://cua.ai/driver/install.ps1 | iex`) and run it in
  the interactive console or RDP session that shows the app.
- The plugin hooks run `python3`. If `python3` is missing or opens the Microsoft
  Store stub, install Python 3.8+ (python.org or `winget install Python.Python.3.12`).
  Without it the hooks fail open and only the facade's hard rules protect you.

## What blocks the subagent (STATUS: blocked)

- UAC prompts run on the secure desktop and cannot be automated. Elevated
  windows refuse input from a non-elevated driver (`PERMISSION_MISSING`, "target
  runs elevated"); ask the user to restart the app without elevation or to do
  the step themselves.
- Windows Hello, credential prompts, Windows Security and the Privacy and
  Accounts pages of Settings are denied by policy.
- PowerShell, Command Prompt and Windows Terminal are on the deny list.

## Behaviour worth knowing

- Per-monitor DPI scaling is handled by the facade; coordinates in reports are
  image pixels.
- Background delivery uses UI Automation where the control supports it; legacy
  Win32 or game windows may need the foreground retry or Set-of-Mark targeting.
- `alt+f4`, `win+l`, `win+r`, `win+x` and `ctrl+shift+esc` are refused.
