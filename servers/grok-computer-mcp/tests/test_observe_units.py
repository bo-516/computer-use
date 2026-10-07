"""Pure observation pieces: roles, stable refs, rendering, diffs, capture, Set-of-Mark."""

from __future__ import annotations

import io
import json
from typing import cast

import pytest
from PIL import Image

from grok_computer_mcp.backend.base import Capture, RawElement
from grok_computer_mcp.coords import Space, make_frame
from grok_computer_mcp.geometry import Display, Point, Rect
from grok_computer_mcp.handlers.results import observe_output
from grok_computer_mcp.hooklib.defaults import DEFAULT_POLICY
from grok_computer_mcp.hooklib.state import TEXT_ENTRY_ROLES as HOOK_TEXT_ROLES
from grok_computer_mcp.limits import (
    MARKS_PER_PAGE,
    TARGET_IMAGE_BYTES,
    TEXT_SECTION_MAX_BYTES,
    TEXT_SECTION_MAX_LINES,
    TOOL_TEXT_MAX_BYTES,
)
from grok_computer_mcp.observe import capture
from grok_computer_mcp.observe.diff import (
    WindowContext,
    cap_changes,
    dialog_changes,
    element_changes,
    text_changes,
    window_changes,
)
from grok_computer_mcp.observe.elements import ObservedElement
from grok_computer_mcp.observe.refs import RefAllocator
from grok_computer_mcp.observe.render import render, state_of
from grok_computer_mcp.observe.roles import TEXT_ENTRY_ROLES, is_interactive, normalize_role
from grok_computer_mcp.observe.som import Region, marks_for
from grok_computer_mcp.observe.textsel import mark_credential_text
from grok_computer_mcp.observe.tree import build_elements, clip, focused_element, subtree
from grok_computer_mcp.safety.policy import SafetyRules
from grok_computer_mcp.session import Observation

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


def test_clip_strips_private_use_and_lone_surrogates() -> None:
    assert clip("\uE001Search", 40) == "Search"
    assert clip("\U00100000Search", 40) == "Search"
    assert clip("\ud800Search", 40) == "Search"
    assert clip("\uE001", 40) == ""
    els = build(
        [
            raw(0, "AXWindow", "W", (0, 0, 640, 400)),
            raw(1, "AXButton", "\uE001Search", (10, 10, 80, 20), 0),
            raw(2, "AXButton", "\uE001", (100, 10, 80, 20), 0),
        ],
        RefAllocator(),
    )
    assert els[1].label == "Search"
    assert els[2].label == ""


def test_state_tokens_appear_only_when_the_backend_sent_them() -> None:
    els = build(
        [
            raw(0, "AXWindow", "W", (0, 0, 640, 400)),
            raw(1, "AXCheckBox", "Sync", (1, 1, 40, 20), 0, value="1"),
        ],
        RefAllocator(),
    )
    assert els[1].expanded is None and els[1].min_value is None and els[1].placeholder is None
    assert state_of(els[1]) == "on"
    slider = _el("s", 1, "slider", "Volume", None, interactive=True, value="50",
                 min_value=0, max_value=100)
    disclosure = _el("d", 2, "disclosure", "More", None, interactive=True, expanded=False)
    named = _el("f", 3, "textfield", "Nickname", None, interactive=True, value="",
                placeholder="Optional")
    assert state_of(slider) == "50 (0\u2013100)"
    assert state_of(disclosure) == "collapsed"
    assert state_of(named) == 'placeholder:"Optional"'
    assert state_of(_el("g", 4, "disclosure", "More", None, interactive=True)) == ""


def test_grouping_text_scroll_and_credential_lines() -> None:
    image = Rect(0, 0, 1280, 800)
    window = _el("e0", 0, "window", "W", None, bbox=Rect(0, 0, 1280, 800))
    profile = _el("e1", 1, "group", "Profile", "e0", bbox=Rect(0, 0, 400, 300))
    unused = _el("e2", 2, "group", "Unused", "e0", bbox=Rect(0, 700, 80, 20))
    save = _el("e3", 3, "button", "Save", "e1", interactive=True, bbox=Rect(10, 10, 80, 28))
    dup = _el("e4", 4, "text", "Save", "e3", bbox=Rect(10, 10, 80, 16))
    saved = _el("e5", 5, "text", "Changes saved", "e1", bbox=Rect(10, 40, 200, 16))
    count = _el("e6", 6, "text", "Token count: 3", "e1", bbox=Rect(10, 60, 200, 16))
    secret = _el("e7", 7, "text", "API token", "e1", value="b" * 32, bbox=Rect(10, 80, 300, 16))
    off = _el("e8", 8, "text", "Off screen phrase", "e1", visible=False, bbox=Rect(10, 900, 80, 16))
    recent = _el("e9", 9, "list", "Recent files", "e0", bbox=Rect(0, 0, 400, 400))
    visible = [
        _el(f"r{i}", 20 + i, "row", f"row {i}", "e9", interactive=True,
            bbox=Rect(10, 40 + i * 20, 100, 18))
        for i in range(3)
    ]
    virtual = [
        _el(f"v{i}", 40 + i, "row", f"virt {i}", "e9", interactive=True,
            bbox=Rect(10, 820 + i, 100, 1))
        for i in range(20)
    ]
    inbox = _el("e10", 10, "list", "Inbox", "e0", bbox=Rect(500, 40, 200, 80))
    mail = _el("m0", 11, "row", "mail", "e10", interactive=True, bbox=Rect(510, 50, 80, 18))
    elements = mark_credential_text(
        [window, profile, unused, save, dup, saved, count, secret, off, recent, *visible,
         *virtual, inbox, mail], RULES)
    controls = [save, *visible, *virtual, mail]
    out = render("header", controls, elements, max_elements=100, text_budget_bytes=3000,
                 image=image)
    assert 'group "Profile"' in out.text
    assert "Unused" not in out.text
    assert 'text "Changes saved"' in out.text
    assert 'text "Save"' not in out.text
    assert 'text "Token count: 3"' in out.text
    assert "text (hidden: credential)" in out.text
    assert "b" * 32 not in out.text
    assert "Off screen phrase" not in out.text
    save_line = next(line for line in out.text.splitlines()
                     if '"Save"' in line and "button" in line)
    assert save_line.startswith("  ")
    recent_line = next(line for line in out.text.splitlines() if "Recent files" in line)
    assert "scroll \u21910 \u219320" in recent_line
    inbox_line = next(line for line in out.text.splitlines() if '"Inbox"' in line)
    assert "scroll" not in inbox_line
    assert set(out.listed) == {el.ref for el in controls}


def test_text_section_keeps_the_interactive_count_inside_its_caps() -> None:
    window = _el("e0", 0, "window", "W", None, bbox=Rect(0, 0, 1280, 800))
    buttons = [
        _el(f"b{i}", i, "button", f"Button {i}", "e0", interactive=True,
            bbox=Rect(10, 10, 80, 20))
        for i in range(1, 4)
    ]
    texts = [
        _el(f"t{i}", 100 + i, "text", f"line {i} " + "x" * 30, "e0", bbox=Rect(10, 40, 200, 16))
        for i in range(60)
    ]
    elements = [window, *buttons, *texts]
    plain = render("header", buttons, elements, max_elements=10)
    rich = render("header", buttons, elements, max_elements=10,
                  text_budget_bytes=TEXT_SECTION_MAX_BYTES, image=Rect(0, 0, 1280, 800))
    assert plain.listed == rich.listed
    text_lines = [line for line in rich.text.splitlines() if "text \"" in line]
    assert len(text_lines) <= TEXT_SECTION_MAX_LINES
    assert len("\n".join(text_lines).encode()) <= TEXT_SECTION_MAX_BYTES
    obs = Observation(
        id="obs_budget", created_at=0, scope="window", app="App", pid=1, window=None,
        frame=FRAME, frame_space=Space.DESKTOP_POINTS, elements=elements, marks={},
        fingerprint="x", mode="tree")
    produced = observe_output(obs, None, max_elements=50)
    total = len(produced.text.encode()) + len(json.dumps(produced.structured).encode())
    assert total <= TOOL_TEXT_MAX_BYTES
    listed = produced.structured["elements"]
    assert isinstance(listed, list)
    assert len(cast(list[object], listed)) == len(buttons)


def test_text_and_dialog_diffs_follow_element_diffs() -> None:
    before = [
        _el("e0", 0, "window", "W", None),
        _el("e1", 1, "text", "Status", None, bbox=Rect(10, 10, 80, 16)),
        _el("e2", 2, "text", "API token", None, value="c" * 32, hidden_text=True,
            bbox=Rect(10, 30, 200, 16)),
    ]
    dialog = _el("e9", 9, "dialog", "Delete file?", None, bbox=Rect(100, 100, 200, 80))
    after = [
        before[0],
        _el("e1", 1, "text", "Status", None, value="Changes saved", bbox=Rect(10, 10, 200, 16)),
        before[2],
        dialog,
    ]
    texts = text_changes(before, after)
    dialogs = dialog_changes(before, after)
    assert {"kind": "text_appeared", "text": "Changes saved"} in texts
    assert {"kind": "text_gone", "text": "Status"} in texts
    assert all("c" * 32 not in str(item.get("text", "")) for item in texts)
    assert {"kind": "dialog_opened", "ref": "e9", "label": "Delete file?"} in dialogs
    closed = dialog_changes(after, before)
    assert {"kind": "dialog_closed", "ref": "e9", "label": "Delete file?"} in closed


_BOX = Rect(10, 10, 40, 20)


def _el(
    ref: str, index: int, role: str, label: str, parent: str | None, *,
    value: str | None = None, bbox: Rect | None = None,
    interactive: bool = False, visible: bool = True, expanded: bool | None = None,
    min_value: float | None = None, max_value: float | None = None,
    placeholder: str | None = None, hidden_text: bool = False,
) -> ObservedElement:
    """One image-space element for render tests."""
    return ObservedElement(
        ref=ref, index=index, handle=None, role=role, raw_role=role, label=label, value=value,
        bbox=_BOX if bbox is None else bbox, interactive=interactive, visible=visible,
        secure=False, in_form=False,
        enabled=True, focused=False, selected=None, parent_ref=parent,
        depth=0 if parent is None else 1, expanded=expanded, min_value=min_value,
        max_value=max_value, placeholder=placeholder, hidden_text=hidden_text)
