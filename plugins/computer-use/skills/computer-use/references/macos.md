# macOS notes for GUI delegation

These notes are for the parent agent when it writes a GUI task or reads a report.

## Permissions

- Cua Driver needs Accessibility and Screen Recording. Grant them to `CuaDriver.app`
  (System Settings > Privacy & Security), then run it as a daemon so the grants
  belong to the app, not to the terminal: `open -n -g -a CuaDriver --args serve`.
- If a report says `PERMISSION_MISSING` with "Accessibility" or "Screen Recording",
  tell the user to grant it to CuaDriver.app and restart the daemon. Do not retry.
- `grok-computer-mcp doctor` checks both grants and the daemon.

## What blocks the subagent (STATUS: blocked)

- Login windows, 2FA, CAPTCHA, Keychain and password prompts, and every OS
  permission dialog ("would like to access ...", "allow ... to control ...").
- Password managers, Keychain Access, Terminal-like apps and the Privacy &
  Security / Passwords panes of System Settings are on the deny list: the facade
  refuses to observe or act on them.

## Behaviour worth knowing

- Actions are delivered in the background first (no focus steal). When the
  driver reports that background delivery is unavailable, the facade retries
  once in the foreground, which briefly brings the window to the front.
- Moving the physical mouse during a foreground action aborts it with
  `USER_INTERRUPT`; the user can also run `grok-computer-mcp stop`.
- Retina scaling is handled by the facade: coordinates in reports are pixels in
  the observation image, never screen points.
- Electron and web content (Slack, VS Code, browser tabs) often report text
  changes as unverifiable; the subagent confirms with a screenshot.
- Menu-bar items, the Dock and notifications live outside the app window; ask
  for `observe` with `scope: "screen"` only when the task needs them.
- Apps on another Space or minimized can still be read; launching an app with
  `apps(action="launch")` keeps it in the background.
