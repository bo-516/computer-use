#!/usr/bin/env python3
"""PostToolUse/PostToolUseFailure audit log for GUI tool calls; never blocks.

Thin launcher: the logic lives in the stdlib-only ``hooklib`` package next to this file (Python
3.8+, no network, < 100 ms cold start). See ``hooklib/__init__.py`` for the boundary rules.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hooklib.entry import audit_main

if __name__ == "__main__":
    sys.exit(audit_main())
