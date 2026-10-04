"""Error codes the facade returns to the model (goal.md §5.8).

Boundary: pure data plus one exception type. Every error leaves the server as
``{ok: false, code, message, retryable, hint}``; ``hint`` tells the model its next step. A new
code is added here *and* to the table in goal.md §5.8 in the same change (AGENTS.md).
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    """All error codes. The first thirteen are goal.md §5.8 as written; the rest were added with
    the facade and documented in the same table."""

    STALE_REF = "STALE_REF"
    STALE_OBSERVATION = "STALE_OBSERVATION"
    ELEMENT_NOT_FOUND = "ELEMENT_NOT_FOUND"
    NOT_INTERACTABLE = "NOT_INTERACTABLE"
    OCCLUDED = "OCCLUDED"
    BACKGROUND_UNAVAILABLE = "BACKGROUND_UNAVAILABLE"
    SECURE_FIELD = "SECURE_FIELD"
    APP_DENIED = "APP_DENIED"
    PERMISSION_MISSING = "PERMISSION_MISSING"
    BUSY = "BUSY"
    TIMEOUT = "TIMEOUT"
    USER_INTERRUPT = "USER_INTERRUPT"
    BACKEND_ERROR = "BACKEND_ERROR"
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    KEY_DENIED = "KEY_DENIED"
    POLICY_ERROR = "POLICY_ERROR"
    POINT_UNGROUNDED = "POINT_UNGROUNDED"
    GROUNDING_UNAVAILABLE = "GROUNDING_UNAVAILABLE"


HINTS: dict[ErrorCode, str] = {
    ErrorCode.STALE_REF: (
        "This ref does not belong to the given observation. Call observe again and use a ref "
        "from the new observation."),
    ErrorCode.STALE_OBSERVATION: (
        "The screen changed after this observation. Call observe again, then act with the new "
        "observation_id and refs."),
    ErrorCode.ELEMENT_NOT_FOUND: (
        "No such ref or mark. It may have scrolled out of view: scroll, or expand a container "
        "with observe(root_ref=...)."),
    ErrorCode.NOT_INTERACTABLE: (
        "The element is disabled or hidden. Check preconditions (select or fill other fields "
        "first), then observe again."),
    ErrorCode.OCCLUDED: (
        "Something covers the target. Close the covering dialog or bring the app to the front "
        "with apps(action=\"focus\"), then observe again."),
    ErrorCode.BACKGROUND_UNAVAILABLE: (
        "Background delivery is unsupported here and the automatic foreground retry failed. "
        "Report STATUS: blocked."),
    ErrorCode.SECURE_FIELD: (
        "Do not retry. Ask the user to type into this field themselves and report STATUS: "
        "blocked."),
    ErrorCode.APP_DENIED: "Do not retry. This app is on the deny list; report STATUS: blocked.",
    ErrorCode.PERMISSION_MISSING: (
        "Report STATUS: blocked and name the missing permission so the user can grant it."),
    ErrorCode.BUSY: (
        "Another session is operating the desktop. Wait a few seconds and retry once; if it is "
        "still busy, report it."),
    ErrorCode.TIMEOUT: (
        "The condition did not appear in time. Call observe to see the current state."),
    ErrorCode.USER_INTERRUPT: (
        "The user took over the mouse or pressed the emergency stop. Stop now and report."),
    ErrorCode.BACKEND_ERROR: (
        "Retry once; if it fails again, report STATUS: failed with this message."),
    ErrorCode.INVALID_ARGUMENT: "Fix the arguments as described and call the tool again.",
    ErrorCode.KEY_DENIED: "Do not retry. This key combination is not allowed; report it.",
    ErrorCode.POLICY_ERROR: (
        "The safety policy could not be loaded, so every GUI action is refused. Report STATUS: "
        "blocked with this message."),
    ErrorCode.POINT_UNGROUNDED: (
        "Raw coordinates are disabled. Use a ref or mark, or call locate and click the point "
        "it returns."),
    ErrorCode.GROUNDING_UNAVAILABLE: (
        "No grounding model is configured. Use refs, or observe(mode=\"som\") and click a mark."),
}

RETRYABLE: dict[ErrorCode, bool] = {
    ErrorCode.STALE_REF: True,
    ErrorCode.STALE_OBSERVATION: True,
    ErrorCode.ELEMENT_NOT_FOUND: True,
    ErrorCode.NOT_INTERACTABLE: True,
    ErrorCode.OCCLUDED: True,
    ErrorCode.BACKGROUND_UNAVAILABLE: False,
    ErrorCode.SECURE_FIELD: False,
    ErrorCode.APP_DENIED: False,
    ErrorCode.PERMISSION_MISSING: False,
    ErrorCode.BUSY: True,
    ErrorCode.TIMEOUT: True,
    ErrorCode.USER_INTERRUPT: False,
    ErrorCode.BACKEND_ERROR: True,
    ErrorCode.INVALID_ARGUMENT: True,
    ErrorCode.KEY_DENIED: False,
    ErrorCode.POLICY_ERROR: False,
    ErrorCode.POINT_UNGROUNDED: True,
    ErrorCode.GROUNDING_UNAVAILABLE: False,
}


class FacadeError(Exception):
    """A tool failure reported to the model as an error result (never a protocol error)."""

    def __init__(self, code: ErrorCode, message: str, *, hint: str | None = None) -> None:
        """Create the error.

        Args:
            code: The error code.
            message: What happened, specific to this call.
            hint: Override for the default next-step hint of ``code``.
        """
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint or HINTS[code]

    @property
    def retryable(self) -> bool:
        """Whether retrying (after the hinted step) can succeed."""
        return RETRYABLE[self.code]

    def payload(self) -> dict[str, object]:
        """The structured error result.

        Returns:
            ``{ok: false, code, message, retryable, hint}``.
        """
        return {"ok": False, "code": self.code.value, "message": self.message,
                "retryable": self.retryable, "hint": self.hint}

    def text(self) -> str:
        """Compact model-facing text for the error.

        Returns:
            One or two lines with the code, message and hint.
        """
        return f"ERROR {self.code.value}: {self.message}\nhint: {self.hint}"
