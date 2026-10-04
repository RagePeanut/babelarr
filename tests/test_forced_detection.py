"""Tests for forced-subtitle detection at the Plex boundary (_subtitle_views).

The ``<lang>-forced`` rule modifier matches on ``SubtitleStreamView.forced``.
That flag normally comes from Plex's proper ``forced`` track flag -- but some
poorly-tagged files encode "forced" ONLY in the human-readable title (e.g.
"Français [FORCED]"), as seen on the real "Rope" release. The rule here:

  * If ANY subtitle track in the part carries the real ``forced`` flag, the file
    is considered well-structured -> trust the flags, ignore titles entirely.
  * Only if NO track is flagged do we fall back to matching the English word
    "forced" in the title (whole word, case-insensitive).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from babelarr.plex_client import _subtitle_views  # noqa: E402


class FakeSubStream:
    def __init__(self, id, lang, forced=False, title=None, ext=None, disp=None):
        self.id = id
        self.languageCode = lang
        self.forced = forced
        self.title = title
        self.extendedDisplayTitle = ext
        self.displayTitle = disp


class FakePart:
    def __init__(self, subs):
        self._subs = subs

    def subtitleStreams(self):
        return self._subs


def _forced_ids(views):
    return [v.id for v in views if v.forced]


def test_uses_flag_when_present():
    part = FakePart([
        FakeSubStream(1, "fra", forced=True, title="Français [FORCED]"),
        FakeSubStream(2, "fra", forced=False, title="Français [FULL]"),
    ])
    assert _forced_ids(_subtitle_views(part)) == [1]


def test_flag_present_ignores_title_markers_on_other_tracks():
    # One track is properly flagged -> file is well-structured -> a different
    # track whose TITLE says "forced" but has no flag is NOT treated as forced.
    part = FakePart([
        FakeSubStream(1, "eng", forced=True, title="English"),
        FakeSubStream(2, "fra", forced=False, title="Forced French"),
    ])
    assert _forced_ids(_subtitle_views(part)) == [1]


def test_title_fallback_when_no_flag_anywhere():
    # The Rope case: no track flagged -> fall back to the title word "forced".
    part = FakePart([
        FakeSubStream(10, "fra", forced=False, title="Français [FORCED]"),
        FakeSubStream(11, "fra", forced=False, title="Français [FULL]"),
        FakeSubStream(12, "eng", forced=False, title="Anglais [FULL]"),
    ])
    assert _forced_ids(_subtitle_views(part)) == [10]


def test_title_fallback_case_insensitive_and_in_extended_title():
    part = FakePart([
        FakeSubStream(10, "fra", forced=False, ext="FR Forced : SRT"),
        FakeSubStream(11, "fra", forced=False, disp="forced"),
        FakeSubStream(12, "eng", forced=False, title="Full"),
    ])
    assert _forced_ids(_subtitle_views(part)) == [10, 11]


def test_title_fallback_whole_word_only():
    # Avoid false positives on substrings like "unforced" / "forcefully".
    part = FakePart([
        FakeSubStream(10, "fra", forced=False, title="Unforced commentary"),
        FakeSubStream(11, "eng", forced=False, title="forcefully dramatic"),
    ])
    assert _forced_ids(_subtitle_views(part)) == []


def test_no_markers_at_all_yields_no_forced():
    part = FakePart([
        FakeSubStream(10, "fra", forced=False, title="Français"),
        FakeSubStream(11, "eng", forced=False, title="English"),
    ])
    assert _forced_ids(_subtitle_views(part)) == []
