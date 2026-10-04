"""Copy the plugin's ``hooklib`` into the facade package (byte-for-byte).

The hooks must run on the user's system ``python3`` (3.8+, stdlib only), so they cannot import the
facade; the facade vendors the same package to expose ``grok-computer-mcp guard`` and to share
policy semantics. ``tests/test_hooklib_sync.py`` fails when the copies drift; run this script
after editing ``plugins/computer-use/hooks/bin/hooklib``.

Usage: ``uv run python scripts/sync_hooklib.py [--check]``
"""

from __future__ import annotations

import argparse
import filecmp
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "plugins" / "computer-use" / "hooks" / "bin" / "hooklib"
TARGET = REPO / "servers" / "grok-computer-mcp" / "src" / "grok_computer_mcp" / "hooklib"


def differences() -> list[str]:
    """List files that differ, are missing, or are extra in the vendored copy.

    Returns:
        Relative file names that are out of sync.
    """
    source = {p.name for p in SOURCE.glob("*.py")}
    target: set[str] = {p.name for p in TARGET.glob("*.py")} if TARGET.exists() else set()
    out = sorted(source ^ target)
    for name in sorted(source & target):
        if not filecmp.cmp(SOURCE / name, TARGET / name, shallow=False):
            out.append(name)
    return out


def main() -> int:
    """Sync or check the vendored copy.

    Returns:
        Exit code: 0 when in sync (after copying unless --check), 1 when --check finds drift.
    """
    parser = argparse.ArgumentParser(description="Sync the vendored hooklib copy.")
    parser.add_argument("--check", action="store_true", help="only report drift")
    args = parser.parse_args()
    drift = differences()
    if args.check:
        for name in drift:
            print(f"out of sync: {name}", file=sys.stderr)
        return 1 if drift else 0
    if TARGET.exists():
        shutil.rmtree(TARGET)
    TARGET.mkdir(parents=True)
    for path in sorted(SOURCE.glob("*.py")):
        shutil.copyfile(path, TARGET / path.name)
    print(f"synced {len(list(TARGET.glob('*.py')))} files into {TARGET.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
