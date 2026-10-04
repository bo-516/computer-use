# Linux notes for GUI delegation

These notes are for the parent agent when it writes a GUI task or reads a report.

## Setup

- Install Cua Driver (`curl -fsSL https://cua.ai/driver/install.sh | bash`).
- Accessibility (AT-SPI) must be on for the app to expose a tree: GTK apps need
  the accessibility bus (on most desktops it is already running); Qt apps need
  `QT_ACCESSIBILITY=1`; Electron apps need `--force-renderer-accessibility`.
  Launch the app under development with these when you start it.
- CI and headless runs use an X11 server such as Xvfb.

## X11 versus Wayland

- X11: background delivery works for most toolkits; pixel actions use XTest.
- Wayland: what works depends on the compositor (Sway, GNOME/Mutter, KDE/KWin).
  Screen capture and input go through portals that may show a permission dialog;
  the subagent reports `blocked` and the user must approve it once.
- When the driver refuses background delivery, the facade retries in the
  foreground; on some compositors that also fails and the task is blocked.

## What blocks the subagent (STATUS: blocked)

- Polkit/sudo password dialogs, keyring unlock prompts (Seahorse, KWallet) and
  portal permission dialogs.
- Terminal emulators (GNOME Terminal, Konsole, xterm, Alacritty, kitty ...) are on
  the deny list; run commands yourself instead of delegating them.

## Behaviour worth knowing

- `ctrl+alt+t`, `alt+f2` and `ctrl+alt+backspace` are refused.
- Fractional scaling is handled by the facade; coordinates are image pixels.
