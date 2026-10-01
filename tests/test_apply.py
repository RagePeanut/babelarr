"""Tests for apply_title / apply_poster: user-lock detection + skip behavior."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from babelarr.plex_client import apply_poster, apply_title  # noqa: E402
from babelarr.state import LabelState  # noqa: E402


class FakeField:
    def __init__(self, name, locked):
        self.name = name
        self.locked = locked


class FakeTag:
    def __init__(self, tag):
        self.tag = tag


class FakePoster:
    def __init__(self, rating_key, selected):
        self.ratingKey = rating_key
        self.selected = selected


class FakeItem:
    def __init__(self, title="Orig", title_locked=False, poster_locked=False,
                 selected_poster=None, labels=None):
        self.title = title
        self.ratingKey = "1"
        self.guids = []
        self.guid = None
        self._fields = []
        if title_locked:
            self._fields.append(FakeField("title", True))
        if poster_locked:
            self._fields.append(FakeField("thumb", True))
        self._selected_poster = selected_poster
        self._labels = list(labels or [])
        self.edit_calls = []
        self.poster_uploads = []
        self.locked_poster = False

    @property
    def fields(self):
        return self._fields

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

    def editTitle(self, title, locked=True):
        self.title = title
        self.edit_calls.append((title, locked))
        if not any(f.name == "title" for f in self._fields):
            self._fields.append(FakeField("title", locked))

    def posters(self):
        return [self._selected_poster] if self._selected_poster else []

    def uploadPoster(self, url=None):
        self.poster_uploads.append(url)
        self._selected_poster = FakePoster("upload://posters/newhash", True)

    def lockPoster(self):
        self.locked_poster = True

    def reload(self):
        pass


# --- titles -----------------------------------------------------------------

def test_title_set_when_unlocked():
    item = FakeItem(title="Orig", title_locked=False)
    st = LabelState()
    changed = apply_title(item, "New", {"title"}, st, dry_run=False)
    assert changed
    assert item.title == "New"
    assert item.edit_calls[-1] == ("New", True)  # locked after set
    assert st.is_ours(item, "title", "New")  # fingerprinted


def test_title_skipped_when_user_locked():
    # Locked, and no Babelarr fingerprint -> user owns it -> skip.
    item = FakeItem(title="User Pick", title_locked=True)
    st = LabelState()
    changed = apply_title(item, "New", {"title"}, st, dry_run=False)
    assert not changed
    assert item.title == "User Pick"  # untouched


def test_title_remanaged_when_ours_even_if_locked():
    # Locked but it's OUR value (fingerprint matches) -> we may update it.
    item = FakeItem(title="OldAuto", title_locked=True)
    st = LabelState()
    st.record(item, "title", "OldAuto")  # pretend we set it before
    changed = apply_title(item, "NewAuto", {"title"}, st, dry_run=False)
    assert changed
    assert item.title == "NewAuto"


def test_title_locked_but_not_protected_overwrites():
    # title NOT in skip set -> overwrite even a user lock.
    item = FakeItem(title="User Pick", title_locked=True)
    st = LabelState()
    changed = apply_title(item, "New", set(), st, dry_run=False)
    assert changed
    assert item.title == "New"


def test_title_dry_run_no_write():
    item = FakeItem(title="Orig", title_locked=False)
    st = LabelState()
    apply_title(item, "New", {"title"}, st, dry_run=True)
    assert item.title == "Orig"
    assert not item.edit_calls


# --- posters ----------------------------------------------------------------

def test_poster_set_when_unlocked():
    item = FakeItem(poster_locked=False)
    st = LabelState()
    changed = apply_poster(item, "http://img/fr.jpg", {"poster"}, st, dry_run=False)
    assert changed
    assert item.poster_uploads == ["http://img/fr.jpg"]
    assert item.locked_poster
    # Fingerprint recorded on the resulting selected poster's ratingKey.
    assert st.is_ours(item, "thumb", "upload://posters/newhash")


def test_poster_skipped_when_user_locked():
    # Locked with a user-selected poster we never recorded -> skip.
    item = FakeItem(poster_locked=True,
                    selected_poster=FakePoster("upload://posters/userhash", True))
    st = LabelState()
    changed = apply_poster(item, "http://img/fr.jpg", {"poster"}, st, dry_run=False)
    assert not changed
    assert item.poster_uploads == []


def test_poster_remanaged_when_ours():
    item = FakeItem(poster_locked=True,
                    selected_poster=FakePoster("upload://posters/mine", True))
    st = LabelState()
    st.record(item, "thumb", "upload://posters/mine")  # our prior poster
    changed = apply_poster(item, "http://img/new.jpg", {"poster"}, st, dry_run=False)
    assert changed
    assert item.poster_uploads == ["http://img/new.jpg"]


def test_poster_user_swap_detected():
    # We recorded 'mine', but the selected poster is now 'userhash' -> user
    # swapped it -> treated as user-owned -> skip.
    item = FakeItem(poster_locked=True,
                    selected_poster=FakePoster("upload://posters/userhash", True))
    st = LabelState()
    st.record(item, "thumb", "upload://posters/mine")
    changed = apply_poster(item, "http://img/new.jpg", {"poster"}, st, dry_run=False)
    assert not changed
