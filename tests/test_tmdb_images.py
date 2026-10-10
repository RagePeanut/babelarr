"""Tests for TMDBClient.logos / backdrops parsing into ImageView tuples.

The two /images queries (filtered + unfiltered) are merged by file_path; each
image's iso_639_1 becomes the ImageView.language_code (None/"" -> textless) and
the absolute image URL becomes the opaque key.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from babelarr.tmdb import TMDBClient, _IMG_BASE  # noqa: E402


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeSession:
    """Returns the same /images payload regardless of query params."""

    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        return FakeResponse(self._payload)


IMAGES_PAYLOAD = {
    "logos": [
        {"file_path": "/en_logo.png", "iso_639_1": "en", "vote_average": 5.0},
        {"file_path": "/fr_logo.png", "iso_639_1": "fr", "vote_average": 7.0},
        {"file_path": "/no_logo.png", "iso_639_1": None, "vote_average": 2.0},
    ],
    "backdrops": [
        {"file_path": "/bd_none.png", "iso_639_1": None, "vote_average": 8.0},
        {"file_path": "/bd_en.png", "iso_639_1": "en", "vote_average": 4.0},
    ],
}


def _client():
    return TMDBClient(api_key="x", session=FakeSession(IMAGES_PAYLOAD))


def test_logos_parsed_into_imageviews():
    client = _client()
    logos = client.logos(123, is_movie=True)
    by_key = {v.key: v for v in logos}
    assert f"{_IMG_BASE}/en_logo.png" in by_key
    assert by_key[f"{_IMG_BASE}/fr_logo.png"].language_code == "fr"
    assert by_key[f"{_IMG_BASE}/fr_logo.png"].vote_average == 7.0
    # textless logo preserved with language_code None.
    assert by_key[f"{_IMG_BASE}/no_logo.png"].language_code is None


def test_backdrops_parsed_into_imageviews():
    client = _client()
    backdrops = client.backdrops(456, is_movie=True)
    by_key = {v.key: v for v in backdrops}
    assert by_key[f"{_IMG_BASE}/bd_none.png"].language_code is None
    assert by_key[f"{_IMG_BASE}/bd_en.png"].language_code == "en"
    assert len(backdrops) == 2


def test_images_queries_hit_images_endpoint():
    client = _client()
    client.logos(789, is_movie=False)  # tv
    # Hits the /tv/789/images endpoint (filtered + unfiltered variants).
    assert any("/tv/789/images" in url for url, _ in client._session.calls)
