"""Tests for lock-state persistence (labels + file backends) and detection."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from babelarr.state import (  # noqa: E402
    FileState,
    LabelState,
    build_state,
    fingerprint,
    item_guid_key,
    label_for,
)


class FakeTag:
    def __init__(self, tag):
        self.tag = tag


class FakeGuid:
    def __init__(self, id):
        self.id = id


class FakeItem:
    """Minimal stand-in for a plexapi item for state tests."""

    def __init__(self, rating_key="1", guids=None, labels=None):
        self.ratingKey = rating_key
        self.guids = [FakeGuid(g) for g in (guids or [])]
        self.guid = None
        self._labels = list(labels or [])

    @property
    def labels(self):
        return [FakeTag(t) for t in self._labels]

    def addLabel(self, labels, locked=True):
        for l in labels:
            if l not in self._labels:
                self._labels.append(l)

    def removeLabel(self, labels, locked=True):
        for l in labels:
            if l in self._labels:
                self._labels.remove(l)


# --- fingerprint ------------------------------------------------------------

def test_fingerprint_stable_and_short():
    f = fingerprint("The Film")
    assert f == fingerprint("The Film")
    assert len(f) == 8
    assert fingerprint("Other") != f


def test_guid_key_prefers_tmdb():
    item = FakeItem(guids=["imdb://tt123", "tmdb://999"])
    assert item_guid_key(item) == "tmdb://999"


def test_guid_key_falls_back_to_rating_key():
    item = FakeItem(rating_key="42", guids=[])
    assert item_guid_key(item) == "ratingKey://42"


# --- label backend ----------------------------------------------------------

def test_label_is_ours_roundtrip():
    st = LabelState()
    item = FakeItem()
    assert not st.is_ours(item, "title", "The Film")
    st.record(item, "title", "The Film")
    assert st.is_ours(item, "title", "The Film")
    # A different current value is NOT ours (user edited it).
    assert not st.is_ours(item, "title", "Changed By User")


def test_label_record_is_self_healing():
    st = LabelState()
    item = FakeItem(labels=[label_for("title", "Old"), "babelarr-locked:title:deadbeef"])
    st.record(item, "title", "New")
    titles = [t for t in item._labels if t.startswith("babelarr-locked:title:")]
    assert titles == [label_for("title", "New")]  # exactly one, the fresh one


def test_label_per_field_independent():
    st = LabelState()
    item = FakeItem()
    st.record(item, "title", "T")
    st.record(item, "thumb", "P")
    assert st.is_ours(item, "title", "T")
    assert st.is_ours(item, "thumb", "P")
    assert not st.is_ours(item, "title", "P")


def test_label_none_value_not_ours():
    st = LabelState()
    item = FakeItem()
    st.record(item, "title", "T")
    assert not st.is_ours(item, "title", None)


def test_label_is_ours_when_plex_recased_label():
    # Plex title-cases stored labels: babelarr-locked:... -> Babelarr-locked:...
    # The real-world 3% bug. Must still be recognized as ours.
    st = LabelState()
    stored = label_for("title", "3%")           # babelarr-locked:title:d1e1d77e
    recased = stored[0].upper() + stored[1:]     # Babelarr-locked:title:d1e1d77e
    item = FakeItem(labels=[recased])
    assert st.is_ours(item, "title", "3%")


def test_label_record_does_not_duplicate_recased_label():
    # If Plex already stored a title-cased copy, record() must NOT add a second
    # (lowercase) duplicate for the same value.
    st = LabelState()
    stored = label_for("thumb", "poster-key")
    recased = stored[0].upper() + stored[1:]
    item = FakeItem(labels=[recased])
    st.record(item, "thumb", "poster-key")
    matches = [t for t in item._labels
               if t.lower().startswith("babelarr-locked:thumb:")]
    assert len(matches) == 1  # the pre-existing recased one, no lowercase dup


# --- file backend -----------------------------------------------------------

def test_file_is_ours_roundtrip(tmp_path):
    path = tmp_path / "state.json"
    st = FileState(str(path))
    item = FakeItem(guids=["tmdb://5"])
    assert not st.is_ours(item, "title", "The Film")
    st.record(item, "title", "The Film")
    assert st.is_ours(item, "title", "The Film")
    assert not st.is_ours(item, "title", "User Edit")


def test_file_persists_across_instances(tmp_path):
    path = tmp_path / "state.json"
    item = FakeItem(guids=["tmdb://5"])
    st1 = FileState(str(path))
    st1.record(item, "title", "The Film")
    st1.flush()
    # New instance reads the file back.
    st2 = FileState(str(path))
    assert st2.is_ours(item, "title", "The Film")


def test_file_corrupt_fails_safe(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{ not valid json", encoding="utf-8")
    st = FileState(str(path))  # must not raise
    item = FakeItem(guids=["tmdb://5"])
    # Forgotten state -> nothing is "ours" -> safe back-off.
    assert not st.is_ours(item, "title", "Anything")


def test_file_dry_run_does_not_write(tmp_path):
    path = tmp_path / "state.json"
    st = FileState(str(path), dry_run=True)
    item = FakeItem(guids=["tmdb://5"])
    st.record(item, "title", "T")
    st.flush()
    assert not path.exists()


# --- factory ----------------------------------------------------------------

def test_build_state_factory(tmp_path):
    assert isinstance(build_state("labels", "", False), LabelState)
    assert isinstance(build_state("file", str(tmp_path / "s.json"), False), FileState)
