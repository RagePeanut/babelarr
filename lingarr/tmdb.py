"""Minimal TMDB client for resolving a title's original language.

Plex items carry TMDB (and/or IMDB/TVDB) guids we can use to look up the
authoritative ``original_language`` field. Results are cached in-process to
avoid hammering TMDB during full sweeps.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Optional

import requests

log = logging.getLogger("lingarr.tmdb")

_BASE = "https://api.themoviedb.org/3"


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

    @lru_cache(maxsize=4096)
    def movie_original_language(self, tmdb_id: int) -> Optional[str]:
        data = self._get(f"/movie/{tmdb_id}")
        return data.get("original_language") if data else None

    @lru_cache(maxsize=4096)
    def tv_original_language(self, tmdb_id: int) -> Optional[str]:
        data = self._get(f"/tv/{tmdb_id}")
        return data.get("original_language") if data else None

    @lru_cache(maxsize=4096)
    def find_by_external_id(self, external_id: str, source: str) -> Optional[dict]:
        """Resolve an IMDB/TVDB id to TMDB results via the /find endpoint.

        ``source`` is e.g. ``imdb_id`` or ``tvdb_id``.
        """
        return self._get(f"/find/{external_id}", external_source=source)
