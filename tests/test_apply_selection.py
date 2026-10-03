"""Tests for apply_selection -> correct plexapi MediaPart stream methods.

Regression guard: the code must call setSelectedAudioStream /
setSelectedSubtitleStream / resetSelectedSubtitleStream (NOT the non-existent
setDefault*/resetDefault* names). The fake part below exposes ONLY the correct
methods, so a regression to the wrong names raises AttributeError.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from babelarr.plex_client import apply_selection  # noqa: E402
from babelarr.selector import Selection  # noqa: E402


class FakeStream:
    def __init__(self, id, selected=False):
        self.id = id
        self.selected = selected


class FakePart:
    def __init__(self, audio=None, subs=None):
        self._audio = audio or []
        self._subs = subs or []
        self.calls = []

    def audioStreams(self):
        return self._audio

    def subtitleStreams(self):
        return self._subs

    # Only the CORRECT plexapi method names exist here.
    def setSelectedAudioStream(self, stream):
        self.calls.append(("audio", stream))

    def setSelectedSubtitleStream(self, stream):
        self.calls.append(("sub", stream))

    def resetSelectedSubtitleStream(self):
        self.calls.append(("sub_off", None))


def test_sets_audio_via_setSelected():
    part = FakePart(audio=[FakeStream(1, selected=True), FakeStream(2)])
    changed = apply_selection(part, Selection(2, None, False), dry_run=False)
    assert changed
    assert ("audio", 2) in part.calls


def test_sets_subtitle_via_setSelected():
    part = FakePart(subs=[FakeStream(10), FakeStream(11)])
    changed = apply_selection(part, Selection(None, 11, False), dry_run=False)
    assert changed
    assert ("sub", 11) in part.calls


def test_disables_subtitle_via_resetSelected():
    part = FakePart(subs=[FakeStream(10, selected=True)])
    changed = apply_selection(part, Selection(None, None, True), dry_run=False)
    assert changed
    assert ("sub_off", None) in part.calls


def test_no_change_when_already_selected():
    # Audio stream 2 already selected -> no call.
    part = FakePart(audio=[FakeStream(1), FakeStream(2, selected=True)])
    changed = apply_selection(part, Selection(2, None, False), dry_run=False)
    assert not changed
    assert part.calls == []


def test_dry_run_makes_no_calls():
    part = FakePart(audio=[FakeStream(1, selected=True)])
    apply_selection(part, Selection(2, None, False), dry_run=True)
    assert part.calls == []


def test_disable_subs_noop_when_none_selected():
    # No subtitle currently active -> nothing to turn off.
    part = FakePart(subs=[FakeStream(10, selected=False)])
    changed = apply_selection(part, Selection(None, None, True), dry_run=False)
    assert not changed
    assert part.calls == []
