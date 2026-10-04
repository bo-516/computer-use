"""Named budgets and thresholds, each with the source it comes from.

Boundary: constants only. AGENTS.md forbids magic values; every size, byte limit, timeout, retry
count, threshold and mark limit used by the facade is defined here (or next to the single pure
function that owns it) with a comment citing the host limit or the ``docs/goal.md`` section.
"""

from __future__ import annotations

# --- Host image normalization (goal.md §2.1). Above any of these the host re-encodes or rescales
# the image, which silently breaks coordinates, so canonical screenshots stay below all three.
HOST_IMAGE_MAX_EDGE_PX = 2000
HOST_IMAGE_MAX_PIXELS = 2_408_448
HOST_IMAGE_MAX_BYTES = 1_500_000
# Images under 1 KB are dropped by the host (goal.md §2.1).
HOST_IMAGE_MIN_BYTES = 1024

# --- Canonical screenshots (goal.md §5.6.1, §5.9).
SCREENSHOT_LONG_EDGE_PX = 1280
# Smallest long edge a user may configure; below this text in screenshots becomes unreadable.
SCREENSHOT_MIN_LONG_EDGE_PX = 640
JPEG_QUALITY = 80
TARGET_IMAGE_BYTES = 400 * 1024
# Quality steps tried, in order, when a JPEG exceeds TARGET_IMAGE_BYTES; dimensions never change
# (they define the coordinate space).
JPEG_FALLBACK_QUALITIES = (70, 60, 50, 40)
# Long edge requested from the backend before our own deterministic downscale; large enough to
# downscale from, small enough to keep base64 transfers from the driver fast.
BACKEND_CAPTURE_LONG_EDGE_PX = 2560

# --- Text and payload budgets (AGENTS.md "Output budgets", goal.md §5.9).
TOOL_TEXT_MAX_BYTES = 16 * 1024  # host truncates MCP text at 20 KB
ACTION_SUMMARY_MAX_BYTES = 2 * 1024
STATE_FILE_MAX_BYTES = 64 * 1024
MAX_IMAGES_PER_RESULT = 1  # host extracts up to 5; one is enough (goal.md §5.9)
LABEL_MAX_CHARS = 80
VALUE_MAX_CHARS = 60
STATE_LABEL_MAX_CHARS = 200  # docs/schemas/last_observation.schema.json maxLength
SUMMARY_MAX_CHARS = 200

# --- Observation (goal.md §5.5, §5.7).
AUTO_MODE_MIN_INTERACTIVE = 3
DEFAULT_MAX_ELEMENTS = 150
MAX_ELEMENTS_LIMIT = 600
# Elements requested from the backend walk (Cua's default cap is 2 000; goal.md §5.5 relies on
# interactive filtering, not on the backend truncating).
BACKEND_MAX_ELEMENTS = 2000
# Observations kept in memory for observation_id lookups; older ids return STALE_OBSERVATION.
OBSERVATIONS_KEPT = 16
# Frames narrower or shorter than this are virtualized off-viewport rows (Cua reports h:1).
MIN_VISIBLE_EXTENT_PX = 2

# --- Set-of-Mark (goal.md §6.1).
MARKS_PER_PAGE = 80
SOM_FONT_DIVISOR = 90  # font size = long edge / divisor, so labels scale with the screenshot
SOM_MIN_FONT_PX = 11

# --- Staleness (goal.md §5.6.4). Frames are compared on a 4 px grid so sub-pixel jitter does not
# stale an observation while a moved target does.
FINGERPRINT_GRID_PX = 4
DHASH_SIZE = 16  # 16x16 difference hash = 256 bits
DHASH_STALE_BITS = 16  # more differing bits than this means the screen changed

# --- Action settling and waits.
SETTLE_POLL_S = 0.15
SETTLE_TIMEOUT_S = 1.5
WAIT_FOR_POLL_S = 0.25
WAIT_FOR_DEFAULT_MS = 5000
WAIT_FOR_MAX_MS = 30_000
# Pointer movement (screen points) beyond which a foreground action counts as user takeover
# (goal.md §7.7 USER_INTERRUPT).
USER_INTERRUPT_MOVE_PT = 3.0

# --- Session lock (goal.md §4.4, AGENTS.md "one operator per desktop").
LOCK_IDLE_RELEASE_S = 120.0
LOCK_SWEEP_INTERVAL_S = 15.0

# --- Grounding (goal.md §6.2, §6.3).
GROUNDING_CONFIDENCE_THRESHOLD = 0.5
GROUNDING_MAX_CANDIDATES = 3
GROUNDING_SAMPLES = 3
GROUNDING_CLUSTER_RADIUS_PX = 24
GROUNDING_TIMEOUT_S = 30.0
# A raw point counts as grounded (strict tier) when it is this close to a locate candidate.
GROUNDED_POINT_TOLERANCE_PX = 12

# --- Traces (goal.md §7.6).
TRACE_RETENTION_DAYS = 7

# --- Backend calls.
BACKEND_CALL_TIMEOUT_S = 30.0
BACKEND_START_TIMEOUT_S = 20.0
TYPE_TEXT_MAX_CHARS = 10_000
SCROLL_MAX_AMOUNT = 50  # Cua clamps wheel notches to 1..50
CLICK_MAX_COUNT = 3
