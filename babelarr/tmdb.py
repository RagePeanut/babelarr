"""Minimal TMDB client for original language, posters, and localized titles.

Plex items carry TMDB (and/or IMDB/TVDB) guids we use to look up authoritative
metadata: the ``original_language`` field, language-tagged poster images, and
localized titles (translations). Results are cached in-process to avoid
hammering TMDB during full sweeps.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Dict, List, Optional

import requests

from .image_selector import ImageView
from .langcodes import normalize
from .poster_selector import PosterView
from .title_selector import TitleOptions

log = logging.getLogger("babelarr.tmdb")

_BASE = "https://api.themoviedb.org/3"
_IMG_BASE = "https://image.tmdb.org/t/p/original"


class TMDBClient:
    def __init__(self, api_key: str, session: Optional[requests.Session] = None):
        self._api_key = api_key
        self._session = session or requests.Session()

    def _get(self, path: str, **params) -> Optional[dict]:
        params["api_key"] = self._api_key
        try:
            resp = self._session.get(f"{_BASE}{path}", params=params, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            log.warning("TMDB request failed for %s: %s", path, exc)
            return None

    # -- language metadata ---------------------------------------------------
    #
    # TMDB distinguishes two notions that Babelarr keeps separate:
    #   * original_language -- the PRODUCTION language (tied to origin country),
    #     NOT necessarily the language the content is spoken in. Used for
    #     posters/titles ("the original poster/title").
    #   * spoken_languages  -- the languages actually spoken in the content.
    #     Used (with the resolver in plex_client) to drive audio/subtitles.

    @lru_cache(maxsize=4096)
    def movie_original_language(self, tmdb_id: int) -> Optional[str]:
        data = self._get(f"/movie/{tmdb_id}")
        return data.get("original_language") if data else None

    @lru_cache(maxsize=4096)
    def tv_original_language(self, tmdb_id: int) -> Optional[str]:
        data = self._get(f"/tv/{tmdb_id}")
        return data.get("original_language") if data else None

    @lru_cache(maxsize=4096)
    def language_info(self, tmdb_id: int, is_movie: bool) -> tuple:
        """Return ``(production_language, spoken_languages)`` for a title.

        ``production_language`` is TMDB's ``original_language`` (may be None).
        ``spoken_languages`` is an ordered tuple of ISO 639-1 codes from TMDB's
        ``spoken_languages`` list (possibly empty).
        """
        kind = "movie" if is_movie else "tv"
        data = self._get(f"/{kind}/{tmdb_id}") or {}
        production = data.get("original_language")
        spoken = tuple(
            s.get("iso_639_1")
            for s in (data.get("spoken_languages") or [])
            if s.get("iso_639_1")
        )
        return production, spoken

    @lru_cache(maxsize=4096)
    def find_by_external_id(self, external_id: str, source: str) -> Optional[dict]:
        """Resolve an IMDB/TVDB id to TMDB results via the /find endpoint."""
        return self._get(f"/find/{external_id}", external_source=source)

    # -- posters -------------------------------------------------------------

    @lru_cache(maxsize=2048)
    def posters(self, tmdb_id: int, is_movie: bool) -> tuple:
        """Return a tuple of PosterView for a title (all languages + textless).

        ``include_image_language=null`` ensures the "no language" (textless)
        posters are returned alongside language-tagged ones. The absolute image
        URL is used as each poster's opaque ``key`` so the Plex layer can upload
        it directly.
        """
        kind = "movie" if is_movie else "tv"
        data = self._get(
            f"/{kind}/{tmdb_id}/images", include_image_language="null,en"
        )
        # Without a language filter TMDB returns only the default-language set;
        # re-query without the filter to get every language's posters.
        data_all = self._get(f"/{kind}/{tmdb_id}/images", include_image_language="")
        merged: Dict[str, dict] = {}
        for d in (data, data_all):
            if not d:
                continue
            for p in d.get("posters", []) or []:
                fp = p.get("file_path")
                if fp:
                    merged[fp] = p
        views: List[PosterView] = []
        for fp, p in merged.items():
            views.append(
                PosterView(
                    key=f"{_IMG_BASE}{fp}",
                    language_code=p.get("iso_639_1"),  # None/"" -> textless
                    vote_average=float(p.get("vote_average", 0.0) or 0.0),
                )
            )
        return tuple(views)

    # -- logos & backdrops ---------------------------------------------------
    #
    # TMDB's single /images endpoint returns ``posters``, ``logos`` and
    # ``backdrops`` arrays, all sharing the same shape (``file_path``,
    # ``iso_639_1``, ``vote_average``). Logos back Plex's "clearLogo" (the title
    # artwork overlaid on the backdrop); backdrops back Plex's "art". Both are
    # chosen with the same language-keyed logic as posters, so we expose them as
    # ``ImageView`` tuples for the shared image selector.

    def _images_of(self, tmdb_id: int, is_movie: bool, category: str) -> tuple:
        """Return a tuple of ImageView for one image ``category``.

        ``category`` is a key in TMDB's /images response (``logos`` or
        ``backdrops``). As with posters we merge an English+null-filtered query
        with an unfiltered one so every language's images -- and the "no
        language" (textless) ones -- are present. The absolute image URL is the
        opaque ``key`` the Plex layer applies.
        """
        kind = "movie" if is_movie else "tv"
        data = self._get(
            f"/{kind}/{tmdb_id}/images", include_image_language="null,en"
        )
        data_all = self._get(f"/{kind}/{tmdb_id}/images", include_image_language="")
        merged: Dict[str, dict] = {}
        for d in (data, data_all):
            if not d:
                continue
            for img in d.get(category, []) or []:
                fp = img.get("file_path")
                if fp:
                    merged[fp] = img
        views: List[ImageView] = []
        for fp, img in merged.items():
            views.append(
                ImageView(
                    key=f"{_IMG_BASE}{fp}",
                    language_code=img.get("iso_639_1"),  # None/"" -> textless
                    vote_average=float(img.get("vote_average", 0.0) or 0.0),
                )
            )
        return tuple(views)

    @lru_cache(maxsize=2048)
    def logos(self, tmdb_id: int, is_movie: bool) -> tuple:
        """Return a tuple of ImageView for a title's logos (all languages)."""
        return self._images_of(tmdb_id, is_movie, "logos")

    @lru_cache(maxsize=2048)
    def backdrops(self, tmdb_id: int, is_movie: bool) -> tuple:
        """Return a tuple of ImageView for a title's backdrops (all languages)."""
        return self._images_of(tmdb_id, is_movie, "backdrops")

    # -- titles --------------------------------------------------------------

    @lru_cache(maxsize=2048)
    def title_options(self, tmdb_id: int, is_movie: bool) -> TitleOptions:
        """Return localized titles + original title for a TMDB title."""
        kind = "movie" if is_movie else "tv"
        detail = self._get(f"/{kind}/{tmdb_id}") or {}
        original_title = (
            detail.get("original_title")
            if is_movie
            else detail.get("original_name")
        )
        original_language = detail.get("original_language")

        by_language: Dict[str, str] = {}
        trans = self._get(f"/{kind}/{tmdb_id}/translations") or {}
        for t in trans.get("translations", []) or []:
            lang = normalize(t.get("iso_639_1"))
            if lang is None:
                continue
            data = t.get("data", {}) or {}
            title = data.get("title") if is_movie else data.get("name")
            if title and lang not in by_language:
                by_language[lang] = title

        return TitleOptions(
            by_language=by_language,
            original_title=original_title,
            original_language=original_language,
        )
