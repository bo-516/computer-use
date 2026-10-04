"""The policy record and its shipped defaults (goal.md §7.3).

Boundary: pure data. ``hooklib.policy`` layers user and project files on top; matching lives in
``hooklib.matching`` and ``hooklib.keys``. Changing a default list changes what the subagent can
reach, so call it out in the PR (AGENTS.md).
"""

from __future__ import annotations

from typing import NamedTuple, Tuple


class Policy(NamedTuple):
    """Effective policy after layering. Immutable; lists are tuples in declaration order."""

    servers: Tuple[str, ...]
    require_subagent: bool
    subagent_types: Tuple[str, ...]
    state_max_age_s: float
    allow_isolated_browser: bool
    allow_apps: Tuple[str, ...]
    deny_apps: Tuple[str, ...]
    risky_words: Tuple[str, ...]
    credential_words: Tuple[str, ...]
    dangerous_keys: Tuple[str, ...]
    poor_ax_apps: Tuple[str, ...]


DEFAULT_POLICY = Policy(
    # MCP server names whose tools are GUI tools (public API, goal.md §4.3). `cua` is listed so a
    # stray raw Cua Driver server is still governed (and denied) in Phase 2+.
    servers=("computer", "browser", "cua"),
    require_subagent=True,
    # spawn_subagent exposes plugin agents as `<plugin>:<agent>`; the bare name covers hosts that
    # report the short form (V3/V13).
    subagent_types=("computer-use:computer", "computer"),
    state_max_age_s=30.0,
    # Playwright MCP runs with --isolated: no user cookies or saved logins (goal.md §7.4).
    allow_isolated_browser=True,
    allow_apps=(),
    # Case-insensitive substrings of the app name; "App > Title" also requires the window title
    # to contain Title. Over-matching only denies more, which is the safe direction.
    deny_apps=(
        # Password managers, keychains, authenticators.
        "1Password", "Bitwarden", "KeePass", "LastPass", "Dashlane", "Enpass", "NordPass",
        "Proton Pass", "Keeper Password", "Keychain Access", "Passwords", "Seahorse",
        "KWalletManager", "Credential Manager", "Authy", "Authenticator", "密码管理", "钥匙串",
        # System security settings.
        "System Settings > Privacy", "System Settings > Passwords", "System Settings > Users",
        "System Settings > Touch ID", "System Preferences > Security", "Windows Security",
        "Settings > Privacy", "Settings > Accounts", "系统设置 > 隐私", "系统设置 > 密码",
        "设置 > 隐私",
        # Terminals: a GUI shell would hand the subagent the shell access it must never have.
        "Terminal", "iTerm", "Warp", "Alacritty", "kitty", "WezTerm", "Ghostty", "Tabby",
        "Terminator", "Tilix", "Konsole", "xterm", "PowerShell", "Command Prompt", "cmd.exe",
        "终端",
        # Banking, brokerage and wallets.
        "Banking", "Trader Workstation", "IB Gateway", "thinkorswim", "MetaTrader", "moomoo",
        "Futu", "Tiger Trade", "Ledger Live", "Trezor Suite", "Exodus", "Electrum",
        "网上银行", "手机银行", "富途牛牛", "老虎证券", "同花顺", "东方财富", "通达信", "钱包",
    ),
    # R2 words (goal.md §7.3): whole words for Latin text, substrings for CJK.
    risky_words=(
        "send", "submit", "delete", "remove", "erase", "wipe", "discard", "overwrite", "pay",
        "payment", "purchase", "buy", "checkout", "place order", "order now", "publish", "post",
        "transfer", "confirm", "uninstall", "reset", "revoke", "deactivate", "close account",
        "empty trash", "sign out", "log out", "logout", "format", "don't save", "do not save",
        "发送", "提交", "删除", "移除", "清空", "支付", "付款", "购买", "下单", "发布", "转账",
        "确认", "卸载", "重置", "退出登录", "注销", "格式化", "覆盖", "丢弃", "不保存",
    ),
    # Labels that make a text input a credential field even when the accessibility role does not
    # say "secure" (web and Electron password inputs). Typing into one is R3.
    credential_words=(
        "password", "passcode", "passphrase", "pin", "pin code", "one-time code",
        "one time code", "verification code", "security code", "2fa", "otp", "mfa", "cvv", "cvc",
        "card number", "credit card", "api key", "api token", "access token", "token", "secret",
        "private key", "seed phrase", "recovery phrase",
        "密码", "口令", "验证码", "动态码", "校验码", "安全码", "卡号", "信用卡", "密钥", "私钥",
        "助记词", "令牌",
    ),
    # R3 key combinations (goal.md §7.3): quit, force quit, log out, lock, power, task managers,
    # run dialogs and terminal launchers.
    dangerous_keys=(
        "cmd+q", "cmd+alt+esc", "cmd+shift+q", "cmd+alt+shift+q", "ctrl+cmd+q", "ctrl+cmd+power",
        "cmd+alt+power", "ctrl+alt+delete", "ctrl+alt+del", "alt+f4", "ctrl+shift+esc",
        "ctrl+alt+backspace", "ctrl+alt+t", "alt+f2", "win+l", "win+r", "win+x",
    ),
    # Apps with known-thin accessibility trees (goal.md §5.7): observe(auto) uses Set-of-Mark.
    poor_ax_apps=(
        "Unity", "Unreal Editor", "Godot", "Remote Desktop", "Windows App", "mstsc",
        "VNC Viewer", "TigerVNC", "RealVNC", "Screen Sharing", "TeamViewer", "AnyDesk", "Parsec",
        "Figma", "Photoshop", "GIMP", "Krita", "Blender", "Excalidraw", "Paint", "java",
        "远程桌面",
    ),
)
