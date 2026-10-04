#!/usr/bin/env python3
"""SubagentStop gate for the computer subagent: report format and verify-before-success.

Thin launcher: the logic lives in the stdlib-only ``hooklib`` package next to this file (Python
3.8+, no network, < 100 ms cold start). See ``hooklib/__init__.py`` for the boundary rules.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hooklib.entry_stop import verify_gate_main

if __name__ == "__main__":
    sys.exit(verify_gate_main())
