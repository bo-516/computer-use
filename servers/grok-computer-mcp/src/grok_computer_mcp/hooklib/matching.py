"""Pure text matching for the safety policy: app patterns, risky words, credential words.

Boundary: no I/O, stdlib only, Python 3.8. Shared by the guard hook and (vendored) the facade so
both layers classify the same label the same way.

Matching rules (documented in goal.md §7.3):

* deny patterns match case-insensitive *substrings* of the app name, optionally narrowed by a
  window-title substring with the ``"App > Title"`` form; over-matching denies more, which is safe;
* allow patterns match the whole app name (case-insensitive), so ``"Code"`` never allows ``Xcode``;
* word lists match whole words for Latin text (``"post"`` does not hit ``"Postal code"``) and
  substrings for CJK, which has no word boundaries.
"""

from __future__ import annotations

import re
from typing import Iterable, List, Optional, Pattern, Sequence, Tuple

# A Latin phrase: letters, digits, spaces, apostrophes and hyphens. Anything else (CJK) is
# matched as a plain substring.
_LATIN_PHRASE = re.compile(r"^[0-9a-z][0-9a-z '\-]*$")
# Characters that end a Latin word. Underscore is not a word character here, so identifiers such
# as "delete_button" still match "delete".
_WORD_EDGE_BEFORE = r"(?<![0-9a-z])"
_WORD_EDGE_AFTER = r"(?![0-9a-z])"


def normalize(text: str) -> str:
    """Casefold and collapse whitespace so comparisons ignore case and spacing.

    Args:
        text: Any label, app name or pattern.

    Returns:
        The normalized text ("" for blank input).
    """
    return " ".join(text.casefold().split())


def split_app_pattern(pattern: str) -> Tuple[str, str]:
    """Split ``"App > Title"`` into normalized app and title parts.

    Args:
        pattern: A deny or allow pattern.

    Returns:
        ``(app, title)``; ``title`` is "" when the pattern has no ``>``.
    """
    app, sep, title = pattern.partition(">")
    return normalize(app), (normalize(title) if sep else "")


def denied_app_pattern(app: str, window_title: str, patterns: Sequence[str]) -> Optional[str]:
    """Return the first deny pattern that matches the app (and window title), if any.

    Args:
        app: App name as reported by the facade or the tool input.
        window_title: Title of the target window, "" when unknown.
        patterns: ``Policy.deny_apps``.

    Returns:
        The matching pattern, or None. A blank app never matches.
    """
    napp = normalize(app)
    ntitle = normalize(window_title)
    if not napp:
        return None
    for pattern in patterns:
        papp, ptitle = split_app_pattern(pattern)
        if papp and papp in napp and (not ptitle or ptitle in ntitle):
            return pattern
    return None


def allowed_app(app: str, patterns: Sequence[str]) -> bool:
    """Whether the app is on the allow list (whole-name, case-insensitive match).

    Args:
        app: App name.
        patterns: ``Policy.allow_apps``.

    Returns:
        True when some pattern equals the app name after normalization.
    """
    napp = normalize(app)
    return bool(napp) and any(normalize(p) == napp for p in patterns)


def compile_words(words: Iterable[str]) -> Optional[Pattern[str]]:
    """Compile a word list into one regex over normalized text.

    Args:
        words: Risky or credential words (any mix of Latin and CJK).

    Returns:
        The compiled pattern, or None for an empty list.
    """
    parts: List[str] = []
    for word in words:
        nword = normalize(word)
        if not nword:
            continue
        if _LATIN_PHRASE.match(nword):
            body = r"\s+".join(re.escape(tok) for tok in nword.split(" "))
            parts.append(_WORD_EDGE_BEFORE + body + _WORD_EDGE_AFTER)
        else:
            parts.append(re.escape(nword))
    if not parts:
        return None
    return re.compile("|".join(parts))


def find_word(text: str, pattern: Optional[Pattern[str]]) -> Optional[str]:
    """Return the first listed word found in ``text``.

    Args:
        text: Label, element description or URL to scan.
        pattern: Result of ``compile_words``.

    Returns:
        The matched word as it appears in the normalized text, or None.
    """
    if pattern is None or not text:
        return None
    match = pattern.search(normalize(text))
    return match.group(0) if match else None
