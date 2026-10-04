"""Pure observation pieces: roles, stable refs, rendering, diffs, capture, Set-of-Mark."""

from __future__ import annotations

import io
import json

import pytest
from PIL import Image

from grok_computer_mcp.backend.base import Capture, RawElement
from grok_computer_mcp.coords import Space, make_frame
from grok_computer_mcp.geometry import Display, Point, Rect
from grok_computer_mcp.hooklib.defaults import DEFAULT_POLICY
from grok_computer_mcp.hooklib.state import TEXT_ENTRY_ROLES as HOOK_TEXT_ROLES
from grok_computer_mcp.limits import MARKS_PER_PAGE, TARGET_IMAGE_BYTES
from grok_computer_mcp.observe import capture
from grok_computer_mcp.observe.diff import (
    WindowContext,
    cap_changes,
    element_changes,
    window_changes,
)
from grok_computer_mcp.observe.elements import ObservedElement
from grok_computer_mcp.observe.refs import RefAllocator
from grok_computer_mcp.observe.render import render, state_of
from grok_computer_mcp.observe.roles import TEXT_ENTRY_ROLES, is_interactive, normalize_role
from grok_computer_mcp.observe.som import Region, marks_for
from grok_computer_mcp.observe.tree import build_elements, focused_element, subtree
from grok_computer_mcp.safety.policy import SafetyRules

DISPLAY = Display("d", Rect(0, 0, 3000, 2000), 2.0, Point(0, 0))
FRAME = make_frame(Rect(0, 0, 640, 400), DISPLAY, 1280)
RULES = SafetyRules.from_policy(DEFAULT_POLICY)


def raw(
    index: int,
    role: str,
    label: str,
    frame: tuple[float, float, float, float],
    parent: int | None = None,
    *,
    value: str | None = None,
    focused: bool = False,
    subrole: str = "",
) -> RawElement:
    """A raw element in DESKTOP_POINTS."""
    return RawElement(
        index=index,
        role=role,
        label=label,
        value=value,
        frame=Rect(*frame),
        parent=parent,
        handle=f"h{index}",
        focused=focused,
        subrole=subrole,
    )


def build(elements: list[RawElement], refs: RefAllocator) -> list[ObservedElement]:
    """Build observation elements on the test frame."""
    return build_elements(elements, FRAME, Space.DESKTOP_POINTS, RULES, refs)


@pytest.mark.parametrize(
    "role,subrole,expected",
    [
        ("AXButton", "", "button"),
        ("push button", "", "button"),
        ("Edit", "", "textfield"),
        ("AXTextField", "AXSecureTextField", "securefield"),
        ("password text", "", "securefield"),
        ("AXCheckBox", "AXSwitch", "switch"),
        ("AXRadioButton", "AXTabButton", "tab"),
        ("Text", "", "text"),
        ("AXStaticText", "", "text"),
        ("Hyperlink", "", "link"),
        ("SomethingNew", "", "somethingnew"),
    ],
)
def test_role_normalization(role: str, subrole: str, expected: str) -> None:
    assert normalize_role(role, subrole) == expected


def test_role_tables_agree_with_hooks() -> None:
    assert tuple(TEXT_ENTRY_ROLES) == tuple(HOOK_TEXT_ROLES)
    assert is_interactive("group", ("AXPress",)) and not is_interactive("group", ())


def test_classification_secure_and_visibility() -> None:
    els = build(
        [
            raw(0, "AXWindow", "W", (0, 0, 640, 400)),
            raw(1, "AXTextField", "Password", (10, 10, 100, 20), 0, value="x"),
            raw(2, "AXTextField", "Name", (10, 40, 100, 20), 0, value="Ana", focused=True),
            raw(3, "AXRow", "virtual", (10, 900, 100, 1), 0),
        ],
        RefAllocator(),
    )
    pwd, name, row = els[1], els[2], els[3]
    assert pwd.secure and pwd.value is None
    assert not name.secure and name.in_form and name.value == "Ana"
    assert not row.visible
    assert focused_element(els) is name
    assert [e.ref for e in subtree(els, els[0].ref)] == [e.ref for e in els]


def test_refs_are_stable_and_survive_label_change_in_place() -> None:
    refs = RefAllocator()
    first = build(
        [
            raw(0, "AXWindow", "W", (0, 0, 640, 400)),
            raw(1, "AXButton", "Start", (10, 10, 80, 20), 0),
            raw(2, "AXButton", "Help", (100, 10, 80, 20), 0),
        ],
        refs,
    )
    second = build(
        [
            raw(0, "AXWindow", "W", (0, 0, 640, 400)),
            raw(1, "AXButton", "New", (300, 300, 80, 20), 0),
            raw(2, "AXButton", "Stop", (10, 10, 80, 20), 0),
            raw(3, "AXButton", "Help", (100, 10, 80, 20), 0),
        ],
        refs,
    )
    by_label = {e.label: e.ref for e in second}
    assert by_label["Stop"] == first[1].ref  # renamed in place keeps its ref
    assert by_label["Help"] == first[2].ref
    assert by_label["New"] not in {e.ref for e in first}


def test_render_lists_visible_first_and_stays_in_budget() -> None:
    els = build(
        [raw(0, "AXWindow", "W", (0, 0, 640, 400))]
        + [raw(i, "AXButton", f"Button {i}", (10, 10 + i, 80, 5), 0) for i in range(1, 300)],
        RefAllocator(),
    )
    out = render("header", els[1:], els, max_elements=1000, budget_bytes=4096)
    assert len(out.text.encode()) <= 4096
    assert out.omitted > 0 and "more elements not listed" in out.text


def test_state_column() -> None:
    els = build(
        [
            raw(0, "AXWindow", "W", (0, 0, 640, 400)),
            raw(1, "AXCheckBox", "Sync", (1, 1, 5, 5), 0, value="1"),
            raw(2, "AXTextField", "Name", (1, 10, 5, 5), 0, value='say "hi"'),
            raw(3, "AXTextField", "API token", (1, 20, 5, 5), 0, subrole="AXSecureTextField"),
        ],
        RefAllocator(),
    )
    assert state_of(els[1]) == "on"
    assert state_of(els[2]) == "\"say 'hi'\""
    assert state_of(els[3]) == "(secure, not typable)"


def test_diff_and_cap() -> None:
    refs = RefAllocator()
    before = build(
        [
            raw(0, "AXWindow", "W", (0, 0, 640, 400)),
            raw(1, "AXCheckBox", "Sync", (1, 1, 50, 20), 0, value="0"),
        ],
        refs,
    )
    after = build(
        [
            raw(0, "AXWindow", "W", (0, 0, 640, 400)),
            raw(1, "AXCheckBox", "Sync", (1, 1, 50, 20), 0, value="1"),
        ]
        + [raw(i, "AXButton", f"New {i}", (1, 30 + i, 50, 20), 0) for i in range(2, 12)],
        refs,
    )
    changes = element_changes(before, after)
    assert changes[0] == {"ref": before[1].ref, "field": "value", "before": "0", "after": "1"}
    assert {"kind": "appeared_more", "count": 5} in changes
    big: list[dict[str, object]] = [{"kind": "x", "pad": "y" * 300}] * 20
    capped = cap_changes(big, 2048)
    assert len(json.dumps(capped).encode()) <= 2048
    assert capped[-1]["kind"] == "truncated"
    assert window_changes(WindowContext("A", 1, "t"), WindowContext("A", 1, "u")) == [
        {"kind": "window_title", "before": "t", "after": "u"}
    ]


def test_capture_canonicalizes_masks_and_encodes() -> None:
    noise = Image.effect_noise((2560, 1600), 120).convert("RGB")
    buf = io.BytesIO()
    noise.save(buf, format="PNG")
    frame = make_frame(Rect(0, 0, 1280, 800), DISPLAY, 1280, (2560, 1600))
    img = capture.canonical(Capture(buf.getvalue(), "image/png", 2560, 1600), frame)
    assert img.size == (1280, 800)
    masked = capture.mask(img, [Rect(100, 100, 200, 100)])
    assert masked.getpixel((150, 120)) == capture.MASK_FILL
    encoded = capture.encode(masked)
    assert (encoded.width, encoded.height) == (1280, 800)
    assert len(encoded.data) <= TARGET_IMAGE_BYTES or encoded.quality == 40
    with pytest.raises(ValueError, match="thresholds"):
        capture.encode(Image.new("RGB", (2000, 10)))


def test_marks_page_and_merge_regions() -> None:
    els = build(
        [raw(0, "AXWindow", "W", (0, 0, 640, 400))]
        + [
            raw(i, "AXButton", f"B{i}", (1 + (i % 50) * 10, 1 + (i // 50) * 30, 8, 8), 0)
            for i in range(1, 121)
        ],
        RefAllocator(),
    )
    page1, pages = marks_for(els, [], 1)
    page2, _ = marks_for(els, [], 2)
    assert pages == 2 and len(page1) == MARKS_PER_PAGE and page2[0].number == MARKS_PER_PAGE + 1
    inside = Region(Rect(24, 4, 10, 10), "text", "B1")  # B1 is image (22, 2, 16, 16)
    elsewhere = Region(Rect(1000, 700, 50, 20), "icon", "")
    numbered, _ = marks_for(els[:3], [inside, elsewhere], 1)
    assert [m.ref is None for m in numbered] == [False, False, True]
