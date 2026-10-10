"""Tests for apply_logo / apply_backdrop (babelarr.plex_client).

These concerns reuse the poster apply machinery via the generic
``_apply_image`` path, plus a logo-only ``off`` path that CLEARS the image
(``deleteLogo`` + lock) so Plex falls back to the text title. The fake item
below exposes the real plexapi method names for logos (clearLogo) and backdrops
(art), so a wrong-name regression raises AttributeError.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from babelarr.image_selector import SELECT_OFF  # noqa: E402
from babelarr.plex_client import (  # noqa: E402
    PlexFeatureUnsupported,
    apply_backdrop,
    apply_logo,
)
from babelarr.state import LabelState  # noqa: E402


class FakeField:
    def __init__(self, name, locked):
        self.name = name
        self.locked = locked


class FakeTag:
    def __init__(self, tag):
        self.tag = tag


class FakeImage:
    def __init__(self, rating_key, selected=False):
        self.ratingKey = rating_key
        self.key = rating_key
        self.selected = selected


class FakeImageItem:
    """A movie exposing both logo (clearLogo) and art (backdrop) image APIs."""

    def __init__(self, logo_locked=False, art_locked=False,
                 logo_candidates=None, art_candidates=None, labels=None):
        self.title = "Movie"
        self.ratingKey = "1"
        self.guids = []
        self.guid = None
        # Stand-in for the PlexServer handle; _server_version reads .version.
        self._server = type("FakeServer", (), {"version": "1.40.0.1000"})()
        self._fields = []
        if logo_locked:
            self._fields.append(FakeField("clearLogo", True))
        if art_locked:
            self._fields.append(FakeField("art", True))
        self._logos = list(logo_candidates or [])
        self._arts = list(art_candidates or [])
        self._labels = list(labels or [])
        # call records
        self.logo_uploads, self.logo_selects = [], []
        self.art_uploads, self.art_selects = [], []
        self.locked_logo = self.locked_art = False
        self.logo_deleted = self.art_deleted = False

    # -- lock fields --
    @property
    def fields(self):
        return self._fields

    # -- labels (LabelState backend) --
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

    # -- logos (clearLogo) --
    def logos(self):
        return self._logos

    def _select_only(self, pool, chosen):
        for c in pool:
            c.selected = (c is chosen)

    def setLogo(self, logo):
        self.logo_selects.append(logo.ratingKey)
        self._select_only(self._logos, logo)

    def uploadLogo(self, url=None):
        self.logo_uploads.append(url)
        up = FakeImage(f"upload://logos/{len(self.logo_uploads)}")
        self._logos.append(up)
        self._select_only(self._logos, up)

    def lockLogo(self):
        self.locked_logo = True
        if not any(f.name == "clearLogo" for f in self._fields):
            self._fields.append(FakeField("clearLogo", True))

    def deleteLogo(self):
        self.logo_deleted = True
        for c in self._logos:
            c.selected = False

    # -- backdrops (art) --
    def arts(self):
        return self._arts

    def setArt(self, art):
        self.art_selects.append(art.ratingKey)
        self._select_only(self._arts, art)

    def uploadArt(self, url=None):
        self.art_uploads.append(url)
        up = FakeImage(f"upload://arts/{len(self.art_uploads)}")
        self._arts.append(up)
        self._select_only(self._arts, up)

    def lockArt(self):
        self.locked_art = True
        if not any(f.name == "art" for f in self._fields):
            self._fields.append(FakeField("art", True))

    def deleteArt(self):
        self.art_deleted = True
        for c in self._arts:
            c.selected = False

    def reload(self):
        pass


LOGO_URL = "https://image.tmdb.org/t/p/original/logo_en.png"
LOGO_URL2 = "https://image.tmdb.org/t/p/original/logo_fr.png"
ART_URL = "https://image.tmdb.org/t/p/original/backdrop.png"
ART_URL2 = "https://image.tmdb.org/t/p/original/backdrop2.png"


def _has_label(item, field):
    pre = f"babelarr-locked:{field}:".lower()
    return any(t.lower().startswith(pre) for t in item._labels)


# --- logo: select / upload / lock ------------------------------------------

def test_logo_selects_existing_candidate():
    item = FakeImageItem(logo_candidates=[FakeImage(LOGO_URL), FakeImage(LOGO_URL2)])
    st = LabelState()
    changed = apply_logo(item, LOGO_URL, {"logo"}, st, dry_run=False)
    assert changed
    assert item.logo_selects == [LOGO_URL]
    assert item.logo_uploads == []
    assert item.locked_logo
    assert st.is_ours(item, "clearLogo", LOGO_URL)


def test_logo_uploads_when_not_a_candidate():
    item = FakeImageItem(logo_candidates=[FakeImage(LOGO_URL2)])
    st = LabelState()
    changed = apply_logo(item, LOGO_URL, {"logo"}, st, dry_run=False)
    assert changed
    assert item.logo_uploads == [LOGO_URL]
    assert _has_label(item, "clearLogo")
    assert _has_label(item, "clearLogo-url")  # upload records source URL


def test_logo_user_lock_protected():
    # Locked, not ours -> user owns the logo -> skip when protected.
    swapped = FakeImage("upload://logos/userpick", selected=True)
    item = FakeImageItem(logo_locked=True, logo_candidates=[swapped,
                                                            FakeImage(LOGO_URL)])
    st = LabelState()
    st.record(item, "clearLogo", LOGO_URL)   # we last set LOGO_URL; user swapped
    changed = apply_logo(item, LOGO_URL, {"logo"}, st, dry_run=False)
    assert not changed
    assert item.logo_selects == [] and item.logo_uploads == []


def test_logo_rule_change_reapplied_even_when_protected():
    sel = FakeImage(LOGO_URL, selected=True)
    item = FakeImageItem(logo_locked=True, logo_candidates=[sel,
                                                           FakeImage(LOGO_URL2)])
    st = LabelState()
    st.record(item, "clearLogo", LOGO_URL)
    st.record(item, "clearLogo-url", LOGO_URL)
    changed = apply_logo(item, LOGO_URL2, {"logo"}, st, dry_run=False)
    assert changed
    assert item.logo_selects == [LOGO_URL2]


def test_logo_dry_run_no_write():
    item = FakeImageItem(logo_candidates=[FakeImage(LOGO_URL)])
    st = LabelState()
    apply_logo(item, LOGO_URL, {"logo"}, st, dry_run=True)
    assert item.logo_selects == [] and item.logo_uploads == []
    assert not item.locked_logo


# --- logo: `off` -> clear ---------------------------------------------------

def test_logo_off_clears_and_locks():
    sel = FakeImage(LOGO_URL, selected=True)
    item = FakeImageItem(logo_candidates=[sel])
    st = LabelState()
    changed = apply_logo(item, SELECT_OFF, {"logo"}, st, dry_run=False)
    assert changed
    assert item.logo_deleted          # cleared
    assert item.locked_logo           # locked so the agent won't re-pick
    # Fingerprinted the cleared state so we don't re-clear every sweep.
    assert st.is_ours(item, "clearLogo", "babelarr:off")


def test_logo_off_idempotent_when_already_cleared():
    item = FakeImageItem(logo_candidates=[])
    st = LabelState()
    st.record(item, "clearLogo", "babelarr:off")  # we already cleared it
    changed = apply_logo(item, SELECT_OFF, {"logo"}, st, dry_run=False)
    assert not changed
    assert not item.logo_deleted


def test_logo_off_dry_run_no_write():
    sel = FakeImage(LOGO_URL, selected=True)
    item = FakeImageItem(logo_candidates=[sel])
    st = LabelState()
    apply_logo(item, SELECT_OFF, {"logo"}, st, dry_run=True)
    assert not item.logo_deleted and not item.locked_logo


# --- backdrop (art) ---------------------------------------------------------

def test_backdrop_selects_existing_candidate():
    item = FakeImageItem(art_candidates=[FakeImage(ART_URL), FakeImage(ART_URL2)])
    st = LabelState()
    changed = apply_backdrop(item, ART_URL, {"backdrop"}, st, dry_run=False)
    assert changed
    assert item.art_selects == [ART_URL]
    assert item.art_uploads == []
    assert item.locked_art
    assert st.is_ours(item, "art", ART_URL)


def test_backdrop_uploads_when_not_a_candidate():
    item = FakeImageItem(art_candidates=[FakeImage(ART_URL2)])
    st = LabelState()
    changed = apply_backdrop(item, ART_URL, {"backdrop"}, st, dry_run=False)
    assert changed
    assert item.art_uploads == [ART_URL]
    assert _has_label(item, "art")
    assert _has_label(item, "art-url")


def test_backdrop_user_lock_protected():
    swapped = FakeImage("upload://arts/userpick", selected=True)
    item = FakeImageItem(art_locked=True, art_candidates=[swapped,
                                                         FakeImage(ART_URL)])
    st = LabelState()
    st.record(item, "art", ART_URL)
    changed = apply_backdrop(item, ART_URL, {"backdrop"}, st, dry_run=False)
    assert not changed
    assert item.art_selects == [] and item.art_uploads == []


def test_backdrop_overwrites_user_lock_when_not_protected():
    swapped = FakeImage("upload://arts/userpick", selected=True)
    item = FakeImageItem(art_locked=True, art_candidates=[swapped,
                                                         FakeImage(ART_URL)])
    st = LabelState()
    st.record(item, "art", ART_URL)
    changed = apply_backdrop(item, ART_URL, set(), st, dry_run=False)
    assert changed
    assert item.art_selects == [ART_URL]


def test_logo_and_backdrop_are_independent():
    # Setting a logo must not touch art and vice versa.
    item = FakeImageItem(logo_candidates=[FakeImage(LOGO_URL)],
                         art_candidates=[FakeImage(ART_URL)])
    st = LabelState()
    apply_logo(item, LOGO_URL, {"logo", "backdrop"}, st, dry_run=False)
    assert item.art_selects == [] and item.art_uploads == []
    apply_backdrop(item, ART_URL, {"logo", "backdrop"}, st, dry_run=False)
    assert item.logo_selects == [LOGO_URL]  # unchanged from the first call


# --- safety net: an old Plex server that doesn't support the operation ------
# Newer artwork ops (e.g. locking clearLogo) 400 on older servers, surfacing as
# plexapi BadRequest. Babelarr must translate that into a clear, actionable
# PlexFeatureUnsupported (naming the detected server version) rather than let a
# raw traceback repeat every sweep. A non-400 error must still propagate as-is.

class FakeBadRequest(Exception):
    """Stand-in for plexapi.exceptions.BadRequest (matched by class name)."""


class OldServerLogoItem(FakeImageItem):
    """lockLogo() 400s, like a Plex build predating clearLogo lock support."""

    def lockLogo(self):
        raise FakeBadRequest("(400) bad_request: clearLogo.locked=1")


def test_old_server_logo_lock_raises_feature_unsupported_with_version():
    item = OldServerLogoItem(logo_candidates=[])  # not a candidate -> upload+lock
    st = LabelState()
    with pytest.raises(PlexFeatureUnsupported) as ei:
        apply_logo(item, LOGO_URL, {"logo"}, st, dry_run=False)
    msg = str(ei.value)
    assert "1.40.0.1000" in msg          # detected server version is reported
    assert "too old" in msg.lower()
    assert "logo" in msg.lower()


def test_old_server_logo_off_raises_feature_unsupported():
    sel = FakeImage(LOGO_URL, selected=True)
    item = OldServerLogoItem(logo_candidates=[sel])
    st = LabelState()
    with pytest.raises(PlexFeatureUnsupported):
        apply_logo(item, SELECT_OFF, {"logo"}, st, dry_run=False)


class ServerErrorLogoItem(FakeImageItem):
    """lockLogo() fails with a NON-400 error (e.g. a transient server fault)."""

    def lockLogo(self):
        raise RuntimeError("500 internal server error")


def test_non_400_lock_error_propagates_unchanged():
    item = ServerErrorLogoItem(logo_candidates=[])
    st = LabelState()
    # Not a 'too old' signal -> must NOT be swallowed/reclassified.
    with pytest.raises(RuntimeError):
        apply_logo(item, LOGO_URL, {"logo"}, st, dry_run=False)
