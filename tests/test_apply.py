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
    def __init__(self, rating_key, selected=False):
        self.ratingKey = rating_key
        self.key = rating_key
        self.selected = selected
        self.selected_count = 0

    def select(self):
        self.selected_count += 1
        self.selected = True


class FakeItem:
    def __init__(self, title="Orig", title_locked=False, poster_locked=False,
                 candidates=None, labels=None):
        self.title = title
        self.ratingKey = "1"
        self.guids = []
        self.guid = None
        self._fields = []
        if title_locked:
            self._fields.append(FakeField("title", True))
        if poster_locked:
            self._fields.append(FakeField("thumb", True))
        self._candidates = list(candidates or [])  # existing poster candidates
        self._labels = list(labels or [])
        self.edit_calls = []
        self.poster_uploads = []
        self.poster_selects = []
        self.locked_poster = False

    def posters(self):
        return self._candidates

    def _select_only(self, poster):
        for c in self._candidates:
            c.selected = (c is poster)

    def setPoster(self, poster):
        self.poster_selects.append(poster.ratingKey)
        self._select_only(poster)

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

    def uploadPoster(self, url=None):
        self.poster_uploads.append(url)
        # Uploading creates a new upload:// poster and selects it.
        up = FakePoster(f"upload://posters/{len(self.poster_uploads)}")
        self._candidates.append(up)
        self._select_only(up)

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


# --- posters (recognition by the actually-selected poster key) --------------

URL = "https://image.tmdb.org/t/p/original/abc.jpg"
URL2 = "https://image.tmdb.org/t/p/original/def.jpg"


def test_poster_selects_existing_candidate_instead_of_uploading():
    # Wanted URL is already a candidate -> select it, don't upload.
    item = FakeItem(poster_locked=False, candidates=[FakePoster(URL), FakePoster(URL2)])
    st = LabelState()
    changed = apply_poster(item, URL, {"poster"}, st, dry_run=False)
    assert changed
    assert item.poster_uploads == []
    assert item.poster_selects == [URL]
    assert item.locked_poster
    # Recorded the resulting selected key (the URL) -> recognizable next run.
    assert st.is_ours(item, "thumb", URL)


def test_poster_uploads_when_url_not_a_candidate():
    item = FakeItem(poster_locked=False, candidates=[FakePoster(URL2)])
    st = LabelState()
    changed = apply_poster(item, URL, {"poster"}, st, dry_run=False)
    assert changed
    assert item.poster_uploads == [URL]
    assert item.poster_selects == []


def _record_poster(st, item, selected_key, url):
    """Seed prior state: resulting selected key + intended url."""
    st.record(item, "thumb", selected_key)
    st.record(item, "thumb_url", url)


def test_poster_not_reselected_when_already_ours():
    # Our poster already selected + already the wanted one -> skip (no thrash).
    sel = FakePoster(URL, selected=True)
    item = FakeItem(poster_locked=True, candidates=[sel, FakePoster(URL2)])
    st = LabelState()
    _record_poster(st, item, URL, URL)     # selected key = URL, wanted = URL
    changed = apply_poster(item, URL, {"poster"}, st, dry_run=False)
    assert not changed
    assert item.poster_selects == []
    assert item.poster_uploads == []


def test_uploaded_poster_not_reuploaded_when_already_ours():
    # The upload-fallback case: our poster is an upload:// (key != URL), already
    # ours and corresponds to the wanted URL -> must NOT re-upload every sweep.
    sel = FakePoster("upload://posters/mine", selected=True)
    item = FakeItem(poster_locked=True, candidates=[sel])  # URL not a candidate
    st = LabelState()
    _record_poster(st, item, "upload://posters/mine", URL)
    changed = apply_poster(item, URL, {"poster"}, st, dry_run=False)
    assert not changed
    assert item.poster_uploads == []       # <- the bug this guards against
    assert item.poster_selects == []


def test_poster_rule_change_reapplied_even_when_protected():
    # The selected poster IS ours, but the rule now wants a DIFFERENT url
    # (wanted=False) -> re-apply, even with poster protected.
    sel = FakePoster(URL, selected=True)
    item = FakeItem(poster_locked=True, candidates=[sel, FakePoster(URL2)])
    st = LabelState()
    _record_poster(st, item, URL, URL)     # ours; wanted url was URL
    changed = apply_poster(item, URL2, {"poster"}, st, dry_run=False)
    assert changed
    assert item.poster_selects == [URL2]   # switched to the new rule's poster


def test_poster_user_swap_preserved():
    # User swapped to a poster we never recorded -> selected key != recorded ->
    # not ours -> protected (skip).
    swapped = FakePoster("upload://posters/userpick", selected=True)
    item = FakeItem(poster_locked=True, candidates=[swapped, FakePoster(URL)])
    st = LabelState()
    _record_poster(st, item, URL, URL)     # we last set URL; user then swapped
    changed = apply_poster(item, URL, {"poster"}, st, dry_run=False)
    assert not changed
    assert item.poster_selects == []
    assert item.poster_uploads == []


def test_poster_user_swap_overwritten_when_not_protected():
    swapped = FakePoster("upload://posters/userpick", selected=True)
    item = FakeItem(poster_locked=True, candidates=[swapped, FakePoster(URL)])
    st = LabelState()
    _record_poster(st, item, URL, URL)
    changed = apply_poster(item, URL, set(), st, dry_run=False)
    assert changed
    assert item.poster_selects == [URL]    # re-selected our wanted poster
