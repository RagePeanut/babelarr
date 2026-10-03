"""Tests for the Plex-layer language glue: label override + content/production.

Exercises ``ov_override_for``, ``content_language_for`` and
``production_language_for`` with fakes (no plexapi / no network).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from babelarr.plex_client import (  # noqa: E402
    content_language_for,
    ov_override_for,
    production_language_for,
)


class FakeTag:
    def __init__(self, tag):
        self.tag = tag


class FakeGuid:
    def __init__(self, id):
        self.id = id


class FakeStream:
    def __init__(self, lang):
        self.languageCode = lang


class FakePart:
    def __init__(self, audio_langs):
        self._audio = [FakeStream(l) for l in audio_langs]

    def audioStreams(self):
        return self._audio


class FakeMedia:
    def __init__(self, audio_langs):
        self.parts = [FakePart(audio_langs)]


class FakeItem:
    def __init__(self, tmdb_id=None, labels=None, audio_langs=()):
        self.guids = [FakeGuid(f"tmdb://{tmdb_id}")] if tmdb_id else []
        self.guid = None
        self._labels = list(labels or [])
        self.media = [FakeMedia(audio_langs)] if audio_langs else []

    @property
    def labels(self):
        return [FakeTag(t) for t in self._labels]


class FakeTMDB:
    """Returns canned language info / original_language per tmdb id."""

    def __init__(self, info):
        # info: {tmdb_id: (production, spoken_tuple)}
        self._info = info

    def language_info(self, tmdb_id, is_movie):
        return self._info.get(tmdb_id, (None, ()))

    def movie_original_language(self, tmdb_id):
        return self._info.get(tmdb_id, (None, ()))[0]

    def tv_original_language(self, tmdb_id):
        return self._info.get(tmdb_id, (None, ()))[0]


# --- label override ---------------------------------------------------------

def test_ov_override_read():
    item = FakeItem(labels=["babelarr-ov:ja", "some-other-label"])
    assert ov_override_for(item) == "ja"


def test_ov_override_absent():
    item = FakeItem(labels=["genre:anime"])
    assert ov_override_for(item) is None


def test_ov_override_wins_over_tmdb():
    # TMDB says production en / spoken en, but label forces Korean.
    tmdb = FakeTMDB({5: ("en", ("en",))})
    item = FakeItem(tmdb_id=5, labels=["babelarr-ov:ko"], audio_langs=["en"])
    assert content_language_for(item, tmdb, is_movie=True) == "kor"


# --- content language (the Uzumaki case) ------------------------------------

def test_content_language_uzumaki():
    # Production en, spoken [ja] -> content Japanese.
    tmdb = FakeTMDB({117676: ("en", ("ja",))})
    item = FakeItem(tmdb_id=117676, audio_langs=["ja", "en"])
    assert content_language_for(item, tmdb, is_movie=False) == "jpn"


def test_content_language_multi_production_absent_uses_first_spoken():
    # Production fr not among spoken [ja, ko] -> first spoken (ja).
    # (No audio-track inspection anymore.)
    tmdb = FakeTMDB({9: ("fr", ("ja", "ko"))})
    item = FakeItem(tmdb_id=9, audio_langs=["ko"])
    assert content_language_for(item, tmdb, is_movie=True) == "jpn"


def test_content_language_silent_film():
    # Only "no language" spoken -> NO_LANGUAGE sentinel.
    from babelarr.langcodes import NO_LANGUAGE
    tmdb = FakeTMDB({3: ("de", ("xx",))})
    item = FakeItem(tmdb_id=3)
    assert content_language_for(item, tmdb, is_movie=True) == NO_LANGUAGE


def test_content_language_no_tmdb_match_is_none():
    tmdb = FakeTMDB({})
    item = FakeItem(tmdb_id=None, audio_langs=["ja"])
    assert content_language_for(item, tmdb, is_movie=True) is None


# --- production language (posters/titles) -----------------------------------

def test_production_language_is_tmdb_original():
    # Uzumaki: production language stays English for posters/titles.
    tmdb = FakeTMDB({117676: ("en", ("ja",))})
    item = FakeItem(tmdb_id=117676, audio_langs=["ja"])
    assert production_language_for(item, tmdb, is_movie=False) == "en"
