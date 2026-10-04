"""``grok-computer-mcp doctor`` and ``status``: installation self-check (goal.md B.1, §10 Phase 4).

Boundary: management I/O, never exposed to the model (goal.md §5.4 "status 不暴露给模型"). Checks
what a new user needs for the 10-minute install target (G4): runtimes for the hooks and servers,
the driver and its OS permissions, the policy, writable state, and that canonical screenshots stay
under the host's re-encode thresholds.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from .backend.base import Backend, BackendError
from .config import Settings
from .coords import make_frame
from .errors import FacadeError
from .lock import DesktopLock
from .observe import capture as capture_mod
from .safety.policy import PolicyHolder

MIN_FACADE_PYTHON = (3, 11)
MIN_HOOK_PYTHON = (3, 8)
PROBE_TIMEOUT_S = 10.0


@dataclass(frozen=True)
class Check:
    """One check result."""

    name: str
    ok: bool
    detail: str
    critical: bool = True


def _hook_python() -> Check:
    """The hooks run on the user's ``python3`` (3.8+)."""
    exe = shutil.which("python3")
    if exe is None:
        return Check("hooks python3", False, "python3 not on PATH: hooks fail open, only the "
                     "facade's hard rules protect you", critical=False)
    try:
        out = subprocess.run([exe, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
                             capture_output=True, text=True, timeout=PROBE_TIMEOUT_S, check=False)
        major, minor = (int(p) for p in out.stdout.strip().split("."))
    except (OSError, ValueError, subprocess.SubprocessError):
        return Check("hooks python3", False, f"{exe} did not report a version", critical=False)
    ok = (major, minor) >= MIN_HOOK_PYTHON
    return Check("hooks python3", ok, f"{exe} is {major}.{minor} (need 3.8+)", critical=False)


def _writable(name: str, path: str) -> Check:
    """Create and remove a probe file in a directory."""
    try:
        Path(path).mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path, prefix=".doctor-"):
            pass
    except OSError as exc:
        return Check(name, False, f"{path}: {exc.strerror or exc}")
    return Check(name, True, path)


def static_checks(settings: Settings) -> list[Check]:
    """Checks that need no driver.

    Args:
        settings: Facade settings.

    Returns:
        Results in display order.
    """
    checks = [Check("facade python", sys.version_info[:2] >= MIN_FACADE_PYTHON,
                    sys.version.split()[0]), _hook_python()]
    if settings.backend == "cua-driver":
        exe = shutil.which(settings.cua_command[0])
        checks.append(Check("cua-driver", exe is not None,
                            exe or f"{settings.cua_command[0]} not on PATH (see goal.md B.1)"))
    for tool, why in (("npx", "browser server (Playwright MCP)"), ("uvx", "facade launcher")):
        found = shutil.which(tool)
        checks.append(Check(tool, found is not None, found or f"missing: needed for the {why}",
                            critical=False))
    holder = PolicyHolder(settings.home, settings.workspace)
    try:
        holder.rules()
        checks.append(Check("policy", True, "loaded" + (f"; warnings: {'; '.join(holder.warnings)}"
                                                        if holder.warnings else "")))
    except FacadeError as err:
        checks.append(Check("policy", False, err.message))
    checks.append(_writable("state dir", str(settings.state_dir)))
    checks.append(_writable("trace dir", str(settings.trace_dir)))
    lock = DesktopLock(settings.lock_dir / "doctor-probe.lock")
    try:
        lock.acquire()
        lock.release()
        checks.append(Check("lock dir", True, str(settings.lock_dir)))
    except (FacadeError, OSError) as exc:
        checks.append(Check("lock dir", False, str(exc)))
    checks.append(Check("mode", True, f"{settings.mode} ({settings.backend})", critical=False))
    grounding = settings.grounding_provider
    checks.append(Check("grounding", True, f"{grounding}, tier {settings.grounding_tier}",
                        critical=False))
    return checks


async def backend_checks(settings: Settings, backend: Backend) -> list[Check]:
    """Checks that talk to the driver: permissions and the canonical screenshot size.

    Args:
        settings: Facade settings.
        backend: Backend to probe (closed afterwards).

    Returns:
        Results in display order.
    """
    checks: list[Check] = []
    try:
        await backend.start()
        status = await backend.status()
        checks.append(Check("driver", True, f"{status.name} {status.version} on {status.platform}"))
        for perm, granted in sorted(status.permissions.items()):
            checks.append(Check(f"permission {perm}", granted,
                                "granted" if granted else "missing: grant it to CuaDriver.app"))
        snap = await backend.read_screen(screenshot=True)
        if snap.capture is not None:
            frame = make_frame(snap.display.bounds, snap.display, settings.long_edge,
                               (snap.capture.width, snap.capture.height))
            encoded = capture_mod.encode(capture_mod.canonical(snap.capture, frame))
            checks.append(Check("screenshot", True,
                                f"{encoded.width}x{encoded.height} JPEG q{encoded.quality}, "
                                f"{len(encoded.data) // 1024} KB (host limits 2000 px / 1.5 MB)"))
        else:
            checks.append(Check("screenshot", False, "the driver returned no capture"))
    except (BackendError, ValueError) as exc:
        checks.append(Check("driver", False, str(exc)))
    finally:
        try:
            await backend.close()
        except BackendError:
            checks.append(Check("driver close", False, "close failed", critical=False))
    return checks


def render(checks: list[Check]) -> str:
    """Human-readable report.

    Args:
        checks: Results.

    Returns:
        One line per check.
    """
    lines: list[str] = []
    for c in checks:
        mark = "ok  " if c.ok else ("FAIL" if c.critical else "warn")
        lines.append(f"[{mark}] {c.name}: {c.detail}")
    return "\n".join(lines)


def as_json(checks: list[Check]) -> list[dict[str, object]]:
    """Machine-readable report."""
    return [asdict(c) for c in checks]
